"""Credential isolation, retry safety, and data-preserving relation lifecycle."""

import json
from pathlib import Path

import ops
import pytest
import yaml
from ops.testing import Context, Network, PeerRelation, Relation, Secret, State

from api import APIError
from charm import ROOT_LABEL, OpenVikingCharm, account_for


@pytest.fixture
def setup(mocker):
    workload = mocker.patch("charm.OpenVikingWorkload").return_value
    workload.installed.return_value = True
    workload.healthy.return_value = True
    api = mocker.patch("charm.OpenVikingAPI").return_value
    api.ensure_client.return_value = "client-key-original-123456"
    api.rotate.return_value = "client-key-rotated-123456"
    spec = yaml.safe_load(Path("charmcraft.yaml").read_text())
    ctx = Context(
        OpenVikingCharm,
        meta={k: spec[k] for k in ("name", "provides", "peers")},
        config=spec["config"],
        actions=spec["actions"],
    )
    return ctx, workload, api


def initial(*relations, **kwargs):
    model = Secret(tracked_content={"api-key": "sk-openrouter-test"})
    root = Secret(tracked_content={"api-key": "root-test-key"}, owner="app", label=ROOT_LABEL)
    return State(
        leader=True,
        config={"model-secret": model.id},
        secrets={model, root},
        relations={PeerRelation("peers"), *relations},
        networks={Network("context", ingress_addresses=("10.10.10.2",))},
        **kwargs,
    )


def consumer(identity="agent-one"):
    return Relation("context", remote_app_data={"schema-version": "1", "context-id": identity})


def test_client_credential_is_separate_and_not_in_relation_data(setup):
    ctx, workload, api = setup
    relation = consumer()
    result = ctx.run(ctx.on.relation_changed(relation), initial(relation))
    assert isinstance(result.unit_status, ops.ActiveStatus)
    data = result.get_relation(relation.id).local_app_data
    client = next(s for s in result.secrets if s.id == data["credentials"])
    assert client.tracked_content == {"api-key": api.ensure_client.return_value}
    assert relation.id in client.remote_grants
    assert data["account-id"] == account_for("agent-one")
    assert data["endpoint"] == "http://10.10.10.2:1933"
    assert "client-key" not in str(data)
    assert client.tracked_content["api-key"] != workload.configure.call_args.kwargs["root_key"]


def test_rotation_updates_secret_and_retains_identity(setup):
    ctx, _, api = setup
    relation = consumer()
    state = ctx.run(ctx.on.relation_changed(relation), initial(relation))
    secret_id = state.get_relation(relation.id).local_app_data["credentials"]
    result = ctx.run(ctx.on.action("rotate-client-key", {"relation-id": relation.id}), state)
    assert result.get_secret(id=secret_id).latest_content == {"api-key": api.rotate.return_value}
    api.rotate.assert_called_once_with(account_for("agent-one"))
    assert ctx.action_results["context-id"] == "agent-one"
    assert "client-key" not in str(ctx.action_results)


def test_owner_checks_latest_key_after_rotation_and_does_not_republish_it(setup, mocker):
    ctx, _, api = setup
    relation = consumer()
    state = ctx.run(ctx.on.relation_changed(relation), initial(relation))
    state = ctx.run(ctx.on.action("rotate-client-key", {"relation-id": relation.id}), state)
    ctx.run(ctx.on.action("check-context"), state)
    api.check_client.assert_called_once_with(api.rotate.return_value)
    api.ensure_client.return_value = api.rotate.return_value
    update = mocker.spy(ops.Secret, "set_content")
    result = ctx.run(ctx.on.update_status(), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    update.assert_not_called()


def test_disconnect_rotates_without_deleting_and_reconnect_reuses_account(setup):
    ctx, _, api = setup
    relation = consumer()
    state = ctx.run(ctx.on.relation_changed(relation), initial(relation))
    state = ctx.run(ctx.on.relation_broken(state.get_relation(relation.id)), state)
    api.rotate.assert_called_once_with(account_for("agent-one"))
    peer = next(r for r in state.relations if r.endpoint == "peers")
    assert json.loads(peer.local_app_data["clients"]) == {}
    replacement = consumer()
    api.ensure_client.return_value = api.rotate.return_value
    state = State(
        leader=True,
        config=state.config,
        secrets=state.secrets,
        relations={peer, replacement},
        networks=state.networks,
    )
    result = ctx.run(ctx.on.relation_changed(replacement), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert result.get_relation(replacement.id).local_app_data["account-id"] == account_for(
        "agent-one"
    )


def test_failed_revocation_is_retried_after_relation_disappears(setup):
    ctx, _, api = setup
    relation = consumer()
    state = ctx.run(ctx.on.relation_changed(relation), initial(relation))
    api.rotate.side_effect = APIError(503)
    state = ctx.run(ctx.on.relation_broken(state.get_relation(relation.id)), state)
    assert isinstance(state.unit_status, ops.WaitingStatus)
    peer = next(r for r in state.relations if r.endpoint == "peers")
    assert str(relation.id) in json.loads(peer.local_app_data["clients"])
    api.rotate.side_effect = None
    result = ctx.run(
        ctx.on.update_status(),
        State(
            leader=True,
            config=state.config,
            secrets=state.secrets,
            relations={peer},
        ),
    )
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert json.loads(result.get_relation(peer.id).local_app_data["clients"]) == {}


def test_bad_consumer_does_not_stop_server_for_good_consumer(setup):
    ctx, workload, api = setup
    bad = consumer("../../invalid")
    good = consumer("good")
    result = ctx.run(ctx.on.update_status(), initial(bad, good))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.stop.assert_not_called()
    api.ensure_client.assert_called_once_with(account_for("good"))


def test_duplicate_identity_does_not_share_credentials(setup):
    ctx, workload, api = setup
    one, two = consumer(), consumer()
    result = ctx.run(ctx.on.update_status(), initial(one, two))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    assert api.ensure_client.call_count == 1
    assert sum(bool(result.get_relation(r.id).local_app_data) for r in (one, two)) == 1
    workload.stop.assert_not_called()


def test_nonleader_also_stops_when_scaled(setup):
    ctx, workload, _ = setup
    result = ctx.run(ctx.on.update_status(), State(leader=False, planned_units=2))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.stop.assert_called_once()


def test_no_model_key_stops_and_blocks(setup):
    ctx, workload, _ = setup
    result = ctx.run(ctx.on.config_changed(), State(leader=True))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.stop.assert_called_once()


def test_initial_install_waits_for_peer_storage_without_start_stop_cycle(setup):
    ctx, workload, _ = setup
    state = initial()
    result = ctx.run(
        ctx.on.install(),
        State(
            leader=True,
            config=state.config,
            secrets=state.secrets,
        ),
    )
    assert result.unit_status == ops.WaitingStatus("Waiting for peer relation")
    workload.configure.assert_not_called()
    workload.stop.assert_not_called()


def test_server_not_ready_does_not_publish_credentials(setup):
    ctx, workload, api = setup
    workload.healthy.return_value = False
    relation = consumer()
    result = ctx.run(ctx.on.update_status(), initial(relation))
    assert isinstance(result.unit_status, ops.WaitingStatus)
    assert not result.get_relation(relation.id).local_app_data
    api.ensure_client.assert_not_called()

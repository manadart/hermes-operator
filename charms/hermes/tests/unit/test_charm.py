"""Exercise the charm's configuration and credential lifecycle through Ops events."""

import subprocess
from pathlib import Path

import ops
import pytest
import yaml
from ops.testing import ActionFailed, Context, Relation, Secret, State

from charm import API_SECRET_LABEL, HermesCharm
from workload import VERSION


@pytest.fixture
def workload(mocker):
    workload = mocker.patch("charm.HermesWorkload").return_value
    workload.installed.return_value = True
    workload.healthy.return_value = True
    return workload


@pytest.fixture
def ctx(workload):
    spec = yaml.safe_load(Path("charmcraft.yaml").read_text())
    return Context(
        HermesCharm,
        meta={"name": spec["name"], "peers": spec["peers"], "requires": spec["requires"]},
        config=spec["config"],
        actions=spec["actions"],
    )


def configured_state(**kwargs):
    provider = Secret(tracked_content={"api-key": "sk-or-v1-test-key"})
    return State(
        config={"openrouter-secret": provider.id}, secrets={provider}, leader=True, **kwargs
    )


def test_install_without_credentials_installs_then_blocks(ctx, workload):
    workload.installed.return_value = False
    result = ctx.run(ctx.on.install(), State(leader=True))
    workload.install.assert_called_once()
    workload.configure.assert_not_called()
    workload.stop.assert_called_once()
    assert result.workload_version == VERSION
    assert result.unit_status == ops.BlockedStatus(
        "Set openrouter-secret to a Juju secret containing api-key"
    )


def context_state(*, latest_key=None):
    provider = Secret(tracked_content={"api-key": "sk-or-v1-test-key"})
    client = Secret(
        tracked_content={"api-key": "old-context-token-123456"},
        latest_content={"api-key": latest_key or "old-context-token-123456"},
    )
    relation = Relation(
        "context-store",
        remote_app_data={
            "schema-version": "1",
            "context-id": "review-agent",
            "endpoint": "http://10.10.10.2:1933",
            "credentials": client.id,
        },
    )
    return (
        relation,
        client,
        State(
            leader=True,
            config={"openrouter-secret": provider.id, "context-id": "review-agent"},
            secrets={provider, client},
            relations={relation},
        ),
    )


def test_context_relation_selects_provider_and_stable_identity(ctx, workload, mocker):
    mocker.patch("context.ContextConnection.available", return_value=True)
    relation, _, state = context_state()
    result = ctx.run(ctx.on.relation_changed(relation), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    connection = workload.configure.call_args.kwargs["context"]
    assert connection.identity == "review-agent"
    assert connection.endpoint == "http://10.10.10.2:1933"
    assert result.get_relation(relation.id).local_app_data["context-id"] == "review-agent"
    assert connection.api_key not in repr(connection)


def test_context_rotation_consumes_latest_secret(ctx, workload, mocker):
    mocker.patch("context.ContextConnection.available", return_value=True)
    _, client, state = context_state(latest_key="new-context-token-123456")
    ctx.run(ctx.on.secret_changed(client), state)
    assert workload.configure.call_args.kwargs["context"].api_key == "new-context-token-123456"


def test_context_disconnect_reconciles_without_remote_provider(ctx, workload):
    relation, _, state = context_state()
    result = ctx.run(ctx.on.relation_broken(relation), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert workload.configure.call_args.kwargs["context"] is None


def test_incomplete_context_waits_without_rewriting_running_configuration(ctx, workload):
    relation = Relation("context-store")
    state = configured_state(relations={relation})
    result = ctx.run(ctx.on.relation_changed(relation), state)
    assert isinstance(result.unit_status, ops.WaitingStatus)
    assert result.get_relation(relation.id).local_app_data["context-id"]
    workload.configure.assert_not_called()
    workload.stop.assert_not_called()


def test_context_identity_cannot_change_while_related(ctx, workload, mocker):
    mocker.patch("context.ContextConnection.available", return_value=True)
    relation, _, state = context_state()
    state = ctx.run(ctx.on.relation_changed(relation), state)
    workload.reset_mock()
    result = ctx.run(
        ctx.on.config_changed(),
        State(
            leader=True,
            config={**state.config, "context-id": "different-agent"},
            secrets=state.secrets,
            relations=state.relations,
        ),
    )
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.configure.assert_not_called()


def test_context_failure_is_not_reported_active(ctx, mocker):
    mocker.patch("context.ContextConnection.available", return_value=False)
    relation, _, state = context_state()
    result = ctx.run(ctx.on.relation_changed(relation), state)
    assert isinstance(result.unit_status, ops.WaitingStatus)


def test_provider_endpoint_change_keeps_identity_and_adopts_new_address(ctx, workload, mocker):
    mocker.patch("context.ContextConnection.available", return_value=True)
    relation, _, state = context_state()
    state = ctx.run(ctx.on.relation_changed(relation), state)
    old = state.get_relation(relation.id)
    changed = Relation(
        "context-store",
        id=old.id,
        local_app_data=old.local_app_data,
        remote_app_data={**old.remote_app_data, "endpoint": "http://10.10.10.3:1933"},
    )
    result = ctx.run(
        ctx.on.relation_changed(changed),
        State(
            leader=True,
            config=state.config,
            secrets=state.secrets,
            relations={changed},
        ),
    )
    assert isinstance(result.unit_status, ops.ActiveStatus)
    connection = workload.configure.call_args.kwargs["context"]
    assert connection.endpoint == "http://10.10.10.3:1933"
    assert connection.identity == "review-agent"


def test_configuration_creates_api_secret_and_starts_gateway(ctx, workload):
    result = ctx.run(ctx.on.config_changed(), configured_state())
    assert isinstance(result.unit_status, ops.ActiveStatus)
    api_secret = next(s for s in result.secrets if s.label == API_SECRET_LABEL)
    assert api_secret.owner == "unit"
    args = workload.configure.call_args.kwargs
    assert args["api_key"] == api_secret.tracked_content["api-key"]
    assert args["provider_key"] == "sk-or-v1-test-key"
    assert args["model"] == "z-ai/glm-5.3"
    assert args["max_turns"] == 20


def test_secret_rotation_uses_new_revision_without_changing_api_identity(ctx, workload):
    provider = Secret(
        tracked_content={"api-key": "sk-or-v1-old-key"},
        latest_content={"api-key": "sk-or-v1-new-key"},
    )
    api = Secret(
        tracked_content={"api-key": "stable-api-token"}, owner="unit", label=API_SECRET_LABEL
    )
    state = State(config={"openrouter-secret": provider.id}, secrets={provider, api})
    result = ctx.run(ctx.on.secret_changed(provider), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    args = workload.configure.call_args.kwargs
    assert args["provider_key"] == "sk-or-v1-new-key"
    assert args["api_key"] == "stable-api-token"


def test_removing_credential_configuration_stops_gateway(ctx, workload):
    state = ctx.run(ctx.on.config_changed(), configured_state())
    workload.reset_mock()
    result = ctx.run(ctx.on.config_changed(), State(secrets=state.secrets))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.stop.assert_called_once()
    workload.configure.assert_not_called()


def test_inaccessible_secret_stops_gateway(ctx, workload):
    missing = Secret(tracked_content={"api-key": "sk-or-v1-missing"})
    result = ctx.run(ctx.on.config_changed(), State(config={"openrouter-secret": missing.id}))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    assert "Cannot read" in result.unit_status.message
    workload.stop.assert_called_once()


@pytest.mark.parametrize("key", ["", "short", "key-with\nnewline", "${ENV_VAR}"])
def test_invalid_secret_content_is_not_rendered(ctx, workload, key):
    provider = Secret(tracked_content={"api-key": key})
    result = ctx.run(
        ctx.on.config_changed(),
        State(config={"openrouter-secret": provider.id}, secrets={provider}),
    )
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.configure.assert_not_called()


@pytest.mark.parametrize("config", [{"max-turns": 0}, {"max-turns": 1001}, {"model": ""}])
def test_invalid_configuration_is_blocked(ctx, workload, config):
    result = ctx.run(ctx.on.config_changed(), State(config=config))
    assert isinstance(result.unit_status, ops.BlockedStatus)
    workload.configure.assert_not_called()


def test_scale_out_is_blocked(ctx, workload):
    result = ctx.run(ctx.on.update_status(), configured_state(planned_units=2))
    assert "one unit" in result.unit_status.message
    workload.stop.assert_called_once()


def test_unhealthy_gateway_is_not_active(ctx, workload):
    workload.healthy.return_value = False
    result = ctx.run(ctx.on.update_status(), configured_state())
    assert isinstance(result.unit_status, ops.WaitingStatus)


def test_access_action_returns_secret_reference_not_token(ctx):
    result = ctx.run(ctx.on.action("get-api-access"), State())
    api = next(s for s in result.secrets if s.label == API_SECRET_LABEL)
    assert ctx.action_results["secret-id"] == api.id
    assert api.tracked_content["api-key"] not in str(ctx.action_results)


def test_restart_action_reconciles_and_forces_restart(ctx, workload):
    ctx.run(ctx.on.action("restart"), configured_state())
    assert workload.configure.call_args.kwargs["force_restart"] is True


def github_state(pem, *, latest_pem=None, config=None):
    provider = Secret(tracked_content={"api-key": "sk-or-v1-test-key"})
    github = Secret(
        tracked_content={"private-key": pem},
        latest_content={"private-key": latest_pem or pem},
    )
    api = Secret(
        tracked_content={"api-key": "stable-api-token"}, owner="unit", label=API_SECRET_LABEL
    )
    return github, State(
        config={
            "openrouter-secret": provider.id,
            "github-app-id": "4245402",
            "github-installation-id": "145176996",
            "github-app-secret": github.id,
            **(config or {}),
        },
        secrets={provider, github, api},
    )


def test_github_is_optional_and_ids_can_be_staged_without_key(ctx, workload):
    state = configured_state()
    config = {**state.config, "github-app-id": "4245402", "github-installation-id": "145176996"}
    result = ctx.run(ctx.on.config_changed(), State(config=config, secrets=state.secrets))
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert workload.configure.call_args.kwargs["github_app"] is None


def test_github_secret_rotation_preserves_other_credentials(ctx, workload, github_pems):
    github, state = github_state(github_pems[0], latest_pem=github_pems[1])
    result = ctx.run(ctx.on.secret_changed(github), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    args = workload.configure.call_args.kwargs
    assert args["github_app"].app_id == "4245402"
    assert args["github_app"].installation_id == "145176996"
    assert args["github_app"].private_key == github_pems[1]
    assert args["api_key"] == "stable-api-token"
    assert args["provider_key"] == "sk-or-v1-test-key"


def test_clearing_github_secret_disables_only_github(ctx, workload, github_pems):
    _, state = github_state(github_pems[0])
    config = {k: v for k, v in state.config.items() if k != "github-app-secret"}
    result = ctx.run(ctx.on.config_changed(), State(config=config, secrets=state.secrets))
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert workload.configure.call_args.kwargs["github_app"] is None
    workload.stop.assert_not_called()


@pytest.mark.parametrize(
    "config",
    [
        {"github-app-id": ""},
        {"github-app-id": "-1"},
        {"github-installation-id": "0"},
        {"github-installation-id": "not-a-number"},
    ],
)
def test_github_secret_requires_valid_ids(ctx, workload, github_pems, config):
    _, state = github_state(github_pems[0], config=config)
    result = ctx.run(ctx.on.config_changed(), state)
    assert isinstance(result.unit_status, ops.BlockedStatus)
    assert next(iter(config)) in result.unit_status.message
    workload.configure.assert_not_called()
    workload.stop.assert_called_once()


def test_invalid_github_key_stops_without_disclosing_it(ctx, workload):
    _, state = github_state("invalid-sensitive-key-content")
    result = ctx.run(ctx.on.config_changed(), state)
    assert isinstance(result.unit_status, ops.BlockedStatus)
    assert "unencrypted RSA private key" in result.unit_status.message
    assert "invalid-sensitive-key-content" not in result.unit_status.message
    workload.stop.assert_called_once()


def test_inaccessible_github_secret_stops_gateway(ctx, workload, github_pems):
    github, state = github_state(github_pems[0])
    result = ctx.run(
        ctx.on.update_status(), State(config=state.config, secrets=state.secrets - {github})
    )
    assert isinstance(result.unit_status, ops.BlockedStatus)
    assert "Cannot read github-app-secret" in result.unit_status.message
    workload.stop.assert_called_once()


def test_github_check_returns_only_ids_and_count(ctx, workload, github_pems):
    _, state = github_state(github_pems[0])
    workload.check_github_app.return_value = 3
    ctx.run(ctx.on.action("check-github-app"), state)
    assert ctx.action_results == {
        "app-id": "4245402",
        "installation-id": "145176996",
        "repositories": 3,
    }


def test_github_check_requires_configuration(ctx, workload):
    with pytest.raises(ActionFailed, match="Set github-app-secret"):
        ctx.run(ctx.on.action("check-github-app"), configured_state())
    workload.check_github_app.assert_not_called()


def test_github_check_failure_does_not_expose_subprocess_output(ctx, workload, github_pems):
    _, state = github_state(github_pems[0])
    workload.check_github_app.side_effect = subprocess.CalledProcessError(
        1, ["gh"], output="sensitive-output", stderr="sensitive-error"
    )
    with pytest.raises(ActionFailed, match="GitHub App authentication failed") as exc:
        ctx.run(ctx.on.action("check-github-app"), state)
    assert "sensitive" not in str(exc.value)

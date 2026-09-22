"""Exercise the charm's configuration and credential lifecycle through Ops events."""

from pathlib import Path

import ops
import pytest
import yaml
from ops.testing import Context, Secret, State

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
        meta={"name": spec["name"], "peers": spec["peers"]},
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

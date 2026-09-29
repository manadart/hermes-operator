"""Reconciliation must protect the index and preserve it across credential changes."""

import json
import os
from types import SimpleNamespace

import pytest

import workload


@pytest.fixture
def host(tmp_path, monkeypatch, mocker):
    state = tmp_path / "state"
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(workload, "STATE", state)
    monkeypatch.setattr(workload, "INSTALL", install)
    monkeypatch.setattr(workload, "SERVICE_FILE", tmp_path / "openviking.service")
    mocker.patch(
        "workload.pwd.getpwnam",
        return_value=SimpleNamespace(
            pw_uid=os.getuid(),
            pw_gid=os.getgid(),
        ),
    )
    original = workload.write_file

    def write(path, content, **kwargs):
        kwargs.setdefault("uid", os.getuid())
        kwargs.setdefault("gid", os.getgid())
        return original(path, content, **kwargs)

    mocker.patch("workload.write_file", side_effect=write)
    command = mocker.patch("workload.run")
    return state, command


def settings(**overrides):
    return dict(
        bind_address="0.0.0.0",
        model_key="model-secret",
        root_key="root-secret",
        embedding_model="openai/text-embedding-3-small",
        dimension=1536,
        extraction_model="z-ai/glm-5.3",
        **overrides,
    )


def test_configuration_rotation_and_stop_preserve_database(host):
    state, command = host
    server = workload.OpenVikingWorkload()
    server.configure(**settings())
    database = state / "data/database"
    database.write_bytes(b"precious-context")
    assert (state / "ov.conf").stat().st_mode & 0o777 == 0o600
    assert (state / "data").stat().st_mode & 0o777 == 0o700
    command.reset_mock()
    server.configure(**settings())
    assert not any(c.args[1] == "restart" for c in command.call_args_list)
    server.configure(**{**settings(), "model_key": "rotated-model-key"})
    command.assert_any_call("systemctl", "restart", workload.SERVICE)
    config = json.loads((state / "ov.conf").read_text())
    assert config["embedding"]["dense"]["api_key"] == "rotated-model-key"
    server.stop()
    assert not (state / "ov.conf").exists()
    assert database.read_bytes() == b"precious-context"


@pytest.mark.parametrize("change", [{"dimension": 3072}, {"embedding_model": "different/model"}])
def test_incompatible_embedding_change_cannot_rewrite_live_configuration(host, change):
    state, command = host
    server = workload.OpenVikingWorkload()
    server.configure(**settings())
    before = (state / "ov.conf").read_bytes()
    command.reset_mock()
    with pytest.raises(ValueError, match="cannot change"):
        server.configure(**{**settings(), **change})
    assert (state / "ov.conf").read_bytes() == before
    command.assert_not_called()


def test_workload_cannot_redirect_data_directory(host, tmp_path):
    state, _ = host
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    (state / "data").symlink_to(outside)
    with pytest.raises(OSError):
        workload.OpenVikingWorkload().ensure_state()
    assert outside.stat().st_mode & 0o777 == 0o755

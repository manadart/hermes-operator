"""Check persistence, permissions, and when reconciliation restarts the workload."""

import json
import os
from types import SimpleNamespace

import pytest
import yaml

import workload
from context import ContextConnection
from github_app import GitHubApp


@pytest.fixture
def host(tmp_path, monkeypatch, mocker):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(workload, "STATE", state)
    monkeypatch.setattr(workload, "WORKSPACE", state / "workspace")
    monkeypatch.setattr(workload, "SERVICE_FILE", tmp_path / "hermes.service")
    monkeypatch.setattr(workload, "INSTALL", tmp_path / "install")
    mocker.patch(
        "workload.pwd.getpwnam",
        return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()),
    )
    # Exercise real atomic writes as the test runner; only root-owned service
    # files need their requested ownership mapped to this unprivileged process.
    original_write = workload.write_file

    def write_as_test_user(path, content, **kwargs):
        kwargs.setdefault("uid", os.getuid())
        kwargs.setdefault("gid", os.getgid())
        return original_write(path, content, **kwargs)

    mocker.patch("workload.write_file", side_effect=write_as_test_user)
    command = mocker.patch("workload.run")
    mocker.patch.object(workload.HermesWorkload, "running", return_value=True)
    return state, command


def test_reconciliation_preserves_state_and_restarts_only_for_changes(host):
    state, command = host
    (state / "state.db").write_bytes(b"existing-session-state")
    agent = workload.HermesWorkload()
    settings = dict(model="z-ai/glm-5.3", max_turns=20, provider_key="sk-test", api_key="api-test")
    agent.configure(**settings)
    command.assert_any_call("systemctl", "restart", workload.SERVICE)
    command.reset_mock()
    agent.configure(**settings)
    assert not any(call.args[1] == "restart" for call in command.call_args_list)
    agent.configure(**{**settings, "provider_key": "sk-new"})
    command.assert_any_call("systemctl", "restart", workload.SERVICE)
    assert (state / "state.db").read_bytes() == b"existing-session-state"
    assert (state / ".env").stat().st_mode & 0o777 == 0o600
    rendered = yaml.safe_load((state / "config.yaml").read_text())
    assert rendered["model"] == {"provider": "openrouter", "default": "z-ai/glm-5.3"}
    assert "sk-new" not in (state / "config.yaml").read_text()


def test_stopping_clears_credentials_but_preserves_sessions(host):
    state, command = host
    workload.SERVICE_FILE.write_text("service")
    (state / ".env").write_text("OPENROUTER_API_KEY=old-key")
    (state / "gh-app-key.pem").write_text("private-key")
    (state / "github-app.json").write_text("{}")
    (state / "state.db").write_bytes(b"sessions")
    workload.HermesWorkload().stop()
    command.assert_called_once_with("systemctl", "disable", "--now", workload.SERVICE)
    assert not (state / ".env").exists()
    assert not (state / "gh-app-key.pem").exists()
    assert not (state / "github-app.json").exists()
    assert (state / "state.db").read_bytes() == b"sessions"


def test_context_connect_and_disconnect_preserve_local_knowledge(host):
    state, command = host
    agent = workload.HermesWorkload()
    settings = dict(model="z-ai/glm-5.3", max_turns=20, provider_key="sk-test", api_key="api-test")
    agent.configure(**settings)
    memory = state / "memories/MEMORY.md"
    memory.write_text("Local knowledge must survive relation changes")
    connection = ContextConnection("http://10.10.10.2:1933", "agent", "context-secret-key")
    agent.configure(**settings, context=connection)
    assert yaml.safe_load((state / "config.yaml").read_text())["memory"]["provider"] == "openviking"
    assert 'OPENVIKING_API_KEY="context-secret-key"' in (state / ".env").read_text()
    assert "context-secret-key" not in (state / "config.yaml").read_text()
    command.reset_mock()
    agent.configure(**settings)
    assert "OPENVIKING" not in (state / ".env").read_text()
    assert "openviking" not in (state / "config.yaml").read_text()
    assert memory.read_text() == "Local knowledge must survive relation changes"
    command.assert_any_call("systemctl", "restart", workload.SERVICE)


def test_configuring_existing_install_initializes_managed_home_without_losing_state(host):
    state, _ = host
    (state / "sessions").mkdir()
    transcript = state / "sessions/existing.json"
    transcript.write_text('{"message":"preserve me"}')
    workload.HermesWorkload().configure(
        model="z-ai/glm-5.3", max_turns=20, provider_key="sk-test", api_key="api-test"
    )
    for name in ("workspace", "cron", "sessions", "logs", "memories"):
        directory = state / name
        assert directory.is_dir()
        assert directory.stat().st_mode & 0o777 == 0o700
        assert directory.stat().st_uid == os.getuid()
    assert transcript.read_text() == '{"message":"preserve me"}'


def test_managed_home_does_not_follow_workload_directory_symlinks(host, tmp_path):
    state, _ = host
    target = tmp_path / "unrelated-directory"
    target.mkdir(mode=0o755)
    (state / "memories").symlink_to(target)
    with pytest.raises(OSError):
        workload.HermesWorkload().ensure_state()
    assert target.stat().st_mode & 0o777 == 0o755


def test_atomic_write_replaces_symlink_without_overwriting_target(tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("preserve me")
    destination = tmp_path / "config.yaml"
    destination.symlink_to(target)
    workload.write_file(destination, "new config", uid=os.getuid(), gid=os.getgid())
    assert not destination.is_symlink()
    assert destination.read_text() == "new config"
    assert target.read_text() == "preserve me"


def test_install_marker_requires_expected_version_and_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "INSTALL", tmp_path)
    monkeypatch.setattr(workload, "SOURCE", tmp_path / "source")
    agent = workload.HermesWorkload()
    (tmp_path / "installed.json").write_text(json.dumps(agent._install_identity()))
    assert not agent.installed()

    executable = workload.SOURCE / ".venv/bin/hermes"
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert agent.installed()
    (tmp_path / "installed.json").write_text('{"version":"old"}')
    assert not agent.installed()


def test_github_key_permissions_rotation_and_removal(host, github_pems):
    state, command = host
    agent = workload.HermesWorkload()
    (state / "state.db").write_bytes(b"saved-session")
    settings = dict(model="z-ai/glm-5.3", max_turns=20, provider_key="sk-test", api_key="api-test")
    app = GitHubApp.from_secret("4245402", "145176996", github_pems[0])
    agent.configure(**settings, github_app=app)
    key_path = state / "gh-app-key.pem"
    assert key_path.read_text() == github_pems[0]
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert key_path.stat().st_uid == os.getuid()
    assert (state / "github-app.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((state / "github-app.json").read_text()) == {
        "app_id": "4245402",
        "installation_id": "145176996",
    }
    helper = workload.INSTALL / "bin/gh"
    assert helper.stat().st_mode & 0o777 == 0o755
    assert helper.read_text().startswith(f"#!{workload.SOURCE}/.venv/bin/python\n")
    assert f"Environment=PATH={workload.INSTALL}/bin:" in workload.SERVICE_FILE.read_text()
    env = dict(
        (name, json.loads(value))
        for name, value in (
            line.split("=", 1) for line in (state / ".env").read_text().splitlines()
        )
    )
    assert env["GH_APP_ID"] == env["GITHUB_APP_ID"] == "4245402"
    assert env["GH_INSTALL_ID"] == env["GITHUB_APP_INSTALLATION_ID"] == "145176996"
    assert env["GH_APP_KEY"] == env["GITHUB_APP_PRIVATE_KEY_PATH"] == str(key_path)
    assert "PRIVATE KEY" not in (state / ".env").read_text()
    config = yaml.safe_load((state / "config.yaml").read_text())
    assert config["terminal"]["env_passthrough"] == ["GH_APP_ID", "GH_INSTALL_ID", "GH_APP_KEY"]
    assert config["terminal"]["shell_init_files"][-1] == str(workload.INSTALL / "github-path.sh")
    command.reset_mock()
    agent.configure(**settings, github_app=app)
    assert not any(call.args[1] == "restart" for call in command.call_args_list)
    command.reset_mock()
    agent.configure(
        **settings, github_app=GitHubApp.from_secret("4245402", "145176996", github_pems[1])
    )
    assert key_path.read_text() == github_pems[1]
    command.assert_any_call("systemctl", "restart", workload.SERVICE)
    command.reset_mock()
    agent.configure(**settings)
    assert not key_path.exists()
    assert not (state / "github-app.json").exists()
    assert f"Environment=PATH={workload.INSTALL}/bin:" not in workload.SERVICE_FILE.read_text()
    assert "GH_APP" not in (state / ".env").read_text()
    assert "GH_INSTALL" not in (state / ".env").read_text()
    assert "GITHUB_APP" not in (state / ".env").read_text()
    assert "env_passthrough" not in yaml.safe_load((state / "config.yaml").read_text())["terminal"]
    assert (state / "state.db").read_bytes() == b"saved-session"
    assert 'OPENROUTER_API_KEY="sk-test"' in (state / ".env").read_text()
    command.assert_any_call("systemctl", "restart", workload.SERVICE)

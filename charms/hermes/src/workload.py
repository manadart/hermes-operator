"""Installation and systemd lifecycle for the pinned Hermes workload."""

import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from context import ContextConnection
from github_app import GitHubApp

VERSION = "0.21.3"
COMMIT = "345cd2b057a452236de401d3534b8502a7465e8d"
SOURCE_URL = f"https://codeload.github.com/NousResearch/hermes-agent/tar.gz/{COMMIT}"
SOURCE_SHA256 = "ed17fdd4423bfc7faee02399a866d5ee30a04a09523da6ea39d794f664526e55"
UV_VERSION = "0.11.6"
INSTALL = Path("/opt/hermes")
SOURCE = INSTALL / f"hermes-agent-{COMMIT}"
STATE = Path("/var/lib/hermes")
WORKSPACE = STATE / "workspace"
SERVICE_FILE = Path("/etc/systemd/system/hermes.service")
SERVICE = "hermes.service"
API_URL = "http://127.0.0.1:8642"
# HERMES_MANAGED requires these directories to exist before loading agent config.
STATE_SUBDIRS = ("workspace", "cron", "sessions", "logs", "memories")


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a command without a shell; never include credential values in argv."""
    return subprocess.run(args, check=True, timeout=1800, **kwargs)


def write_file(path: Path, content: str, *, uid: int = 0, gid: int = 0, mode: int = 0o600) -> bool:
    """Atomically replace a file without following a workload-created symlink."""
    if path.exists() and not path.is_symlink():
        metadata = path.stat()
        if (
            path.read_text() == content
            and metadata.st_uid == uid
            and metadata.st_gid == gid
            and stat.S_IMODE(metadata.st_mode) == mode
        ):
            return False
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            os.fchmod(out.fileno(), mode)
            os.fchown(out.fileno(), uid, gid)
            out.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def configuration(
    model: str, max_turns: int, *, github_enabled: bool = False, context_enabled: bool = False
) -> str:
    """Render only the settings owned by the initial charm."""
    toolsets = ["terminal", "file", "delegation", "memory", "session_search", "todo"]
    terminal = {"backend": "local", "cwd": str(WORKSPACE)}
    if github_enabled:
        terminal["env_passthrough"] = ["GH_APP_ID", "GH_INSTALL_ID", "GH_APP_KEY"]
        # The terminal's login shell can replace systemd's PATH before its snapshot.
        terminal["shell_init_files"] = [
            "~/.profile",
            "~/.bash_profile",
            "~/.bashrc",
            str(INSTALL / "github-path.sh"),
        ]
    settings = {
        "model": {"provider": "openrouter", "default": model},
        "agent": {"max_turns": max_turns},
        "terminal": terminal,
        "platform_toolsets": {"cli": toolsets, "api_server": toolsets},
        "platforms": {
            "api_server": {"enabled": True, "extra": {"host": "127.0.0.1", "port": 8642}}
        },
        "delegation": {"max_concurrent_children": 2},
        "kanban": {"dispatch_in_gateway": False},
    }
    if context_enabled:
        settings["memory"] = {"provider": "openviking"}
    return yaml.safe_dump(settings, sort_keys=False)


def systemd_unit(*, github_enabled: bool = False) -> str:
    github_path = f"{INSTALL}/bin:" if github_enabled else ""
    return f"""[Unit]
Description=Hermes Agent gateway
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=hermes
Group=hermes
WorkingDirectory={WORKSPACE}
Environment=HOME={STATE}
Environment=HERMES_HOME={STATE}
Environment=HERMES_MANAGED=juju
Environment=HERMES_SUPERVISED_CHILD=1
Environment=HERMES_GATEWAY_NO_SUPERVISE=1
Environment=PYTHONUNBUFFERED=1
Environment=VIRTUAL_ENV={SOURCE}/.venv
Environment=PATH={github_path}{SOURCE}/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart={SOURCE}/.venv/bin/hermes gateway run
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillMode=mixed
UMask=0077
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


class HermesWorkload:
    """All host mutations are kept outside the charm's event handlers."""

    def ensure_state(self) -> bool:
        """Initialize the managed home on fresh installs and charm upgrades."""
        account = pwd.getpwnam("hermes")
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        state_fd = os.open(STATE, flags)
        changed = False
        try:
            os.fchown(state_fd, account.pw_uid, account.pw_gid)
            os.fchmod(state_fd, 0o700)
            for name in STATE_SUBDIRS:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=state_fd)
                    changed = True
                except FileExistsError:
                    pass
                # Workload-writable directory entries must not redirect root chown/chmod.
                directory_fd = os.open(name, flags, dir_fd=state_fd)
                try:
                    os.fchown(directory_fd, account.pw_uid, account.pw_gid)
                    os.fchmod(directory_fd, 0o700)
                finally:
                    os.close(directory_fd)
        finally:
            os.close(state_fd)
        return changed

    def installed(self) -> bool:
        try:
            marker = json.loads((INSTALL / "installed.json").read_text())
        except (OSError, ValueError):
            return False
        return marker == self._install_identity() and (SOURCE / ".venv/bin/hermes").is_file()

    @staticmethod
    def _install_identity() -> dict:
        return {"version": VERSION, "commit": COMMIT, "uv": UV_VERSION, "python": "3.12"}

    def install(self) -> None:
        if self.installed():
            return
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        run("apt-get", "update", env=env)
        run(
            "apt-get",
            "install",
            "-y",
            "-o",
            "DPkg::Lock::Timeout=300",
            "ca-certificates",
            "git",
            "ripgrep",
            "python3.12-venv",
            "build-essential",
            "python3.12-dev",
            env=env,
        )
        INSTALL.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            pwd.getpwnam("hermes")
        except KeyError:
            run(
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(STATE),
                "--shell",
                "/usr/sbin/nologin",
                "hermes",
            )
        self.ensure_state()
        if not SOURCE.exists():
            with tempfile.TemporaryDirectory(dir=INSTALL) as temporary:
                archive = Path(temporary) / "source.tar.gz"
                with (
                    urllib.request.urlopen(SOURCE_URL, timeout=120) as response,
                    archive.open("wb") as out,
                ):
                    shutil.copyfileobj(response, out)
                with archive.open("rb") as source_file:
                    digest = hashlib.file_digest(source_file, "sha256").hexdigest()
                if digest != SOURCE_SHA256:
                    raise ValueError("Hermes source archive checksum mismatch")
                with tarfile.open(archive) as tar:
                    tar.extractall(temporary, filter="data")
                extracted = Path(temporary) / SOURCE.name
                if not (extracted / "uv.lock").is_file():
                    raise ValueError("Hermes source archive has no dependency lock")
                extracted.rename(SOURCE)
        installer = INSTALL / "installer"
        run("python3.12", "-m", "venv", str(installer))
        run(str(installer / "bin/pip"), "install", f"uv=={UV_VERSION}")
        run(
            str(installer / "bin/uv"),
            "sync",
            "--frozen",
            "--no-dev",
            "--extra",
            "messaging",
            "--extra",
            "mcp",
            "--python",
            "/usr/bin/python3.12",
            cwd=SOURCE,
            env={**env, "UV_PYTHON_DOWNLOADS": "never"},
        )
        write_file(
            INSTALL / "installed.json", json.dumps(self._install_identity()) + "\n", mode=0o644
        )

    def configure(
        self,
        *,
        model: str,
        max_turns: int,
        provider_key: str,
        api_key: str,
        github_app: GitHubApp | None = None,
        context: ContextConnection | None = None,
        force_restart: bool = False,
    ) -> None:
        changed = self.ensure_state()
        account = pwd.getpwnam("hermes")
        identity = {"uid": account.pw_uid, "gid": account.pw_gid}
        changed = (
            write_file(
                STATE / "config.yaml",
                configuration(
                    model,
                    max_turns,
                    github_enabled=github_app is not None,
                    context_enabled=context is not None,
                ),
                **identity,
            )
            or changed
        )
        environment_values = {"OPENROUTER_API_KEY": provider_key, "API_SERVER_KEY": api_key}
        if context is not None:
            environment_values.update(
                {"OPENVIKING_ENDPOINT": context.endpoint, "OPENVIKING_API_KEY": context.api_key}
            )
        key_path = STATE / "gh-app-key.pem"
        if github_app is not None:
            changed = self.install_github_cli() or changed
            changed = write_file(key_path, github_app.private_key, **identity) or changed
            changed = (
                write_file(
                    STATE / "github-app.json",
                    json.dumps(
                        {"app_id": github_app.app_id, "installation_id": github_app.installation_id}
                    )
                    + "\n",
                    **identity,
                )
                or changed
            )
            environment_values.update(
                {
                    "GH_APP_ID": github_app.app_id,
                    "GH_INSTALL_ID": github_app.installation_id,
                    "GH_APP_KEY": str(key_path),
                    # Hermes's built-in GitHub skill-source authentication uses these names.
                    "GITHUB_APP_ID": github_app.app_id,
                    "GITHUB_APP_INSTALLATION_ID": github_app.installation_id,
                    "GITHUB_APP_PRIVATE_KEY_PATH": str(key_path),
                }
            )
        else:
            for path in (key_path, STATE / "github-app.json"):
                if path.exists() or path.is_symlink():
                    path.unlink()
                    changed = True
        # Hermes loads this file with python-dotenv. JSON quoting escapes quotes/backslashes.
        environment = "".join(
            f"{name}={json.dumps(value)}\n" for name, value in environment_values.items()
        )
        changed = write_file(STATE / ".env", environment, **identity) or changed
        unit_changed = write_file(
            SERVICE_FILE, systemd_unit(github_enabled=github_app is not None), mode=0o644
        )
        if unit_changed:
            run("systemctl", "daemon-reload")
        run("systemctl", "enable", SERVICE)
        if changed or unit_changed or force_restart:
            run("systemctl", "restart", SERVICE)
        elif not self.running():
            run("systemctl", "start", SERVICE)

    def install_github_cli(self) -> bool:
        """Install on enable, including upgrades of an already installed workload."""
        if not Path("/usr/bin/gh").is_file():
            env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            run("apt-get", "update", env=env)
            run("apt-get", "install", "-y", "-o", "DPkg::Lock::Timeout=300", "gh", env=env)
        binary_dir = INSTALL / "bin"
        binary_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        sources = Path(__file__).parent
        changed = write_file(
            binary_dir / "github_app.py", (sources / "github_app.py").read_text(), mode=0o644
        )
        changed = (
            write_file(
                binary_dir / "gh",
                f"#!{SOURCE}/.venv/bin/python\n" + (sources / "github_cli.py").read_text(),
                mode=0o755,
            )
            or changed
        )
        return (
            write_file(
                INSTALL / "github-path.sh", f'export PATH="{binary_dir}:$PATH"\n', mode=0o644
            )
            or changed
        )

    def check_github_app(self) -> int:
        """Probe installation access without returning tokens or repository names."""
        result = subprocess.run(
            [
                "/usr/sbin/runuser",
                "-u",
                "hermes",
                "--",
                str(INSTALL / "bin/gh"),
                "api",
                "/installation/repositories",
                "--jq",
                ".total_count",
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": str(STATE)},
            check=True,
            timeout=90,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip())

    def stop(self) -> None:
        if SERVICE_FILE.exists():
            run("systemctl", "disable", "--now", SERVICE)
        (STATE / ".env").unlink(missing_ok=True)
        (STATE / "gh-app-key.pem").unlink(missing_ok=True)
        (STATE / "github-app.json").unlink(missing_ok=True)

    def running(self) -> bool:
        return (
            subprocess.run(
                ["systemctl", "is-active", "--quiet", SERVICE], timeout=15, check=False
            ).returncode
            == 0
        )

    def healthy(self, *, attempts: int = 1) -> bool:
        for attempt in range(attempts):
            if self.running():
                try:
                    with urllib.request.urlopen(f"{API_URL}/health", timeout=2) as response:
                        if json.load(response).get("status") == "ok":
                            return True
                except (OSError, ValueError, urllib.error.URLError):
                    pass
            if attempt + 1 < attempts:
                time.sleep(1)
        return False

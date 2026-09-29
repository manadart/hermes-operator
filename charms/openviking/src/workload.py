"""Pinned OpenViking installation and persistent systemd workload."""

import hashlib
import json
import os
import pwd
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.4.22"
UV_VERSION = "0.11.6"
INSTALL = Path("/opt/openviking")
PROJECT = INSTALL / "runtime"
STATE = Path("/var/lib/openviking")
SERVICE_FILE = Path("/etc/systemd/system/openviking.service")
SERVICE = "openviking.service"
DEPENDENCIES = Path(__file__).parent / "workload-deps"


def run(*args: str, **kwargs):
    return subprocess.run(args, check=True, timeout=1800, **kwargs)


def write_file(path: Path, content: str, *, uid=0, gid=0, mode=0o600) -> bool:
    if path.exists() and not path.is_symlink():
        info = path.stat()
        if (
            path.read_text() == content
            and info.st_uid == uid
            and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == mode
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


def service_unit() -> str:
    return f"""[Unit]
Description=OpenViking context server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=openviking
Group=openviking
WorkingDirectory={STATE}
Environment=HOME={STATE}
Environment=OPENVIKING_CONFIG_FILE={STATE}/ov.conf
Environment=PYTHONUNBUFFERED=1
Environment=PATH={PROJECT}/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart={PROJECT}/.venv/bin/openviking-server --config {STATE}/ov.conf
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


class OpenVikingWorkload:
    @staticmethod
    def identity() -> dict:
        return {
            "version": VERSION,
            "uv": UV_VERSION,
            "python": "3.12",
            "lock": hashlib.sha256((DEPENDENCIES / "uv.lock").read_bytes()).hexdigest(),
        }

    def installed(self) -> bool:
        try:
            return (
                json.loads((INSTALL / "installed.json").read_text()) == self.identity()
                and (PROJECT / ".venv/bin/openviking-server").is_file()
            )
        except (OSError, ValueError):
            return False

    def ensure_state(self) -> None:
        account = pwd.getpwnam("openviking")
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        state_fd = os.open(STATE, flags)
        try:
            os.fchown(state_fd, account.pw_uid, account.pw_gid)
            os.fchmod(state_fd, 0o700)
            try:
                os.mkdir("data", mode=0o700, dir_fd=state_fd)
            except FileExistsError:
                pass
            data_fd = os.open("data", flags, dir_fd=state_fd)
            try:
                os.fchown(data_fd, account.pw_uid, account.pw_gid)
                os.fchmod(data_fd, 0o700)
            finally:
                os.close(data_fd)
        finally:
            os.close(state_fd)

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
            "python3.12-venv",
            "python3.12-dev",
            "build-essential",
            "libgomp1",
            env=env,
        )
        try:
            pwd.getpwnam("openviking")
        except KeyError:
            run(
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(STATE),
                "--shell",
                "/usr/sbin/nologin",
                "openviking",
            )
        self.ensure_state()
        PROJECT.mkdir(mode=0o755, parents=True, exist_ok=True)
        for name in ("pyproject.toml", "uv.lock"):
            write_file(PROJECT / name, (DEPENDENCIES / name).read_text(), mode=0o644)
        installer = INSTALL / "installer"
        run("python3.12", "-m", "venv", str(installer))
        run(str(installer / "bin/pip"), "install", f"uv=={UV_VERSION}")
        run(
            str(installer / "bin/uv"),
            "sync",
            "--frozen",
            "--no-dev",
            "--python",
            "/usr/bin/python3.12",
            cwd=PROJECT,
            env={**env, "UV_PYTHON_DOWNLOADS": "never"},
        )
        write_file(INSTALL / "installed.json", json.dumps(self.identity()) + "\n", mode=0o644)

    def configure(
        self,
        *,
        bind_address: str,
        model_key: str,
        root_key: str,
        embedding_model: str,
        dimension: int,
        extraction_model: str,
    ) -> None:
        self.ensure_state()
        embedding = {"model": embedding_model, "dimension": dimension}
        marker = INSTALL / "embedding.json"
        if marker.exists() and json.loads(marker.read_text()) != embedding:
            raise ValueError(
                "Embedding model and dimension cannot change on an existing context store"
            )
        config = {
            "server": {
                "host": bind_address,
                "port": 1933,
                "auth_mode": "api_key",
                "root_api_key": root_key,
            },
            "storage": {
                "workspace": str(STATE / "data"),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "max_concurrent": 2,
                "dense": {
                    "provider": "openai",
                    "api_base": "https://openrouter.ai/api/v1",
                    "api_key": model_key,
                    **embedding,
                },
            },
            "vlm": {
                "provider": "openai",
                "api_base": "https://openrouter.ai/api/v1",
                "api_key": model_key,
                "model": extraction_model,
                "max_concurrent": 2,
            },
        }
        account = pwd.getpwnam("openviking")
        changed = write_file(
            STATE / "ov.conf",
            json.dumps(config, indent=2) + "\n",
            uid=account.pw_uid,
            gid=account.pw_gid,
        )
        write_file(marker, json.dumps(embedding) + "\n", mode=0o644)
        unit_changed = write_file(SERVICE_FILE, service_unit(), mode=0o644)
        if unit_changed:
            run("systemctl", "daemon-reload")
        run("systemctl", "enable", SERVICE)
        if changed or unit_changed:
            run("systemctl", "restart", SERVICE)
        else:
            run("systemctl", "start", SERVICE)

    def healthy(self, attempts: int = 1) -> bool:
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen("http://127.0.0.1:1933/ready", timeout=5) as response:
                    if response.status == 200 and json.load(response).get("status") == "ready":
                        return True
            except (OSError, ValueError, urllib.error.URLError):
                pass
            if attempt + 1 < attempts:
                time.sleep(2)
        return False

    def stop(self) -> None:
        if SERVICE_FILE.exists():
            run("systemctl", "disable", "--now", SERVICE)
        (STATE / "ov.conf").unlink(missing_ok=True)

#!/usr/bin/env python3
"""Check gh resolution through real Hermes terminal code without GitHub/model calls.

Run as root with the installed Hermes Python. Installs the optional gh helper,
then uses an isolated temporary home as the hermes user. No real keys are read.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def worker() -> None:
    from tools.environments.local import LocalEnvironment

    terminal = LocalEnvironment(cwd=str(Path(os.environ["HERMES_HOME"]) / "workspace"))
    try:
        result = terminal.execute(
            'test "$(command -v gh)" = /opt/hermes/bin/gh && '
            'test "$GH_APP_ID" = 4245402 && test "$GH_INSTALL_ID" = 145176996 && '
            'test "$GH_APP_KEY" = /var/lib/hermes/gh-app-key.pem && '
            'test -z "$GH_TOKEN" && test -z "$GITHUB_APP_ID" && gh --version'
        )
        assert result["returncode"] == 0, result
        print(result["output"].strip())
        print("PASS: Hermes terminal resolves the App wrapper and preserves GH_APP settings")
    finally:
        terminal.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--charm-src", default="/var/lib/juju/agents/unit-hermes-0/charm/src")
    args = parser.parse_args()
    if args.worker:
        worker()
        return
    if os.geteuid() != 0:
        parser.error("run as root to install the CLI and switch to the service user")
    sys.path.insert(0, args.charm_src)
    import workload

    workload.HermesWorkload().install_github_cli()
    if not (workload.STATE / "github-app.json").exists():
        try:
            workload.HermesWorkload().check_github_app()
        except subprocess.CalledProcessError as exc:
            assert "Configure a valid github-app-secret" in exc.stderr
        else:
            raise AssertionError("Unconfigured helper unexpectedly authenticated")
    with tempfile.TemporaryDirectory(prefix="hermes-github-smoke-") as temporary:
        workload.STATE = Path(temporary)
        workload.WORKSPACE = workload.STATE / "workspace"
        workload.HermesWorkload().ensure_state()
        config = workload.STATE / "config.yaml"
        config.write_text(workload.configuration("z-ai/glm-5.3", 20, github_enabled=True))
        config.chmod(0o644)
        subprocess.run(
            [
                "/usr/sbin/runuser",
                "-u",
                "hermes",
                "--",
                str(workload.SOURCE / ".venv/bin/python"),
                str(Path(__file__).resolve()),
                "--worker",
            ],
            env={
                "HOME": temporary,
                "HERMES_HOME": temporary,
                "HERMES_MANAGED": "juju",
                "PATH": f"/opt/hermes/bin:{workload.SOURCE}/.venv/bin:/usr/bin:/bin",
                "GH_APP_ID": "4245402",
                "GH_INSTALL_ID": "145176996",
                "GH_APP_KEY": "/var/lib/hermes/gh-app-key.pem",
                "GH_TOKEN": "unused-test-token",
                "GITHUB_APP_ID": "4245402",
            },
            check=True,
            timeout=90,
        )


if __name__ == "__main__":
    main()

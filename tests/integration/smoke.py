#!/usr/bin/env python3
"""Run on the workload machine as root; checks local APIs without model calls.

Run `seed`, restart the service or reboot the instance, then run `verify`.
Finish with `cleanup`. Only this script's session and marker are removed.
"""

import argparse
import hashlib
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8642"
STATE = Path("/var/lib/hermes")
MARKER = STATE / "workspace/.charm-smoke.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "verify", "cleanup"])
    args = parser.parse_args()
    # The charm emits JSON-quoted dotenv values. Never print the values.
    env = {
        name: json.loads(value)
        for name, value in (
            line.split("=", 1) for line in (STATE / ".env").read_text().splitlines()
        )
    }
    api_key = env["API_SERVER_KEY"]

    def request(path, *, body=None, method=None, authenticated=True):
        headers = {"Authorization": "Bearer " + api_key} if authenticated else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    assert request("/health", authenticated=False)["status"] == "ok"
    try:
        request("/v1/models", authenticated=False)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401, exc.code
    else:
        raise AssertionError("API accepted an unauthenticated model request")
    assert request("/v1/models")["object"] == "list"
    detailed = request("/health/detailed")
    assert detailed["status"] == "ok", detailed
    assert detailed["version"] == "0.21.3", detailed["version"]

    if args.mode == "seed":
        if MARKER.exists():
            raise RuntimeError("A smoke marker already exists; verify/cleanup the previous run")
        session_id = "charm_smoke_" + uuid.uuid4().hex
        session = request("/api/sessions", body={"id": session_id, "title": session_id})
        assert session["session"]["id"] == session_id
        MARKER.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "api_key_hash": hashlib.sha256(api_key.encode()).hexdigest(),
                }
            )
        )
    else:
        marker = json.loads(MARKER.read_text())
        assert hashlib.sha256(api_key.encode()).hexdigest() == marker["api_key_hash"]
        session = request("/api/sessions/" + marker["session_id"])
        assert session["session"]["title"] == marker["session_id"]
        if args.mode == "cleanup":
            request("/api/sessions/" + marker["session_id"], method="DELETE")
            MARKER.unlink()
    print(json.dumps({"result": "passed", "mode": args.mode, "health": detailed["status"]}))


if __name__ == "__main__":
    main()

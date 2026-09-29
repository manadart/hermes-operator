#!/usr/bin/env python3
"""Hold the old key only in memory while the operator rotates or disconnects.

Start on the Hermes unit, wait for the ready message, then perform the Juju
operation. No credentials are written to the report, disk or stdout.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def environment():
    return {
        k: json.loads(v)
        for k, v in (
            line.split("=", 1)
            for line in Path("/var/lib/hermes/.env").read_text().splitlines()
        )
    }


def access(endpoint, key):
    request = urllib.request.Request(
        endpoint + "/api/v1/fs/ls?uri=viking%3A%2F%2Fuser%2Fhermes%2Fmemories%2F",
        headers={"X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replacement", action="store_true", help="Also wait for new working key"
    )
    args = parser.parse_args()
    env = environment()
    endpoint, old_key = env["OPENVIKING_ENDPOINT"], env["OPENVIKING_API_KEY"]
    assert access(endpoint, old_key) == 200
    print("Ready for credential rotation or relation removal", flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if access(endpoint, old_key) == 401:
            if not args.replacement:
                print('{"old-key-revoked": "passed"}')
                return
            current = environment()
            key = current.get("OPENVIKING_API_KEY", "")
            if (
                key
                and key != old_key
                and access(current["OPENVIKING_ENDPOINT"], key) == 200
            ):
                print('{"old-key-revoked": "passed", "replacement-key": "passed"}')
                return
        time.sleep(2)
    raise AssertionError("Credential lifecycle did not complete before deadline")


if __name__ == "__main__":
    main()

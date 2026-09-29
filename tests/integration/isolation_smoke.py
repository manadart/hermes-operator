#!/usr/bin/env python3
"""Run on OpenViking as its service user; create and remove an isolated test account.

The primary account is never mutated. Keys stay inside this process.
"""

import argparse
import json
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-account", required=True)
    parser.add_argument("--uri", required=True)
    args = parser.parse_args()
    config = json.loads(Path("/var/lib/openviking/ov.conf").read_text())
    endpoint = "http://127.0.0.1:1933"
    account = "charm-isolation-" + uuid.uuid4().hex[:12]
    with httpx.Client(
        base_url=endpoint,
        timeout=30,
        headers={
            "X-API-Key": config["server"]["root_api_key"],
        },
    ) as root:
        response = root.get(f"/api/v1/admin/accounts/{args.primary_account}/users")
        response.raise_for_status()
        primary_key = next(
            u["api_key"] for u in response.json()["result"] if u["user_id"] == "hermes"
        )
        response = root.post(
            "/api/v1/admin/accounts",
            json={
                "account_id": account,
                "admin_user_id": "operator",
            },
        )
        response.raise_for_status()
        try:
            response = root.post(
                f"/api/v1/admin/accounts/{account}/users",
                json={
                    "user_id": "hermes",
                    "role": "user",
                },
            )
            response.raise_for_status()
            key = response.json()["result"]["user_key"]
            with httpx.Client(base_url=endpoint, timeout=30) as client:
                response = client.get(
                    "/api/v1/content/read",
                    params={"uri": args.uri},
                    headers={"X-API-Key": primary_key},
                )
                assert response.status_code == 200
                response = client.get(
                    "/api/v1/content/read",
                    params={"uri": args.uri},
                    headers={"X-API-Key": key},
                )
                assert response.status_code == 404, response.status_code
                response = client.get(
                    "/api/v1/admin/accounts", headers={"X-API-Key": key}
                )
                assert response.status_code == 403
            print('{"account-isolation": "passed", "admin-access-denied": "passed"}')
        finally:
            response = root.delete(f"/api/v1/admin/accounts/{account}")
            response.raise_for_status()


if __name__ == "__main__":
    main()

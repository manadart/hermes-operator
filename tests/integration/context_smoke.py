#!/usr/bin/env python3
"""Run as the Hermes service user with its installed Python and HERMES_HOME.

remember submits synthetic knowledge through Hermes's bundled provider. verify
starts a new provider session, waits for extraction, and uses semantic search and
read tools. These modes incur OpenRouter usage. export checks user-scoped export;
it deliberately does not implement a coordinated backup. Reports contain no keys.
"""

import argparse
import io
import json
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("remember", "verify", "export", "chat"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=420)
    args = parser.parse_args()
    home = Path(os.environ.get("HERMES_HOME", "/var/lib/hermes"))
    os.environ["HERMES_HOME"] = str(home)
    from dotenv import load_dotenv

    load_dotenv(home / ".env", override=True)
    # The installed entrypoint runs with the source tree on sys.path. Do the
    # same when this helper is copied outside that tree.
    source = Path(sys.executable).resolve().parent
    if not (source / "plugins").exists():
        source = Path(sys.executable).parent.parent.parent
    sys.path.insert(0, str(source))
    import httpx
    from plugins.memory.openviking import OpenVikingMemoryProvider

    endpoint = os.environ["OPENVIKING_ENDPOINT"]
    headers = {"X-API-Key": os.environ["OPENVIKING_API_KEY"]}
    with httpx.Client(
        base_url=endpoint, headers=headers, timeout=60, follow_redirects=False
    ) as api:
        # Consumers must not be able to list accounts or take a server backup.
        assert api.get("/api/v1/admin/accounts").status_code == 403
        assert api.post("/api/v1/pack/backup", json={}).status_code == 403
        if args.mode == "chat":
            report = json.loads(args.report.read_text())
            session = "context-recall-" + uuid.uuid4().hex
            with httpx.Client(
                base_url="http://127.0.0.1:8642",
                timeout=300,
                headers={"Authorization": "Bearer " + os.environ["API_SERVER_KEY"]},
            ) as gateway:
                response = gateway.post(
                    "/api/sessions", json={"id": session, "title": session}
                )
                response.raise_for_status()
                response = gateway.post(
                    f"/api/sessions/{session}/chat",
                    json={
                        "message": "What is my Aurora Cedar project's release checklist code?",
                        "instructions": (
                            "This checks your external memory integration. Use viking_search and "
                            "viking_read to retrieve the code. Return the exact stored code. "
                            "Do not use terminal, local files, delegation, or create new memories."
                        ),
                        "model": "z-ai/glm-5.3",
                        "provider": "openrouter",
                        "require_model_lock": True,
                        "model_options": {"reasoning": {"effort": "low"}},
                    },
                )
                response.raise_for_status()
                assert report["marker"] in response.text, (
                    "Agent did not recall the stored code"
                )
                response = gateway.get(
                    f"/api/sessions/{session}/messages?limit=100&order=oldest"
                )
                response.raise_for_status()
                names = [
                    m.get("tool_name")
                    for m in response.json()["data"]
                    if m.get("role") == "tool"
                ]
                # Automatic recall may already provide the URI before the first tool call.
                assert any(n in names for n in ("viking_search", "viking_read")), names
                assert not any(
                    n in names for n in ("terminal", "read_file", "delegate_task")
                ), names
                print(
                    json.dumps(
                        {
                            "agent-recall": "passed",
                            "session-id": session,
                            "tools": names,
                        }
                    )
                )
            return
        if args.mode == "export":
            report = json.loads(args.report.read_text())
            response = api.post(
                "/api/v1/pack/export",
                json={
                    "uri": "viking://user/hermes/memories/",
                    "include_vectors": False,
                },
            )
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as pack:
                assert any(
                    report["marker"].encode() in pack.read(p)
                    for p in pack.namelist()
                    if not p.endswith("/")
                )
                print(
                    json.dumps(
                        {
                            "scoped-export": "passed",
                            "archive-entries": len(pack.namelist()),
                        }
                    )
                )
            return

        provider = OpenVikingMemoryProvider()
        provider.initialize(
            "charm-context-smoke-" + uuid.uuid4().hex, hermes_home=str(home)
        )
        try:
            if args.mode == "remember":
                marker = "VIKING-CEDAR-" + uuid.uuid4().hex[:12]
                content = (
                    "For my Juju context portability project named Aurora Cedar, "
                    f"the release checklist code is {marker}. "
                    "Please remember this project name and exact code for future conversations."
                )
                result = json.loads(
                    provider.handle_tool_call("viking_remember", {"content": content})
                )
                assert result.get("status") == "submitted", result
                report = {
                    "marker": marker,
                    "session-id": result["session_id"],
                    "task-id": result.get("task_id"),
                }
                args.report.write_text(json.dumps(report, indent=2) + "\n")
                print(
                    json.dumps(
                        {"submission": "accepted", "task-id": report["task-id"]}
                    ),
                    flush=True,
                )
                return

            report = json.loads(args.report.read_text())
            deadline = time.monotonic() + args.timeout
            last = "pending"
            while time.monotonic() < deadline:
                if report.get("task-id") and not report.get("extraction-completed"):
                    response = api.get("/api/v1/tasks/" + report["task-id"])
                    response.raise_for_status()
                    task = response.json()["result"]
                    last = task.get("status", "unknown")
                    assert last not in ("failed", "cancelled"), {
                        "extraction-status": last
                    }
                    if last != "completed":
                        time.sleep(5)
                        continue
                    report["extraction-completed"] = True
                result = json.loads(
                    provider.handle_tool_call(
                        "viking_search",
                        {
                            "query": "What is my Aurora Cedar project release checklist code?",
                            "limit": 10,
                        },
                    )
                )
                for item in result.get("results", []):
                    memory = json.loads(
                        provider.handle_tool_call(
                            "viking_read",
                            {
                                "uri": item["uri"],
                                "level": "full",
                            },
                        )
                    )
                    if report["marker"] in json.dumps(memory):
                        report["memory-uri"] = item["uri"]
                        args.report.write_text(json.dumps(report, indent=2) + "\n")
                        print(
                            json.dumps(
                                {"fresh-session-recall": "passed", "uri": item["uri"]}
                            )
                        )
                        return
                time.sleep(5)
            raise AssertionError(
                f"Recall did not complete before deadline; extraction={last}"
            )
        finally:
            provider.shutdown()


if __name__ == "__main__":
    main()

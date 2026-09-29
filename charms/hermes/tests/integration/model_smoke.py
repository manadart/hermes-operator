#!/usr/bin/env python3
"""Run inside the workload as hermes; makes real, billable model requests.

Run once to check inference, terminal/file tools, and a single delegated task.
Only a new scratch directory and dedicated sessions are used. Reports and
transcripts are retained for inspection; verify makes no model requests.
Resume reuses recorded replies and can complete an existing asynchronous delegation.
"""

import argparse
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8642"
STATE = Path("/var/lib/hermes")
MODEL = "z-ai/glm-5.3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "verify", "resume"])
    parser.add_argument("--report", type=Path, help="Existing report for verify/resume")
    args = parser.parse_args()
    if args.mode != "run" and args.report is None:
        parser.error(f"{args.mode} requires --report")
    os.umask(0o077)
    credentials = {
        name: json.loads(value)
        for name, value in (
            line.split("=", 1) for line in (STATE / ".env").read_text().splitlines()
        )
    }

    def redact(value):
        text = json.dumps(value)
        for secret in credentials.values():
            text = text.replace(secret, "[REDACTED]")
        return json.loads(text)

    def request(path, body=None):
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + credentials["API_SERVER_KEY"],
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = redact(exc.read().decode(errors="replace"))
            raise RuntimeError(f"HTTP {exc.code}: {detail[:2000]}") from None

    def messages(session_id):
        return request(f"/api/sessions/{session_id}/messages?limit=500&order=oldest")["data"]

    def tool_names(transcript):
        return [m["tool_name"] for m in transcript if m.get("role") == "tool"]

    def chat(session_id, prompt, instructions):
        return request(
            f"/api/sessions/{session_id}/chat",
            {
                "message": prompt,
                "instructions": instructions,
                "model": MODEL,
                "provider": "openrouter",
                "require_model_lock": True,
                "model_options": {"reasoning": {"effort": "low"}},
            },
        )

    if args.mode == "verify":
        report = json.loads(args.report.read_text())
        assert report["result"] == "passed"
        assert Path(report["scratch_file"]).read_text() == report["marker"] + "\n"
        for check in report["checks"]:
            transcript = messages(check["session_id"])
            assert any(
                m["role"] == "assistant" and m.get("content") == check["response"]
                for m in transcript
            )
            assert tool_names(transcript) == check["tool_names"]
        print(json.dumps({"result": "passed", "mode": "verify", "report": str(args.report)}))
        return

    if args.mode == "resume":
        report_path = args.report
        report = json.loads(report_path.read_text())
        scratch = report_path.parent
        scratch_file = Path(report["scratch_file"])
        marker = report["marker"]
    else:
        scratch = Path(tempfile.mkdtemp(prefix="charm-model-smoke-", dir=STATE / "workspace"))
        scratch_file = scratch / "tool-result.txt"
        marker = "HERMES_TOOL_" + uuid.uuid4().hex
        report_path = scratch / "report.json"
        report = {
            "result": "running",
            "scratch_file": str(scratch_file),
            "marker": marker,
            "checks": [],
        }

    def save():
        report_path.write_text(json.dumps(redact(report), indent=2) + "\n")

    save()
    print(json.dumps({"report": str(report_path)}), flush=True)
    instructions = (
        "This is a small deployment acceptance test. Follow only the requested task. "
        f"Any file access or terminal command must be restricted to {scratch}. "
        "Do not inspect credentials, environment variables, other files, memory, or skills. "
        "Do not make network requests or install anything. Do not save memories or skills. "
        "Use at most four tool calls per request and create a subagent only when requested. "
        "If a required tool fails or needs approval, report that and stop without trying "
        "another method. Keep replies short."
    )
    tasks = [
        ("inference", "Reply with exactly HERMES_GLM_READY. Do not call any tools."),
        (
            "tools",
            f"Use write_file to put {marker!r} followed by one newline in "
            f"{str(scratch_file)!r}. Use terminal to run only 'cat {scratch_file}'. "
            "Then use read_file on that same file to verify its contents. "
            "Reply with exactly HERMES_TOOLS_READY after all three tools succeed.",
        ),
        (
            "delegation",
            "Use delegate_task to create exactly one child agent whose only task is to "
            "compute 17 times 19 and return the integer without calling tools or creating "
            "further children. Limit the child to two iterations if supported. Wait for "
            "its result. Report HERMES_DELEGATION_READY followed by the returned integer. "
            "Do not answer the arithmetic yourself without actually delegating.",
        ),
    ]
    for name, prompt in tasks:
        check = next((c for c in report["checks"] if c["name"] == name), None)
        if check is None:
            session_id = "charm_model_smoke_" + uuid.uuid4().hex
            request(
                "/api/sessions", {"id": session_id, "title": f"Charm smoke: {name} {session_id}"}
            )
            check = {"name": name, "session_id": session_id}
            report["checks"].append(check)
        else:
            session_id = check["session_id"]
            if "response" not in check:
                raise SystemExit(
                    "An interrupted request has no recorded reply; inspect its session"
                )
        save()
        print(json.dumps({"check": name, "state": "running"}), flush=True)
        started = time.monotonic()
        try:
            if "response" not in check:
                response = chat(session_id, prompt, instructions)
                check.update(
                    response=response["message"]["content"],
                    runtime=response["runtime"],
                    usage=response["usage"],
                    seconds=round(time.monotonic() - started, 2),
                    tool_names=tool_names(messages(session_id)),
                )
            save()
            assert check["runtime"]["provider"] == "openrouter", check["runtime"]
            assert check["runtime"]["model"] == MODEL, check["runtime"]
            assert check["runtime"]["model_lock"] == "confirmed", check["runtime"]
            if name == "inference":
                assert check["response"].strip() == "HERMES_GLM_READY", check["response"]
                assert not check["tool_names"], check["tool_names"]
            elif name == "tools":
                assert scratch_file.read_text() == marker + "\n"
                assert {"write_file", "terminal", "read_file"} <= set(check["tool_names"])
                for message in messages(session_id):
                    if message["role"] == "tool":
                        result = json.loads(message["content"])
                        assert not result.get("error"), result
                        assert not result.get("approval_pending"), result
                        if message["tool_name"] == "terminal":
                            assert result["exit_code"] == 0, result
                assert "HERMES_TOOLS_READY" in check["response"], check["response"]
            else:
                assert check["tool_names"] == ["delegate_task"], check["tool_names"]
                dispatch = next(
                    json.loads(m["content"])
                    for m in messages(session_id)
                    if m.get("tool_name") == "delegate_task"
                )
                assert dispatch["status"] == "dispatched" and dispatch["count"] == 1, dispatch
                check["delegation_id"] = dispatch["delegation_id"]
                # Native API sessions persist the child's asynchronous result in history.
                # Poll that history, then give the parent a turn to consume the result.
                deadline = time.monotonic() + 60
                while True:
                    completions = [
                        m["content"]
                        for m in messages(session_id)
                        if m["role"] == "user"
                        and (m.get("content") or "").startswith("[ASYNC DELEGATION BATCH COMPLETE")
                        and check["delegation_id"] in m["content"]
                    ]
                    if completions:
                        assert "status=completed" in completions[-1], completions[-1]
                        child_result = completions[-1].split(" ---\n", 1)[1]
                        child_result = child_result.split("\nFull live transcript", 1)[0].strip()
                        assert re.search(r"\b323\b", child_result), child_result
                        check["child_result"] = child_result
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Child result did not arrive in the parent session")
                    time.sleep(1)
                if not re.search(r"HERMES_DELEGATION_READY\s+323\b", check["response"]):
                    check["dispatch_response"] = check["response"]
                    followup = chat(
                        session_id,
                        "The child's completion is now in this session's history. Read its "
                        "returned result and reply HERMES_DELEGATION_READY followed by that "
                        "integer. Do not call any tools or spawn more children.",
                        instructions,
                    )
                    check["response"] = followup["message"]["content"]
                    check["followup_usage"] = followup["usage"]
                assert tool_names(messages(session_id)) == ["delegate_task"]
                assert re.search(r"HERMES_DELEGATION_READY\s+323\b", check["response"])
                check["seconds"] = round(time.monotonic() - started, 2)
            check.pop("error", None)
            print(json.dumps(redact({"result": "passed", **check})), flush=True)
        except Exception as exc:
            report["result"] = "failed"
            check["error"] = str(exc)
            save()
            raise SystemExit(json.dumps(redact({"result": "failed", **check}))) from None
    report["result"] = "passed"
    save()
    print(json.dumps({"result": "passed", "mode": args.mode, "report": str(report_path)}))


if __name__ == "__main__":
    main()

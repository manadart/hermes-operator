#!/usr/bin/env python3
"""Prompt locally for an OpenRouter key and pass it to Juju through a private file."""

import argparse
import getpass
import json
import os
import re
import subprocess
import tempfile


def juju(*args: str) -> str:
    return subprocess.run(
        ["juju", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="hermes:hermes-dev")
    parser.add_argument("--application", default="hermes")
    parser.add_argument("--name", default="openrouter")
    parser.add_argument("--update", action="store_true", help="Update the existing named secret")
    args = parser.parse_args()
    key = getpass.getpass("OpenRouter API key (hidden): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,}", key):
        parser.error("Enter a non-empty OpenRouter API key without whitespace")

    # mkstemp uses mode 0600. The value never appears in argv or shell history.
    fd, path = tempfile.mkstemp(prefix="hermes-openrouter-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump({"api-key": key}, out)
        if args.update:
            juju("update-secret", "--model", args.model, args.name, "--file", path)
            metadata = json.loads(
                juju("show-secret", "--model", args.model, args.name, "--format", "json")
            )
            secret_id = next(iter(metadata))
            if not secret_id.startswith("secret:"):
                secret_id = "secret:" + secret_id
        else:
            secret_id = juju("add-secret", "--model", args.model, args.name, "--file", path)
        juju("grant-secret", "--model", args.model, secret_id, args.application)
        juju("config", "--model", args.model, args.application, f"openrouter-secret={secret_id}")
    except subprocess.CalledProcessError as exc:
        # Commands only receive a file path or secret URI, never its contents.
        parser.exit(1, f"Juju command failed: {exc.stderr.strip()}\n")
    finally:
        os.unlink(path)
    print(f"Configured {args.application} in {args.model} using {secret_id}.")


if __name__ == "__main__":
    main()

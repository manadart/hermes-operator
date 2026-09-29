"""Run gh as the configured GitHub App installation, without storing access tokens."""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from github_app import GitHubApp

CONFIG = Path("/var/lib/hermes/github-app.json")
KEY = Path("/var/lib/hermes/gh-app-key.pem")
GH = "/usr/bin/gh"


class AuthenticationError(Exception):
    """A diagnostic safe to print without credentials or HTTP response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a signed App JWT to another endpoint.
        raise AuthenticationError("GitHub token endpoint returned an unexpected redirect")


def _base64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def installation_token() -> str:
    """Read the current key for each invocation and exchange a short-lived JWT."""
    try:
        config = json.loads(CONFIG.read_text())
        app = GitHubApp.from_secret(config["app_id"], config["installation_id"], KEY.read_text())
    except (OSError, ValueError, KeyError, TypeError):
        raise AuthenticationError(
            "Configure a valid github-app-secret and GitHub App IDs"
        ) from None
    now = int(time.time())
    header = _base64url(b'{"alg":"RS256","typ":"JWT"}')
    claims = _base64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app.app_id}).encode())
    unsigned = header + b"." + claims
    key = serialization.load_pem_private_key(app.private_key.encode(), password=None)
    signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
    jwt = (unsigned + b"." + _base64url(signature)).decode()
    request = urllib.request.Request(
        f"https://api.github.com/app/installations/{app.installation_id}/access_tokens",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "hermes-operator",
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 201:
                raise AuthenticationError("GitHub token endpoint returned an unexpected status")
            result = json.load(response)
        token = result["token"]
        if (
            not isinstance(token, str)
            or not token
            or any(c.isspace() for c in token)
            or "\0" in token
        ):
            raise ValueError("invalid token")
        return token
    except urllib.error.HTTPError as exc:
        raise AuthenticationError(
            f"GitHub rejected App authentication (HTTP {exc.code}); check the key and installation"
        ) from None
    except (OSError, ValueError, KeyError, TypeError):
        raise AuthenticationError("Could not obtain a GitHub App installation token") from None


def main() -> int:
    args = sys.argv[1:]
    env = os.environ.copy()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(name, None)
    env.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1")
    try:
        if args and args not in (["--version"], ["version"], ["--help"], ["help"]):
            env["GH_TOKEN"] = installation_token()
        os.execve(GH, [GH, *args], env)
    except AuthenticationError as exc:
        print(f"gh: {exc}", file=sys.stderr)
    except OSError:
        print("gh: could not execute the installed GitHub CLI", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

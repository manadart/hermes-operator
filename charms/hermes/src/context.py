"""Validated OpenViking connection supplied by a Juju relation."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError("Context endpoint redirect rejected")


@dataclass(frozen=True)
class ContextConnection:
    endpoint: str
    identity: str
    api_key: str = field(repr=False)

    @classmethod
    def from_relation(cls, data, api_key: str, identity: str):
        if data.get("schema-version") != "1":
            raise ValueError("Unsupported context relation schema version")
        if data.get("context-id") != identity:
            raise ValueError("Context provider returned a different context-id")
        endpoint = data.get("endpoint", "").rstrip("/")
        url = urllib.parse.urlsplit(endpoint)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path
            or any(c.isspace() for c in endpoint)
        ):
            raise ValueError("Context provider returned an invalid endpoint")
        try:
            _ = url.port
        except ValueError:
            raise ValueError("Context provider returned an invalid endpoint") from None
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{16,}", api_key):
            raise ValueError("Context credential secret must contain a valid api-key")
        return cls(endpoint, identity, api_key)

    def available(self) -> bool:
        request = urllib.request.Request(
            self.endpoint + "/api/v1/fs/ls?uri=viking%3A%2F%2Fuser%2Fhermes%2Fmemories%2F",
            headers={"X-API-Key": self.api_key},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
                data = json.load(response)
                return (
                    response.status == 200 and isinstance(data, dict) and data.get("status") == "ok"
                )
        except (OSError, ValueError, urllib.error.URLError):
            return False

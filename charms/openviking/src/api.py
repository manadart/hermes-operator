"""Small OpenViking administrative client; credentials never enter exceptions."""

import json
import urllib.error
import urllib.parse
import urllib.request


class APIError(Exception):
    def __init__(self, status: int = 0):
        self.status = status
        super().__init__(
            f"OpenViking request failed (HTTP {status})" if status else "OpenViking unavailable"
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise APIError(code)


class OpenVikingAPI:
    def __init__(self, key: str, endpoint: str = "http://127.0.0.1:1933"):
        self._key = key
        self.endpoint = endpoint.rstrip("/")

    def request(self, method: str, path: str, data=None):
        request = urllib.request.Request(
            self.endpoint + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={"X-API-Key": self._key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                result = json.load(response)
            if not isinstance(result, dict) or result.get("status") != "ok":
                raise APIError()
            return result["result"]
        except urllib.error.HTTPError as exc:
            raise APIError(exc.code) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise APIError() from None

    def ensure_client(self, account_id: str) -> str:
        """Retry safely after partial provisioning, recovering the existing client key."""
        try:
            users = self.request("GET", f"/api/v1/admin/accounts/{account_id}/users")
        except APIError as exc:
            if exc.status != 404:
                raise
            try:
                self.request(
                    "POST",
                    "/api/v1/admin/accounts",
                    {"account_id": account_id, "admin_user_id": "operator"},
                )
            except APIError as create_error:
                if create_error.status != 409:
                    raise
            users = self.request("GET", f"/api/v1/admin/accounts/{account_id}/users")
        for user in users:
            if user["user_id"] == "hermes":
                if user["role"].lower() != "user":
                    raise APIError()
                return user["api_key"]
        try:
            return self.request(
                "POST",
                f"/api/v1/admin/accounts/{account_id}/users",
                {"user_id": "hermes", "role": "user"},
            )["user_key"]
        except APIError as exc:
            if exc.status != 409:
                raise
            # A previous request may have completed despite a lost response.
            users = self.request("GET", f"/api/v1/admin/accounts/{account_id}/users")
            for user in users:
                if user["user_id"] == "hermes" and user["role"].lower() == "user":
                    return user["api_key"]
            raise APIError() from None

    def rotate(self, account_id: str) -> str:
        return self.request("POST", f"/api/v1/admin/accounts/{account_id}/users/hermes/key", {})[
            "user_key"
        ]

    def check_client(self, key: str) -> None:
        OpenVikingAPI(key, self.endpoint).request(
            "GET",
            "/api/v1/fs/ls?" + urllib.parse.urlencode({"uri": "viking://user/hermes/memories/"}),
        )

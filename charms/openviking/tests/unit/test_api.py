"""Recovery from lost create responses must not issue new identities."""

import pytest

from api import APIError, OpenVikingAPI


def test_account_and_user_create_recovery(mocker):
    api = OpenVikingAPI("root-key")
    request = mocker.patch.object(
        api,
        "request",
        side_effect=[
            APIError(404),
            APIError(409),
            [],
            APIError(409),
            [{"user_id": "hermes", "role": "user", "api_key": "recovered-key"}],
        ],
    )
    assert api.ensure_client("account") == "recovered-key"
    assert request.call_count == 5


def test_existing_admin_is_never_handed_to_consumer(mocker):
    api = OpenVikingAPI("root-key")
    mocker.patch.object(
        api,
        "request",
        return_value=[
            {"user_id": "hermes", "role": "admin", "api_key": "privileged-key"},
        ],
    )
    with pytest.raises(APIError):
        api.ensure_client("account")


def test_network_failure_does_not_leak_credential(mocker):
    mocker.patch("api.urllib.request.build_opener").return_value.open.side_effect = OSError(
        "secret-value"
    )
    with pytest.raises(APIError) as exc:
        OpenVikingAPI("secret-value").request("GET", "/api/v1/admin/accounts")
    assert "secret-value" not in str(exc.value)

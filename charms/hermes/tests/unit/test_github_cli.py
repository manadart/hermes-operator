"""Check the signed exchange and gh process boundary without calling GitHub."""

import base64
import io
import json
import urllib.error

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

import github_cli


@pytest.fixture
def credentials(tmp_path, monkeypatch, github_pems):
    config = tmp_path / "github-app.json"
    key = tmp_path / "key.pem"
    config.write_text(json.dumps({"app_id": "4245402", "installation_id": "145176996"}))
    key.write_text(github_pems[0])
    monkeypatch.setattr(github_cli, "CONFIG", config)
    monkeypatch.setattr(github_cli, "KEY", key)
    return key


def response(mocker, body, status=201):
    result = io.BytesIO(json.dumps(body).encode())
    result.status = status
    opener = mocker.patch("github_cli.urllib.request.build_opener").return_value
    opener.open.return_value = result
    return opener.open


def test_exchange_signs_current_key_and_does_not_cache(
    credentials, github_keys, github_pems, mocker
):
    mocker.patch("github_cli.time.time", return_value=1800000000)
    for index, key in enumerate(github_keys):
        credentials.write_text(github_pems[index])
        open_request = response(mocker, {"token": f"ghs_4245402.test-token-{index}"})
        assert github_cli.installation_token() == f"ghs_4245402.test-token-{index}"
        request = open_request.call_args.args[0]
        assert (
            request.full_url == "https://api.github.com/app/installations/145176996/access_tokens"
        )
        assert request.method == "POST"
        assert request.data == b"{}"
        assert open_request.call_args.kwargs["timeout"] == 30
        jwt = request.get_header("Authorization").removeprefix("Bearer ")
        header, claims, signature = jwt.split(".")
        assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "RS256", "typ": "JWT"}
        assert json.loads(base64.urlsafe_b64decode(claims + "==")) == {
            "iat": 1799999940,
            "exp": 1800000540,
            "iss": "4245402",
        }
        key.public_key().verify(
            base64.urlsafe_b64decode(signature + "=="),
            f"{header}.{claims}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "token\n"}, {"token": None}, []])
def test_bad_responses_do_not_disclose_credentials(credentials, mocker, body):
    response(mocker, body)
    with pytest.raises(github_cli.AuthenticationError, match="Could not obtain"):
        github_cli.installation_token()


def test_http_failure_does_not_print_response_body(credentials, mocker, capsys):
    mocker.patch("github_cli.sys.argv", ["gh", "api", "/installation/repositories"])
    opener = mocker.patch("github_cli.urllib.request.build_opener").return_value
    opener.open.side_effect = urllib.error.HTTPError(
        "https://api.github.com", 401, "sensitive response", {}, io.BytesIO(b"sensitive body")
    )
    execute = mocker.patch("github_cli.os.execve")
    assert github_cli.main() == 1
    assert "HTTP 401" in capsys.readouterr().err
    execute.assert_not_called()


def test_redirect_is_rejected_before_forwarding_jwt():
    with pytest.raises(github_cli.AuthenticationError, match="unexpected redirect"):
        github_cli.NoRedirect().redirect_request(None, None, 307, "", {}, "https://other.test")


class Executed(BaseException):
    """Model successful execve without returning to the wrapper."""


def test_gh_gets_fresh_token_in_environment_not_argv(credentials, mocker, monkeypatch, capsys):
    response(mocker, {"token": "ghs_4245402.ephemeral-test-token"})
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(name, "unrelated-personal-token")
    monkeypatch.setenv("GH_HOST", "other.example")
    mocker.patch("github_cli.sys.argv", ["gh", "pr", "list", "--repo", "owner/repo"])
    execute = mocker.patch("github_cli.os.execve", side_effect=Executed)
    with pytest.raises(Executed):
        github_cli.main()
    binary, args, env = execute.call_args.args
    assert binary == "/usr/bin/gh"
    assert args == [binary, "pr", "list", "--repo", "owner/repo"]
    assert env["GH_TOKEN"] == "ghs_4245402.ephemeral-test-token"
    assert env["GH_HOST"] == "github.com"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert "GITHUB_TOKEN" not in env and "GH_ENTERPRISE_TOKEN" not in env
    assert "GITHUB_ENTERPRISE_TOKEN" not in env
    assert capsys.readouterr() == ("", "")


def test_removed_key_fails_without_falling_back_to_personal_token(
    credentials, monkeypatch, mocker, capsys
):
    credentials.unlink()
    monkeypatch.setenv("GH_TOKEN", "personal-token")
    mocker.patch("github_cli.sys.argv", ["gh", "pr", "list"])
    execute = mocker.patch("github_cli.os.execve")
    assert github_cli.main() == 1
    execute.assert_not_called()
    assert "Configure a valid github-app-secret" in capsys.readouterr().err


def test_version_works_without_credentials_or_network(mocker):
    mocker.patch("github_cli.sys.argv", ["gh", "--version"])
    token = mocker.patch("github_cli.installation_token")
    mocker.patch("github_cli.os.execve", side_effect=Executed)
    with pytest.raises(Executed):
        github_cli.main()
    token.assert_not_called()

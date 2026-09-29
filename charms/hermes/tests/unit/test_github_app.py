"""Validate the GitHub App key contract without contacting GitHub."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from github_app import GitHubApp


@pytest.mark.parametrize(
    "format", [serialization.PrivateFormat.TraditionalOpenSSL, serialization.PrivateFormat.PKCS8]
)
def test_accepts_rsa_pem_and_keeps_key_out_of_repr(github_keys, format):
    pem = (
        github_keys[0]
        .private_bytes(serialization.Encoding.PEM, format, serialization.NoEncryption())
        .decode()
    )
    app = GitHubApp.from_secret("4245402", "145176996", pem.replace("\n", "\r\n"))
    assert app.private_key == pem
    assert pem not in repr(app)
    assert "PRIVATE KEY" not in repr(app)


@pytest.mark.parametrize("pem", ["", "not a key", "-----BEGIN CERTIFICATE-----\ninvalid\n"])
def test_rejects_invalid_key_without_echoing_content(pem):
    with pytest.raises(ValueError, match="unencrypted RSA private key") as exc:
        GitHubApp.from_secret("4245402", "145176996", pem)
    if pem:
        assert pem not in str(exc.value)


def test_rejects_encrypted_key(github_keys):
    pem = (
        github_keys[0]
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"test-password"),
        )
        .decode()
    )
    with pytest.raises(ValueError, match="unencrypted RSA private key"):
        GitHubApp.from_secret("4245402", "145176996", pem)


def test_rejects_non_rsa_key():
    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(ValueError, match="unencrypted RSA private key"):
        GitHubApp.from_secret("4245402", "145176996", pem)

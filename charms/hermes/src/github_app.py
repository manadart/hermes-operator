"""Validated GitHub App configuration; private key material stays out of reprs."""

import re
from dataclasses import dataclass, field

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass(frozen=True)
class GitHubApp:
    app_id: str
    installation_id: str
    private_key: str = field(repr=False)

    @classmethod
    def from_secret(cls, app_id: str, installation_id: str, private_key: str) -> "GitHubApp":
        for name, value in (
            ("github-app-id", app_id),
            ("github-installation-id", installation_id),
        ):
            if not re.fullmatch(r"[1-9][0-9]*", value):
                raise ValueError(f"Set {name} to a positive numeric ID")
        error = (
            "github-app-secret must contain private-key: "
            "an unencrypted RSA private key in PEM format"
        )
        if not private_key or len(private_key) > 32768:
            raise ValueError(error)
        try:
            key = serialization.load_pem_private_key(private_key.encode(), password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            raise ValueError(error) from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError(error)
        return cls(app_id, installation_id, private_key.replace("\r\n", "\n").strip() + "\n")

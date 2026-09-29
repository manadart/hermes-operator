"""Reject malformed relation endpoints before sending a credential."""

import pytest

from context import ContextConnection


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///etc/passwd",
        "http://user:password@host:1933",
        "http://host/path",
        "http://host?token=abc",
        "http://host#fragment",
        "http://host:invalid",
        "http://host:65536",
        "http://host\nother",
    ],
)
def test_invalid_endpoint(endpoint):
    with pytest.raises(ValueError):
        ContextConnection.from_relation(
            {"schema-version": "1", "context-id": "agent", "endpoint": endpoint},
            "client-test-key-1234",
            "agent",
        )


def test_different_identity_rejected():
    with pytest.raises(ValueError, match="different context-id"):
        ContextConnection.from_relation(
            {"schema-version": "1", "context-id": "other", "endpoint": "http://host"},
            "client-test-key-1234",
            "agent",
        )

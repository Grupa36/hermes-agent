"""Email allowlist entries ``@domain`` / ``*@domain`` admit every address at exactly that domain."""

import pytest

from gateway.config import Platform
from gateway.session import SessionSource


def _source(user_id: str) -> SessionSource:
    return SessionSource(platform=Platform.EMAIL, user_id=user_id, chat_id=user_id, user_name="tester", chat_type="dm")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.pairing_store = None
    return runner


@pytest.mark.parametrize(
    ("user_id", "allowed"),
    [
        ("alice@corp.example", True),
        ("Bob@Corp.Example", True),
        ("carol@other.example", True),
        ("alice@evilcorp.example", False),
        ("alice@corp.example.evil", False),
        ("alice@sub.corp.example", False),
        ("mallory@evil.example", False),
    ],
)
def test_domain_entries(monkeypatch, user_id, allowed):
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "@corp.example,*@other.example")
    assert _make_runner()._is_user_authorized(_source(user_id)) is allowed

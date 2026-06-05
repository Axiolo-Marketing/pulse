"""Unit tests for the generic signed-token helpers in `auth.tokens`.

The cross-purpose isolation test is the most important one — two tokens
signed with the same secret but different `purpose` salts must not be
redeemable against each other.
"""
import pytest
from freezegun import freeze_time

from pulse_api.auth.session import InvalidSessionError
from pulse_api.auth.tokens import consume_token, issue_token


def test_roundtrip_returns_payload() -> None:
    t = issue_token("email-verify", {"user_id": "abc", "extra": 1})
    assert consume_token("email-verify", t, 3600) == {"user_id": "abc", "extra": 1}


def test_tampered_token_rejected() -> None:
    t = issue_token("email-verify", {"user_id": "abc"})
    # Replace the entire HMAC signature segment with a same-length string
    # of `X`s. A single-char flip occasionally lands on a position where
    # itsdangerous still accepts the token; corrupting the whole sig
    # avoids that flakiness.
    head, _, sig = t.rpartition(".")
    tampered = f"{head}.{'X' * len(sig)}"
    with pytest.raises(InvalidSessionError):
        consume_token("email-verify", tampered, 3600)


def test_expired_token_rejected() -> None:
    with freeze_time("2026-05-13 12:00:00"):
        t = issue_token("password-reset", {"user_id": "abc"})
    with freeze_time("2026-05-13 14:00:00"):
        with pytest.raises(InvalidSessionError) as exc_info:
            consume_token("password-reset", t, 3600)
        assert "expired" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "issued_for, redeemed_as",
    [
        ("email-verify", "password-reset"),
        ("password-reset", "email-verify"),
        ("email-verify", "oauth-state"),
        ("oauth-state", "email-verify"),
        ("password-reset", "oauth-state"),
    ],
)
def test_purpose_salt_prevents_cross_use(issued_for: str, redeemed_as: str) -> None:
    """A token signed for purpose A must not be redeemable as purpose B,
    even though the signing key is identical. The per-purpose salt is what
    enforces this."""
    t = issue_token(issued_for, {"user_id": "abc"})
    with pytest.raises(InvalidSessionError):
        consume_token(redeemed_as, t, 3600)


def test_garbage_input_rejected() -> None:
    with pytest.raises(InvalidSessionError):
        consume_token("email-verify", "not.a.real.token", 3600)


def test_non_dict_payload_rejected() -> None:
    """The helpers refuse non-dict payloads so downstream code can rely on
    indexing the result with string keys."""
    # itsdangerous serializes whatever we give it, but consume_token checks
    # the decoded shape.
    from itsdangerous import URLSafeTimedSerializer

    from pulse_api.config import settings

    serializer = URLSafeTimedSerializer(settings.session_secret, salt="pulse-email-verify")
    bad_token = serializer.dumps("just a string, not a dict")
    with pytest.raises(InvalidSessionError):
        consume_token("email-verify", bad_token, 3600)

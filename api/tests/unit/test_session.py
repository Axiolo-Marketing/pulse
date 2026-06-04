"""Unit tests for signed-cookie session encoding/decoding. No DB.

Expiry is tested with freezegun: sign at T0, advance the clock past
max_age, decode must fail with SignatureExpired (mapped to
InvalidSessionError).
"""
import pytest
from freezegun import freeze_time

from pulse_api.auth.session import (
    InvalidSessionError,
    decode_session,
    encode_session,
)


def test_roundtrip_returns_same_user_id() -> None:
    user_id = "0c4f6b09-3e0c-4b5f-9b2b-2cf12e23c1a0"
    token = encode_session(user_id)
    assert decode_session(token, max_age_seconds=3600) == user_id


def test_tampered_token_rejected() -> None:
    token = encode_session("anything")
    # Replace the entire HMAC signature segment with a same-length string
    # of `X`s. A single-char flip occasionally lands on a position where
    # itsdangerous still accepts the token; corrupting the whole sig
    # is reliably rejected.
    head, _, sig = token.rpartition(".")
    tampered = f"{head}.{'X' * len(sig)}"
    with pytest.raises(InvalidSessionError):
        decode_session(tampered, max_age_seconds=3600)


def test_garbage_token_rejected() -> None:
    with pytest.raises(InvalidSessionError):
        decode_session("not.a.real.token", max_age_seconds=3600)


def test_expired_token_rejected() -> None:
    with freeze_time("2026-05-13 12:00:00"):
        token = encode_session("user-1")
    # 2 hours later — token signed with max_age=1h is expired
    with freeze_time("2026-05-13 14:00:00"):
        with pytest.raises(InvalidSessionError) as exc_info:
            decode_session(token, max_age_seconds=3600)
        assert "expired" in str(exc_info.value).lower()


def test_fresh_token_at_edge_of_window_accepted() -> None:
    with freeze_time("2026-05-13 12:00:00"):
        token = encode_session("user-2")
    # 59 minutes later — still inside max_age=1h
    with freeze_time("2026-05-13 12:59:00"):
        assert decode_session(token, max_age_seconds=3600) == "user-2"


@pytest.mark.parametrize(
    "max_age, ok",
    [
        (60, False),     # 60s window, signed 1h ago → expired
        (3600, True),    # 1h window, signed 1h ago — accepted (max_age inclusive)
        (7200, True),    # 2h window — accepted
    ],
)
def test_max_age_parametrized(max_age: int, ok: bool) -> None:
    with freeze_time("2026-05-13 12:00:00"):
        token = encode_session("u")
    with freeze_time("2026-05-13 13:00:00"):
        if ok:
            assert decode_session(token, max_age_seconds=max_age) == "u"
        else:
            with pytest.raises(InvalidSessionError):
                decode_session(token, max_age_seconds=max_age)

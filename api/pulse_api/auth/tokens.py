"""Signed timed-token helpers for one-off purposes (email verification,
password reset, OAuth state).

Each purpose gets its own salt so a token issued for one purpose can't be
consumed for another, even with the same signing key. `max_age_seconds` is
enforced at consume time — the timestamp is embedded in the token.
"""
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from pulse_api.auth.session import InvalidSessionError
from pulse_api.config import settings


def _serializer(purpose: str) -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set; refusing to sign tokens.")
    return URLSafeTimedSerializer(settings.session_secret, salt=f"pulse-{purpose}")


def issue_token(purpose: str, payload: dict) -> str:
    return _serializer(purpose).dumps(payload)


def consume_token(purpose: str, token: str, max_age_seconds: int) -> dict:
    """Return the decoded payload, or raise InvalidSessionError."""
    try:
        data = _serializer(purpose).loads(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise InvalidSessionError("token expired") from exc
    except BadSignature as exc:
        raise InvalidSessionError("invalid token") from exc
    if not isinstance(data, dict):
        raise InvalidSessionError("malformed payload")
    return data

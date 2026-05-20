"""Signed-cookie session tokens.

Payload is just the user_id. The signing key (SESSION_SECRET) ensures the
token can't be forged; the embedded timestamp + max_age check on decode
gives us expiry. No server-side session store — the browser's cookie IS
the session.

`URLSafeTimedSerializer` produces base64-url-safe tokens with embedded
timestamps; passing `max_age` on `loads()` enforces expiry.
"""
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from pulse_api.config import settings


class InvalidSessionError(Exception):
    """Raised when a cookie is missing, tampered, or expired."""


def _serializer() -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set; refusing to sign sessions.")
    return URLSafeTimedSerializer(settings.session_secret, salt="pulse-session")


def encode_session(user_id: str) -> str:
    """Sign and return an opaque session token carrying user_id."""
    return _serializer().dumps({"user_id": user_id})


def decode_session(token: str, max_age_seconds: int) -> str:
    """Return user_id from a valid, non-expired token. Raises InvalidSessionError otherwise."""
    try:
        data = _serializer().loads(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise InvalidSessionError("session expired") from exc
    except BadSignature as exc:
        raise InvalidSessionError("invalid signature") from exc
    if not isinstance(data, dict) or "user_id" not in data:
        raise InvalidSessionError("malformed payload")
    return data["user_id"]

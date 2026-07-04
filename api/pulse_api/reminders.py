"""Shared helpers for the recipient invite/reminder emails.

The unsubscribe link carries a signed, per-recipient token (its own salt,
so it can't be redeemed as a session/verify/reset token even with the same
``SESSION_SECRET``). Unsubscribe links shouldn't rot, so the max-age is
effectively forever (~10 years).
"""
from pulse_api.auth.session import InvalidSessionError
from pulse_api.auth.tokens import consume_token, issue_token
from pulse_api.config import settings

_UNSUBSCRIBE_PURPOSE = "reminder-unsubscribe"
_UNSUBSCRIBE_MAX_AGE = 3650 * 24 * 3600  # ~10 years — links never practically expire


def deck_url(token: str) -> str:
    """The recipient's private ``?t=`` deck link."""
    return f"{settings.frontend_base_url.rstrip('/')}/?t={token}"


def unsubscribe_url(recipient_id: str) -> str:
    """A signed unsubscribe link for one recipient, pointing at the
    ``/unsubscribe`` page which POSTs the token back to the API."""
    tok = issue_token(_UNSUBSCRIBE_PURPOSE, {"rid": recipient_id})
    return f"{settings.frontend_base_url.rstrip('/')}/unsubscribe?u={tok}"


def parse_unsubscribe_token(token: str) -> str | None:
    """Return the recipient id encoded in an unsubscribe token, or ``None``
    if the token is missing, malformed, expired, or signed for another
    purpose."""
    try:
        data = consume_token(_UNSUBSCRIBE_PURPOSE, token, _UNSUBSCRIBE_MAX_AGE)
    except InvalidSessionError:
        return None
    rid = data.get("rid")
    return str(rid) if rid else None

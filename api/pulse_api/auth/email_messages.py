"""Email body templates for auth flows. Kept text-only and short — these
are transactional and shouldn't try to do marketing.
"""
from pulse_api.config import settings


def verification_email(token: str, name: str | None = None) -> tuple[str, str]:
    """Returns (subject, body) for the email-verification message."""
    link = f"{settings.frontend_base_url.rstrip('/')}/verify-email?token={token}"
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Verify your Pulse email"
    body = (
        f"{greeting}\n\n"
        f"Click the link below to verify your email and finish signing up:\n\n"
        f"{link}\n\n"
        f"The link expires in 7 days. If you didn't sign up for Pulse, you can ignore this email."
    )
    return subject, body


def password_reset_email(token: str, name: str | None = None) -> tuple[str, str]:
    link = f"{settings.frontend_base_url.rstrip('/')}/reset-password?token={token}"
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Reset your Pulse password"
    body = (
        f"{greeting}\n\n"
        f"Click the link below to choose a new password:\n\n"
        f"{link}\n\n"
        f"The link expires in 1 hour. If you didn't request a password reset, you can ignore this email."
    )
    return subject, body


def org_invite_email(
    token: str,
    *,
    org_name: str,
    inviter_name: str | None,
    role: str,
) -> tuple[str, str]:
    """Returns ``(subject, body)`` for an organization invite message.

    The signed ``token`` is the only piece of secret material — it lives
    in the URL the recipient clicks and is the proof the inviter put
    them on the invite list. The DB stores only its SHA-256 hash.

    Args:
        token: Signed ``pulse-org-invite`` token; goes into the link.
        org_name: Human-readable organization name shown in copy.
        inviter_name: Display name of the user who created the invite;
            ``None`` falls back to a generic "a Pulse user" phrase.
        role: Role granted on acceptance — ``"owner"`` or ``"member"``.

    Returns:
        ``(subject, body)`` tuple ready for ``email.send_email``.
    """
    link = f"{settings.frontend_base_url.rstrip('/')}/invite?token={token}"
    inviter = inviter_name or "A Pulse user"
    subject = f"You're invited to {org_name} on Pulse"
    body = (
        f"Hi,\n\n"
        f"{inviter} has invited you to join {org_name} on Pulse as {role}.\n\n"
        f"Click the link below to accept the invitation and set up your account:\n\n"
        f"{link}\n\n"
        f"The link expires in 7 days. If you weren't expecting this invitation, you can ignore this email."
    )
    return subject, body

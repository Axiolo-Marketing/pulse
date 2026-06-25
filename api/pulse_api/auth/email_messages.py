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


def _unsubscribe_footer(unsubscribe_url: str | None) -> str:
    """Trailing unsubscribe line for recipient emails, or empty when no
    link is supplied."""
    if not unsubscribe_url:
        return ""
    return (
        f"\n\nDon't want reminders about this? Unsubscribe:\n{unsubscribe_url}"
    )


def engagement_invite_email(
    *,
    deck_url: str,
    org_name: str,
    recipient_name: str | None = None,
    engagement_name: str | None = None,
    unsubscribe_url: str | None = None,
) -> tuple[str, str]:
    """Returns ``(subject, body)`` for the initial deck invite sent to a
    recipient — the operator-triggered email that replaces the manual
    link-share.

    Args:
        deck_url: The recipient's private ``?t=`` deck link (already built
            from their token by the caller).
        org_name: The consulting org's name, shown in the copy.
        recipient_name: Optional name to greet by.
        engagement_name: Optional engagement label, woven into the ask.
        unsubscribe_url: Optional per-recipient unsubscribe link; appended
            as a footer so the recipient can opt out of follow-up reminders.

    Returns:
        ``(subject, body)`` ready for ``email.send_email``.
    """
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    about = f" about {engagement_name}" if engagement_name else ""
    subject = f"{org_name} would like your input"
    body = (
        f"{greeting}\n\n"
        f"{org_name} has a short set of questions for you{about}. "
        f"It only takes a few minutes, and your answers save as you go.\n\n"
        f"Open your questions here:\n\n"
        f"{deck_url}\n\n"
        f"The link is personal to you — please don't forward it."
        f"{_unsubscribe_footer(unsubscribe_url)}"
    )
    return subject, body


def engagement_reminder_email(
    *,
    deck_url: str,
    org_name: str,
    recipient_name: str | None = None,
    engagement_name: str | None = None,
    unsubscribe_url: str | None = None,
) -> tuple[str, str]:
    """Returns ``(subject, body)`` for a scheduled reminder to a recipient
    who was invited but hasn't finished. Same shape as the invite, gentler
    nudge wording, and always carries the unsubscribe footer.
    """
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    about = f" for {engagement_name}" if engagement_name else ""
    subject = f"Reminder: {org_name} is waiting on your answers"
    body = (
        f"{greeting}\n\n"
        f"Just a reminder that {org_name} is still waiting on your answers"
        f"{about}. Your progress is saved, so you can pick up right where "
        f"you left off:\n\n"
        f"{deck_url}\n\n"
        f"The link is personal to you — please don't forward it."
        f"{_unsubscribe_footer(unsubscribe_url)}"
    )
    return subject, body

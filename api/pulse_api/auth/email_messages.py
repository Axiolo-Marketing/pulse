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

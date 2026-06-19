from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    anon_database_url: str | None = None
    admin_database_url: str | None = None
    member_database_url: str | None = None

    test_database_url: str | None = None
    test_anon_database_url: str | None = None
    test_admin_database_url: str | None = None
    test_member_database_url: str | None = None

    # ── Multi-tenant ─────────────────────────────────────────────────────
    # Whitespace/comma-separated list of operator emails granted
    # `is_superadmin = true`. Consumed by the 0004 data migration and by
    # `make seed-dev`. Empty means no superadmins (acceptable in dev).
    superadmin_emails: str = ""

    # Pulse is invite-only — `POST /api/auth/signup` returns 404 unless
    # this is explicitly enabled. Tests covering the email/password flow
    # override it. Never set true in production.
    signup_enabled: bool = False

    session_secret: str = ""
    session_cookie_name: str = "pulse_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 30

    verify_email_token_max_age_seconds: int = 60 * 60 * 24 * 7        # 7 days
    reset_password_token_max_age_seconds: int = 60 * 60               # 1 hour
    invite_token_max_age_seconds: int = 60 * 60 * 24 * 7              # 7 days

    # Per-org logo uploads (Settings → Organization → Logo).
    max_org_logo_bytes: int = 500_000
    allowed_logo_mime_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "image/webp",
    )

    password_argon2_time_cost: int = 3
    password_argon2_memory_kb: int = 65536

    # Where the user-facing frontend lives. Used to build verification +
    # reset links that go into outbound emails.
    frontend_base_url: str = "http://localhost:14321"

    # ── MCP OAuth 2.1 authorization server ───────────────────────────────
    # Issuer base URL for the OAuth authorization server mounted at the
    # domain root (discovery metadata, /authorize, /token, /register,
    # /revoke). Empty → fall back to ``frontend_base_url``. In prod both
    # resolve to ``https://pulse.axiolo.com``. The resource identifier
    # (audience) of issued access tokens is the MCP endpoint URL derived
    # from this via ``mcp_resource_url``.
    mcp_issuer_url: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "common"
    microsoft_redirect_uri: str = ""

    # ── Outbound email (Resend) ──────────────────────────────────────────
    # Transactional mail (org invites, email verification, password reset)
    # is sent via Resend's HTTPS API. Empty `resend_api_key` keeps
    # `send_email` in log-only mode — the default for dev + tests, where no
    # real mail should leave the box. `email_from` must be an address on a
    # Resend-verified domain, e.g. `Pulse <noreply@pulse.axiolo.com>`.
    resend_api_key: str = ""
    email_from: str = ""

    cors_allowed_origin: str = "http://localhost:4321"
    upload_dir: Path = Path("/var/lib/pulse/uploads")
    max_upload_bytes: int = 26_214_400

    # ── Observability ────────────────────────────────────────────────────
    sentry_dsn: str = ""
    environment: str = "development"
    log_level: str = "INFO"

    # ── Rate limits (per-IP, per-minute) ─────────────────────────────────
    rate_limit_default: str = "60/minute"
    rate_limit_token_validation: str = "10/minute"  # /api/me + /api/auth/login
    rate_limit_account_enumeration: str = "5/minute"  # signup + forgot-password

    @property
    def mcp_issuer_base(self) -> str:
        """Resolved OAuth issuer base URL (no trailing slash).

        Prefers ``mcp_issuer_url`` when set; otherwise falls back to
        ``frontend_base_url``. The MCP authorization server lives at the
        domain root, so this is the same origin Pulse already serves the
        frontend from.

        Returns:
            The issuer base URL with any trailing slash removed.
        """
        base = self.mcp_issuer_url or self.frontend_base_url
        return base.rstrip("/")

    @property
    def mcp_resource_url(self) -> str:
        """RFC 8707 resource identifier (audience) for MCP access tokens.

        The MCP endpoint is mounted at ``/api/mcp`` under the issuer
        origin. Access tokens are bound to this exact string and a token
        presented to a different resource is rejected by
        ``PulseOAuthProvider.load_access_token``.

        Returns:
            ``<issuer base>/api/mcp``.
        """
        return f"{self.mcp_issuer_base}/api/mcp"


settings = Settings()

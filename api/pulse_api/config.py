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

    test_database_url: str | None = None
    test_anon_database_url: str | None = None
    test_admin_database_url: str | None = None

    session_secret: str = ""
    session_cookie_name: str = "pulse_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 30

    verify_email_token_max_age_seconds: int = 60 * 60 * 24 * 7        # 7 days
    reset_password_token_max_age_seconds: int = 60 * 60               # 1 hour

    password_argon2_time_cost: int = 3
    password_argon2_memory_kb: int = 65536

    # Where the user-facing frontend lives. Used to build verification +
    # reset links that go into outbound emails.
    frontend_base_url: str = "http://localhost:14321"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "common"
    microsoft_redirect_uri: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""

    cors_allowed_origin: str = "http://localhost:4321"
    upload_dir: Path = Path("/var/lib/pulse/uploads")
    max_upload_bytes: int = 26_214_400

    initial_admin_email: str = ""

    # ── Observability ────────────────────────────────────────────────────
    sentry_dsn: str = ""
    environment: str = "development"
    log_level: str = "INFO"

    # ── Rate limits (per-IP, per-minute) ─────────────────────────────────
    rate_limit_default: str = "60/minute"
    rate_limit_token_validation: str = "10/minute"  # /api/me + /api/auth/login
    rate_limit_account_enumeration: str = "5/minute"  # signup + forgot-password


settings = Settings()

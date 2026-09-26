"""Voice transcription: per-engagement toggle + transcript on uploads.

* ``engagements.transcription_enabled`` (default false) — the org member's
  per-engagement opt-in. Off by default because transcription sends a
  respondent's recording to a third-party speech-to-text provider; some
  clients (e.g. law firms) must never have that happen.
* ``uploads.transcript*`` — the text, a status
  (``pending``/``done``/``failed``), which provider produced it, the last
  error, and when it finished. Only voice uploads ever get these.

No RLS/grant change: plain columns on already org-scoped tables whose
table-level grants cover new columns. Writes come from the BYPASSRLS
transcription job (``pulse_api/transcription.py``) and the member-session
retry route.
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.engagements "
        "add column transcription_enabled boolean not null default false;"
    )
    op.execute(
        """
        alter table public.uploads
            add column transcript text,
            add column transcript_status text
                check (transcript_status in ('pending', 'done', 'failed')),
            add column transcript_provider text,
            add column transcript_error text,
            add column transcribed_at timestamptz;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        alter table public.uploads
            drop column if exists transcribed_at,
            drop column if exists transcript_error,
            drop column if exists transcript_provider,
            drop column if exists transcript_status,
            drop column if exists transcript;
        """
    )
    op.execute(
        "alter table public.engagements drop column if exists transcription_enabled;"
    )

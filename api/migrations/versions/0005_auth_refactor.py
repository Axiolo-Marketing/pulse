"""auth refactor — drop users.is_admin + NOT NULL on every org_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-03

PR 2 of the multi-tenant refactor. After 0004 landed the schema + the
Axiolo backfill, application code now resolves admin permissions through
``organization_memberships`` instead of ``users.is_admin``. This revision
locks the data shape behind that decision:

1. Backfill ``org_id`` on any row that still has NULL (mop-up only —
   0004 already backfilled every pre-existing row to the Axiolo org).
2. ALTER ``cards.org_id``, ``responses.org_id``, ``uploads.org_id``,
   ``api_keys.org_id``, ``audit_logs.org_id`` → NOT NULL.
3. DROP ``users.is_admin`` (replaced by ``organization_memberships.role
   = 'owner'`` plus ``users.is_superadmin`` for cross-org operators).

``downgrade()`` reverses all three steps in reverse order. It re-creates
``users.is_admin`` and sets it back to true for every user who has at
least one ``owner`` membership — that is the closest one-way reversal of
the data migration 0004 performed.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


# Tables that gain a NOT NULL on ``org_id`` in this revision. Listed in
# one place so ``upgrade`` + ``downgrade`` stay symmetric.
_NULLABLE_TABLES = (
    "cards",
    "responses",
    "uploads",
    "api_keys",
    "audit_logs",
)


def upgrade() -> None:
    # ── 1. Safety backfill: mop up any row that 0004 missed ──────────────
    # 0004 already backfilled every pre-existing row to the Axiolo org;
    # this guards against a row that landed during the window between
    # 0004 deploying and PR 2's auth code shipping. Parametrized binds via
    # `op.get_bind().execute(sa.text(...), {...})` per PR 1 reviewer
    # feedback (`op.execute` does not bind params).
    bind = op.get_bind()
    for tbl in _NULLABLE_TABLES:
        bind.execute(
            sa.text(
                f"update public.{tbl} set org_id = "
                "(select id from public.organizations where slug = :slug) "
                "where org_id is null"
            ),
            {"slug": "axiolo"},
        )

    # ── 2. NOT NULL on every tenant-scoped org_id column ─────────────────
    for tbl in _NULLABLE_TABLES:
        op.execute(
            f"alter table public.{tbl} alter column org_id set not null;"
        )

    # ── 3. Drop users.is_admin ───────────────────────────────────────────
    # 0004 backfilled an owner membership for every is_admin=true user
    # already, so this drop carries no behavioral change.
    op.execute("alter table public.users drop column if exists is_admin;")


def downgrade() -> None:
    # ── 1. Re-add users.is_admin with the old default ────────────────────
    op.execute(
        "alter table public.users "
        "add column is_admin boolean not null default false;"
    )

    # ── 2. Reverse the owner-membership → is_admin backfill ──────────────
    # The closest reversal of the 0004 owner-seeding step: every user
    # who has at least one owner membership becomes is_admin=true again.
    op.execute(
        """
        update public.users u
        set is_admin = true
        where exists (
          select 1
          from public.organization_memberships m
          where m.user_id = u.id
            and m.role = 'owner'
        );
        """
    )

    # ── 3. Drop NOT NULL on the org_id columns (reverse order) ───────────
    for tbl in reversed(_NULLABLE_TABLES):
        op.execute(
            f"alter table public.{tbl} alter column org_id drop not null;"
        )

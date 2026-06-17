"""organizations: add branding jsonb + anon read path

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-16

Adds a nullable ``branding jsonb`` column to ``organizations`` so each
tenant can override the client deck's brand color, background color,
text color, and font. A missing column value (or a missing key inside
the object) means "use the built-in default" — the renderer falls back
on its own.

The client deck runs on the ``pulse_anon`` role, which until now had no
grant or policy on ``organizations`` (it only ever touched ``clients``,
``cards``, ``responses``, ``uploads``). The deck needs to read its own
org's brand fields, so this revision adds the narrowest possible read
path:

* a column-scoped ``select (id, name, logo_path, branding)`` grant, and
* a SELECT policy that admits exactly the row whose ``id`` matches the
  request's ``pulse.org_id`` GUC.

``pulse_request_org_id()`` already exists and ``pulse_anon`` already
holds EXECUTE on it (both from 0004), so the policy can call it directly.
The ``pulse_member`` grant on ``organizations`` is table-level (0004),
so the new column is covered automatically — no extra member grant.

``downgrade()`` reverses everything: drop the anon policy, revoke the
anon column grant, then drop the column.
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.organizations add column branding jsonb;"
    )

    # Narrow anon read path: the deck reads its own org's brand fields on
    # a pulse_anon connection with pulse.org_id set. Column-scoped grant
    # keeps everything else (created_at, etc.) invisible to anon.
    op.execute(
        "grant select (id, name, logo_path, branding) "
        "on public.organizations to pulse_anon;"
    )
    op.execute(
        """
        create policy organizations_anon_read on public.organizations
            for select to pulse_anon
            using (id = public.pulse_request_org_id());
        """
    )


def downgrade() -> None:
    op.execute(
        "drop policy if exists organizations_anon_read on public.organizations;"
    )
    op.execute(
        "revoke select (id, name, logo_path, branding) "
        "on public.organizations from pulse_anon;"
    )
    op.execute(
        "alter table public.organizations drop column if exists branding;"
    )

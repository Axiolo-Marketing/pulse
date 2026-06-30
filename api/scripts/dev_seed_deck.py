"""Idempotent dev-environment deck seeder.

Mints (or refreshes) a complete *client-facing* card deck so a developer can
load the deck UI in a browser without clicking through the admin to build one.
It produces a stable **recipient token** you can drop into ``?t=`` — re-running
this script reuses the same engagement + recipient (no duplicates), so the URL
never changes.

Under the dev "Axiolo" org (created by ``make seed-dev`` — run that first) it
upserts:

- one client ("Dev Demo Co", idempotent on ``(org_id, name)``);
- one engagement ("Dev Demo Deck") with ``voice_enabled = true``, anchored by
  the fixed recipient token so re-runs reuse it;
- one recipient carrying the fixed token ``dec0ded0dec0ded0`` (idempotent on
  the unique ``token``);
- a deck of cards covering every response type, upserted on the unique
  ``(engagement_id, order_index)`` so re-runs refresh content in place.

Run from the project root:
    make seed-deck

…or directly inside the backend container:
    docker compose exec -T backend uv run python -m scripts.dev_seed_deck

Override the fixed token / labels via env:
    DEV_DECK_TOKEN=...          (default dec0ded0dec0ded0)
    DEV_DECK_CLIENT=...         (default "Dev Demo Co")
    DEV_DECK_ENGAGEMENT=...     (default "Dev Demo Deck")
    DEV_DECK_RECIPIENT_EMAIL=...(default client@example.com)
    DEV_DECK_RECIPIENT_NAME=... (default "Dev Client")
    DEV_FRONTEND_BASE=...       (default http://localhost:14321)

Refuses to run against anything that doesn't look local (mirrors
``scripts.dev_seed``) so it can't seed a production DB by accident.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pulse_api.config import settings

# The org `make seed-dev` creates. We attach the demo deck to it.
AXIOLO_ORG_SLUG = "axiolo"

DEFAULT_TOKEN = "dec0ded0dec0ded0"  # fixed → re-runs reuse the same deck link
DEFAULT_CLIENT_NAME = "Dev Demo Co"
DEFAULT_ENGAGEMENT_NAME = "Dev Demo Deck"
DEFAULT_RECIPIENT_EMAIL = "client@example.com"
DEFAULT_RECIPIENT_NAME = "Dev Client"
DEFAULT_FRONTEND_BASE = "http://localhost:14321"

# One card per response_type the CHECK constraint allows (SPEC §4). Each is a
# dict with the columns the deck reads. ``options`` is a list for select-type
# cards and None otherwise; ``default_value`` pre-populates the client's answer.
DECK_CARDS: list[dict] = [
    {
        "order_index": 1,
        "category": "Company basics",
        "title": "Confirm legal name",
        "context": "We pulled this from your incorporation documents.",
        "question": "Is this your registered legal entity name?",
        "response_type": "confirm-edit",
        "options": None,
        "default_value": "Dev Demo Co, Inc.",
        "skip_allowed": False,
    },
    {
        "order_index": 2,
        "category": "Company basics",
        "title": "Primary industry",
        "context": "Pick the closest match — it drives the rest of the deck.",
        "question": "Which industry best describes your business?",
        "response_type": "single-select",
        "options": ["SaaS", "E-commerce", "Professional services"],
        "default_value": None,
        "skip_allowed": False,
    },
    {
        "order_index": 3,
        "category": "Scope",
        "title": "Target regions",
        "context": "Select all the markets we should plan for.",
        "question": "Where do you operate today?",
        "response_type": "multi-select",
        "options": ["North America", "Europe", "LATAM", "APAC"],
        "default_value": None,
        "skip_allowed": True,
    },
    {
        "order_index": 4,
        "category": "Contacts",
        "title": "Billing contact name",
        "context": "Who should we address invoices to?",
        "question": "Full name of the billing contact",
        "response_type": "short-text",
        "options": None,
        "default_value": None,
        "skip_allowed": False,
    },
    {
        "order_index": 5,
        "category": "Goals",
        "title": "Engagement objectives",
        "context": "A few sentences is plenty — we'll refine together.",
        "question": "What does success look like for this engagement?",
        "response_type": "long-text",
        "options": None,
        "default_value": None,
        "skip_allowed": True,
    },
    {
        "order_index": 6,
        "category": "Brand",
        "title": "Brand guidelines link",
        "context": "Paste a shareable link (Google Drive, Notion, Figma…).",
        "question": "Where can we find your current brand guidelines?",
        "response_type": "document-link",
        "options": None,
        "default_value": None,
        "skip_allowed": False,
    },
    {
        "order_index": 7,
        "category": "Contacts",
        "title": "Project lead",
        "context": "We'll use this to set up the kickoff call.",
        "question": "Share the contact details of your project lead.",
        "response_type": "contact-share",
        "options": None,
        "default_value": None,
        "skip_allowed": False,
    },
    {
        "order_index": 8,
        "category": "Documents",
        "title": "Signed SOW",
        "context": "PDF or image is fine.",
        "question": "Upload the countersigned statement of work.",
        "response_type": "file-upload",
        "options": None,
        "default_value": None,
        "skip_allowed": True,
    },
]


def _looks_like_prod(database_url: str) -> bool:
    """Refuse to run against anything that isn't obviously local."""
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1", "db", ""}:
        return True
    if "sslmode=require" in (parsed.query or "").lower():
        return True
    return False


async def seed_deck(
    *,
    token: str,
    client_name: str,
    engagement_name: str,
    recipient_email: str,
    recipient_name: str,
) -> dict:
    """Upsert the demo client → engagement → recipient → cards chain.

    All operations are idempotent: the client keys on ``(org_id, name)``, the
    engagement is anchored by the fixed recipient ``token`` (falling back to a
    ``(client_id, engagement_name)`` lookup), the recipient keys on the unique
    ``token``, and each card keys on the unique ``(engagement_id, order_index)``.
    Connects via ``settings.database_url`` (the ``pulse`` owner role in dev, so
    it bypasses RLS by ownership — same as ``scripts.dev_seed``).

    Args:
        token: Fixed 16-hex recipient token to mint/reuse.
        client_name: Demo client/company name.
        engagement_name: Demo engagement label.
        recipient_email: Recipient email (drives the unique email index).
        recipient_name: Recipient display name.

    Returns:
        Dict with ``org_id``, ``client_id``, ``engagement_id``,
        ``recipient_id``, ``token``, and ``card_count``.

    Raises:
        RuntimeError: If the Axiolo org doesn't exist yet (run ``make seed-dev``).
    """
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            # 1. Resolve the dev org (created by `make seed-dev`).
            org_id = (
                await conn.execute(
                    text(
                        "select id::text from public.organizations where slug = :s"
                    ),
                    {"s": AXIOLO_ORG_SLUG},
                )
            ).scalar_one_or_none()
            if org_id is None:
                raise RuntimeError(
                    f"organization slug={AXIOLO_ORG_SLUG!r} not found — "
                    "run `make seed-dev` first."
                )

            # An owner of the org makes a realistic `created_by` (nullable).
            created_by = (
                await conn.execute(
                    text(
                        "select user_id::text from public.organization_memberships "
                        "where org_id = cast(:org as uuid) and role = 'owner' "
                        "order by created_at limit 1"
                    ),
                    {"org": org_id},
                )
            ).scalar_one_or_none()

            # 2. get-or-create the demo client (idempotent on (org_id, name)).
            await conn.execute(
                text(
                    "insert into public.clients (org_id, name) "
                    "values (cast(:org as uuid), :n) "
                    "on conflict (org_id, name) do nothing"
                ),
                {"org": org_id, "n": client_name},
            )
            client_id = (
                await conn.execute(
                    text(
                        "select id::text from public.clients "
                        "where org_id = cast(:org as uuid) and name = :n"
                    ),
                    {"org": org_id, "n": client_name},
                )
            ).scalar_one()

            # 3. Resolve the engagement. Prefer the one the fixed token already
            #    points at; else reuse an existing demo engagement under the
            #    client; else create it.
            engagement_id = (
                await conn.execute(
                    text(
                        "select engagement_id::text from public.recipients "
                        "where token = :tok"
                    ),
                    {"tok": token},
                )
            ).scalar_one_or_none()
            if engagement_id is None:
                engagement_id = (
                    await conn.execute(
                        text(
                            "select id::text from public.engagements "
                            "where client_id = cast(:cid as uuid) "
                            "  and engagement_name = :en "
                            "order by created_at limit 1"
                        ),
                        {"cid": client_id, "en": engagement_name},
                    )
                ).scalar_one_or_none()
            if engagement_id is None:
                engagement_id = (
                    await conn.execute(
                        text(
                            "insert into public.engagements "
                            "  (client_id, engagement_name, org_id, "
                            "   voice_enabled, created_by) "
                            "values (cast(:cid as uuid), :en, cast(:org as uuid), "
                            "        true, cast(:by as uuid)) "
                            "returning id::text"
                        ),
                        {
                            "cid": client_id,
                            "en": engagement_name,
                            "org": org_id,
                            "by": created_by,
                        },
                    )
                ).scalar_one()
            else:
                # Ensure voice stays enabled on re-runs (and after manual edits).
                await conn.execute(
                    text(
                        "update public.engagements set voice_enabled = true "
                        "where id = cast(:eid as uuid)"
                    ),
                    {"eid": engagement_id},
                )

            # 4. Upsert the recipient carrying the fixed token (unique on token).
            await conn.execute(
                text(
                    "insert into public.recipients "
                    "  (engagement_id, org_id, email, name, token) "
                    "values (cast(:eid as uuid), cast(:org as uuid), "
                    "        :email, :name, :token) "
                    "on conflict (token) do update set "
                    "  email = excluded.email, name = excluded.name"
                ),
                {
                    "eid": engagement_id,
                    "org": org_id,
                    "email": recipient_email,
                    "name": recipient_name,
                    "token": token,
                },
            )
            recipient_id = (
                await conn.execute(
                    text(
                        "select id::text from public.recipients where token = :tok"
                    ),
                    {"tok": token},
                )
            ).scalar_one()

            # 5. Upsert the deck. Each card keys on (engagement_id, order_index),
            #    so re-runs refresh content in place without duplicating rows.
            for card in DECK_CARDS:
                opts = card["options"]
                await conn.execute(
                    text(
                        "insert into public.cards "
                        "  (engagement_id, org_id, order_index, category, title, "
                        "   context, question, response_type, options, "
                        "   default_value, skip_allowed) "
                        "values (cast(:eid as uuid), cast(:org as uuid), :oi, "
                        "        :cat, :title, :ctx, :q, :rt, cast(:opts as jsonb), "
                        "        :dv, :sa) "
                        "on conflict (engagement_id, order_index) do update set "
                        "  org_id = excluded.org_id, "
                        "  category = excluded.category, "
                        "  title = excluded.title, "
                        "  context = excluded.context, "
                        "  question = excluded.question, "
                        "  response_type = excluded.response_type, "
                        "  options = excluded.options, "
                        "  default_value = excluded.default_value, "
                        "  skip_allowed = excluded.skip_allowed"
                    ),
                    {
                        "eid": engagement_id,
                        "org": org_id,
                        "oi": card["order_index"],
                        "cat": card["category"],
                        "title": card["title"],
                        "ctx": card["context"],
                        "q": card["question"],
                        "rt": card["response_type"],
                        "opts": json.dumps(opts) if opts is not None else None,
                        "dv": card["default_value"],
                        "sa": card["skip_allowed"],
                    },
                )

            return {
                "org_id": org_id,
                "client_id": client_id,
                "engagement_id": engagement_id,
                "recipient_id": recipient_id,
                "token": token,
                "card_count": len(DECK_CARDS),
            }
    finally:
        await engine.dispose()


def main() -> int:
    token = os.environ.get("DEV_DECK_TOKEN", DEFAULT_TOKEN)
    client_name = os.environ.get("DEV_DECK_CLIENT", DEFAULT_CLIENT_NAME)
    engagement_name = os.environ.get(
        "DEV_DECK_ENGAGEMENT", DEFAULT_ENGAGEMENT_NAME
    )
    recipient_email = os.environ.get(
        "DEV_DECK_RECIPIENT_EMAIL", DEFAULT_RECIPIENT_EMAIL
    )
    recipient_name = os.environ.get(
        "DEV_DECK_RECIPIENT_NAME", DEFAULT_RECIPIENT_NAME
    )
    frontend_base = os.environ.get(
        "DEV_FRONTEND_BASE", DEFAULT_FRONTEND_BASE
    ).rstrip("/")

    if _looks_like_prod(settings.database_url):
        sys.stderr.write(
            f"refusing to seed against {settings.database_url!r}: "
            "looks like a non-local DB. Set DATABASE_URL to a local DSN.\n"
        )
        return 2

    try:
        result = asyncio.run(
            seed_deck(
                token=token,
                client_name=client_name,
                engagement_name=engagement_name,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
            )
        )
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    deck_url = f"{frontend_base}/?t={result['token']}"
    sys.stdout.write(
        "seeded demo deck:\n"
        f"  client       Dev Demo Co (id={result['client_id']})\n"
        f"  engagement   id={result['engagement_id']} (voice_enabled=true)\n"
        f"  recipient    id={result['recipient_id']}\n"
        f"  cards        {result['card_count']} "
        "(confirm-edit, single-select, multi-select, short-text, "
        "long-text, document-link, contact-share, file-upload)\n"
        f"\n  token        {result['token']}\n"
        f"  deck URL     {deck_url}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

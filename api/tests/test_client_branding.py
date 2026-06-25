"""Tests for the client-facing (token-authed) branding/logo surface.

* ``GET /api/me`` now carries ``org_logo_path`` + ``org_branding`` for
  the token's owning org.
* ``GET /api/me/logo`` serves the org's current logo (200 + bytes),
  404s when the org has no logo, sets a strict CSP header for SVGs, and
  never serves another org's logo cross-tenant.

The logo is seeded by driving the real upload route
(``POST /api/orgs/me/logo``) as an org owner — the same way
``test_org_logo_upload`` does — so the on-disk file + DB ``logo_path``
stay consistent and the token route reads exactly what the operator
wrote.

Cross-org note: the shared ``seed_client`` / ``other_seeded_client``
fixtures are BOTH in the Axiolo org, so they can't prove cross-tenant
isolation. The isolation test below builds a *second* org with its own
owner + client so org A's token is exercised against org B's logo.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


# ── Helpers ───────────────────────────────────────────────────────────────


async def _seed_org_with_owner_and_client(
    db: AsyncSession, *, label: str
) -> dict[str, str]:
    """Create a fresh org + owner user + client engagement.

    Returns ``{org_id, owner_id, client_id, token}``. The owner can drive
    the logo-upload route for that org; the client's ``token`` exercises
    the token-authed deck routes scoped to the same org.
    """
    org_id = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s) returning id::text"
            ),
            {"n": f"{label} Inc", "s": f"{label.lower()}-{secrets.token_hex(4)}"},
        )
    ).mappings().one()["id"]

    owner_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, cast(:o as uuid), now()) "
                "returning id::text"
            ),
            {
                "e": f"owner-{label.lower()}-{secrets.token_hex(3)}@example.com",
                "h": hash_password("a-good-password-here"),
                "n": f"{label} Owner",
                "o": org_id,
            },
        )
    ).mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'owner')"
        ),
        {"o": org_id, "u": owner_id},
    )

    token = secrets.token_hex(8)
    client_id = (
        await db.execute(
            text(
                "with c as ("
                "  insert into public.clients (org_id, name) "
                "  values (cast(:o as uuid), :n) "
                "  on conflict (org_id, name) do update set name = excluded.name "
                "  returning id"
                ") "
                "insert into public.engagements (client_id, org_id) "
                "select c.id, cast(:o as uuid) from c "
                "returning id::text"
            ),
            {"n": f"{label} Client", "o": org_id},
        )
    ).mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.recipients (engagement_id, org_id, token) "
            "values (cast(:e as uuid), cast(:o as uuid), :t)"
        ),
        {"e": client_id, "o": org_id, "t": token},
    )

    return {
        "org_id": org_id,
        "owner_id": owner_id,
        "client_id": client_id,
        "token": token,
    }


async def _upload_logo_as_owner(
    client: AsyncClient,
    *,
    owner_id: str,
    org_id: str,
    filename: str,
    content_type: str,
    payload: bytes,
) -> str:
    """Upload a logo for ``org_id`` as its owner; return the ``logo_path``.

    Drives the production ``POST /api/orgs/me/logo`` route so the disk
    write + DB row are produced exactly as in real use. Restores no
    cookie afterwards — callers set their own auth for the read.
    """
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(owner_id, org_id),
    )
    r = await client.post(
        "/api/orgs/me/logo",
        files={"file": (filename, payload, content_type)},
    )
    assert r.status_code == 200, r.text
    return r.json()["logo_path"]


# ── GET /api/me carries org branding + logo ───────────────────────────────


async def test_me_carries_branding_and_logo(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_client: dict[str, str],
) -> None:
    """After an owner sets branding + uploads a logo, a client in that
    org sees both on ``GET /api/me``.

    ``seed_admin_user`` is the Axiolo owner; ``seed_client`` is a client
    in Axiolo — same org — so the deck bootstrap should reflect the
    owner's brand config.
    """
    branding = {"brand_color": "#2960F6", "font": "inter"}
    # Set branding as the owner.
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(seed_admin_user["id"], seed_admin_user["org_id"]),
    )
    set_r = await client.patch("/api/orgs/me/branding", json=branding)
    assert set_r.status_code == 200, set_r.text

    logo_path = await _upload_logo_as_owner(
        client,
        owner_id=seed_admin_user["id"],
        org_id=seed_admin_user["org_id"],
        filename="brand.png",
        content_type="image/png",
        payload=b"PNG-logo-bytes" * 8,
    )

    # Now hit /api/me as the token-authed client (no cookie).
    client.cookies.clear()
    client.headers["X-Pulse-Token"] = seed_client["token"]
    me = await client.get("/api/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["org_branding"] == branding
    assert body["org_logo_path"] == logo_path


async def test_me_branding_and_logo_default_to_null(
    client_authed: AsyncClient,
) -> None:
    """With no branding/logo configured, both fields are null on /api/me."""
    me = await client_authed.get("/api/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["org_branding"] is None
    assert body["org_logo_path"] is None


# ── GET /api/me/logo ──────────────────────────────────────────────────────


async def test_me_logo_404_when_no_logo(
    client_authed: AsyncClient,
) -> None:
    """No logo configured → 404."""
    r = await client_authed.get("/api/me/logo")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "filename, content_type, expect_csp",
    [
        ("brand.png", "image/png", False),
        ("brand.jpg", "image/jpeg", False),
        ("brand.webp", "image/webp", False),
        ("brand.svg", "image/svg+xml", True),
    ],
    ids=["png", "jpeg", "webp", "svg-has-csp"],
)
async def test_me_logo_serves_bytes_and_content_type(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_client: dict[str, str],
    filename: str,
    content_type: str,
    expect_csp: bool,
) -> None:
    """The token route serves the uploaded bytes with the right
    content-type; SVG responses carry a strict CSP header, others don't."""
    payload = (
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        if content_type == "image/svg+xml"
        else b"logo-bytes-" + secrets.token_bytes(16)
    )
    await _upload_logo_as_owner(
        client,
        owner_id=seed_admin_user["id"],
        org_id=seed_admin_user["org_id"],
        filename=filename,
        content_type=content_type,
        payload=payload,
    )

    client.cookies.clear()
    client.headers["X-Pulse-Token"] = seed_client["token"]
    r = await client.get("/api/me/logo")
    assert r.status_code == 200, r.text
    assert r.content == payload
    assert r.headers["content-type"].startswith(content_type)

    csp = r.headers.get("content-security-policy")
    if expect_csp:
        assert csp is not None
        assert "script-src 'none'" in csp
    else:
        assert csp is None


async def test_me_logo_requires_token(client: AsyncClient) -> None:
    """No ``X-Pulse-Token`` → 401."""
    r = await client.get("/api/me/logo")
    assert r.status_code == 401


# ── Cross-org isolation: org A's token must not fetch org B's logo ────────


async def test_me_logo_is_org_scoped(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """A token bound to org A never receives org B's logo.

    Org A has NO logo; org B has one. Org A's token hitting
    ``/api/me/logo`` must 404 — never leak org B's bytes — because the
    anon SELECT policy on ``organizations`` (migration 0007) admits only
    the row matching the request's ``pulse.org_id`` GUC.
    """
    org_a = await _seed_org_with_owner_and_client(db, label="Alpha")
    org_b = await _seed_org_with_owner_and_client(db, label="Bravo")
    await db.flush()

    # Give org B a logo; org A stays logo-less.
    b_logo_path = await _upload_logo_as_owner(
        client,
        owner_id=org_b["owner_id"],
        org_id=org_b["org_id"],
        filename="bravo.png",
        content_type="image/png",
        payload=b"BRAVO-secret-logo" * 4,
    )
    assert b_logo_path  # sanity: org B really has a logo

    client.cookies.clear()

    # Org A's token: must 404, never serve org B's bytes.
    client.headers["X-Pulse-Token"] = org_a["token"]
    a_logo = await client.get("/api/me/logo")
    assert a_logo.status_code == 404, a_logo.text

    # And /api/me for org A reports no logo despite org B having one.
    a_me = await client.get("/api/me")
    assert a_me.status_code == 200, a_me.text
    assert a_me.json()["org_logo_path"] is None

    # Sanity: org B's own token DOES get its logo (the policy isn't just
    # globally denying).
    client.headers["X-Pulse-Token"] = org_b["token"]
    b_logo = await client.get("/api/me/logo")
    assert b_logo.status_code == 200, b_logo.text
    assert b_logo.content == b"BRAVO-secret-logo" * 4

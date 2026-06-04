"""Parametrized OAuth callback × invite-state matrix.

The OAuth callback's invite-acceptance branch is the new self-signup
replacement in PR 2. Six scenarios:

| invite state       | existing user | expected outcome              |
|--------------------|---------------|-------------------------------|
| no invite          | no            | redirect ?error=…             |
| pending            | no            | user created, membership=role |
| expired            | no            | redirect ?error=…             |
| accepted (used)    | no            | redirect ?error=…             |
| pending            | yes           | membership added (multi-org)  |
| no invite          | yes           | normal sign-in, no new memb.  |

Plus a role-specific case to pin that the accepted membership role
matches the invite's role.

We assert (a) the redirect URL, (b) ``organization_memberships`` rows
for the user.
"""
from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings

PROVIDER_ENDPOINTS = {
    "google": (
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/oauth2/v3/userinfo",
    ),
}
PROVIDER = "google"  # one provider is enough — handler is provider-agnostic
TOKEN_URL, USERINFO_URL = PROVIDER_ENDPOINTS[PROVIDER]


async def _do_authorize(client: AsyncClient) -> tuple[str, str]:
    r = await client.get(
        f"/api/auth/{PROVIDER}/authorize", follow_redirects=False
    )
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    cookie = r.cookies.get(f"oauth_state_{PROVIDER}")
    assert cookie
    return state, cookie


def _stub_provider(
    respx_mock: respx.Router, *, sub: str, email: str, name: str | None = None
) -> None:
    respx_mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fake"})
    )
    respx_mock.get(USERINFO_URL).mock(
        return_value=httpx.Response(
            200, json={"sub": sub, "email": email, "name": name}
        )
    )


async def _seed_invite(
    db: AsyncSession,
    *,
    org_id: str,
    email: str,
    role: str = "member",
    expires_in_days: int | None = 7,
    accepted: bool = False,
) -> str:
    """Insert an invite with the given lifecycle state. Returns the id."""
    # expires_in_days < 0 → already expired.
    if expires_in_days is None:
        expires_clause = "now() - interval '1 day'"
    elif expires_in_days < 0:
        expires_clause = f"now() - interval '{-expires_in_days} days'"
    else:
        expires_clause = f"now() + interval '{expires_in_days} days'"

    row = (
        await db.execute(
            text(
                f"insert into public.organization_invites "
                f"(org_id, email, role, token_hash, expires_at, accepted_at) "
                f"values (cast(:o as uuid), :e, :r, :h, {expires_clause}, "
                f"        {'now()' if accepted else 'null'}) "
                f"returning id::text"
            ),
            {
                "o": org_id,
                "e": email.lower(),
                "r": role,
                "h": f"hash-{secrets.token_hex(6)}",
            },
        )
    ).mappings().one()
    return row["id"]


async def _membership_for(db: AsyncSession, *, email: str) -> dict | None:
    """Resolve the membership row by the user's email, if any."""
    result = await db.execute(
        text(
            "select m.role, m.org_id::text "
            "from public.organization_memberships m "
            "join public.users u on u.id = m.user_id "
            "where lower(u.email) = lower(:e)"
        ),
        {"e": email},
    )
    rows = result.mappings().all()
    if not rows:
        return None
    assert len(rows) == 1
    return dict(rows[0])


# ── Parametrized scenarios ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "scenario",
    [
        "no_invite_no_user",
        "pending_invite_no_user",
        "expired_invite_no_user",
        "accepted_invite_no_user",
        "existing_user_pending_invite",
        "existing_user_no_invite",
        "pending_invite_owner_role",
    ],
)
async def test_oauth_callback_invite_matrix(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    axiolo_org: dict[str, str],
    scenario: str,
) -> None:
    """Drive the six scenarios + a role-specific case in one matrix."""
    sub = f"sub-{scenario}-{secrets.token_hex(4)}"
    email = f"{scenario}@example.com"

    # Default expectations — overridden per scenario below.
    expect_user = True
    expect_error_redirect = False
    expected_role: str | None = "member"

    if scenario == "no_invite_no_user":
        # No invite seeded → user must NOT be created.
        expect_user = False
        expect_error_redirect = True
        expected_role = None

    elif scenario == "pending_invite_no_user":
        await _seed_invite(
            db, org_id=axiolo_org["id"], email=email, role="member"
        )

    elif scenario == "expired_invite_no_user":
        await _seed_invite(
            db,
            org_id=axiolo_org["id"],
            email=email,
            role="member",
            expires_in_days=-1,
        )
        expect_user = False
        expect_error_redirect = True
        expected_role = None

    elif scenario == "accepted_invite_no_user":
        await _seed_invite(
            db,
            org_id=axiolo_org["id"],
            email=email,
            role="member",
            accepted=True,
        )
        expect_user = False
        expect_error_redirect = True
        expected_role = None

    elif scenario == "existing_user_pending_invite":
        # Seed user without a membership, then seed an invite for them.
        from pulse_api.auth.password import hash_password

        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, email_verified_at) "
                "values (:e, :h, now())"
            ),
            {"e": email, "h": hash_password("anything-here-12345")},
        )
        await _seed_invite(
            db, org_id=axiolo_org["id"], email=email, role="member"
        )
        # The existing-user branch now also auto-accepts a pending invite
        # — see oauth.py `_attach_invite_to_user`. Without this, an
        # existing user with no prior membership would sign in with no
        # active org and every /api/admin/* would 403 with no recovery.
        expected_role = "member"

    elif scenario == "existing_user_no_invite":
        from pulse_api.auth.password import hash_password

        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, email_verified_at) "
                "values (:e, :h, now())"
            ),
            {"e": email, "h": hash_password("anything-here-12345")},
        )
        expected_role = None

    elif scenario == "pending_invite_owner_role":
        await _seed_invite(
            db, org_id=axiolo_org["id"], email=email, role="owner"
        )
        expected_role = "owner"

    else:  # pragma: no cover
        raise AssertionError(f"unknown scenario {scenario!r}")

    await db.flush()

    state, state_cookie = await _do_authorize(client)
    _stub_provider(respx_mock, sub=sub, email=email, name="Auto Test")
    client.cookies.set(f"oauth_state_{PROVIDER}", state_cookie)

    r = await client.get(
        f"/api/auth/{PROVIDER}/callback",
        params={"code": "fake", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text

    if expect_error_redirect:
        assert "error=invitation_required" in r.headers["location"], r.headers
        assert r.cookies.get(settings.session_cookie_name) is None
    else:
        assert "error=" not in r.headers["location"], r.headers
        assert r.cookies.get(settings.session_cookie_name)

    user_count = (
        await db.execute(
            text(
                "select count(*) from public.users "
                "where lower(email) = lower(:e)"
            ),
            {"e": email},
        )
    ).scalar()
    assert user_count == (1 if expect_user else 0), (
        f"{scenario}: user_count={user_count}, expected={1 if expect_user else 0}"
    )

    membership = await _membership_for(db, email=email)
    if expected_role is None:
        assert membership is None, (
            f"{scenario}: unexpected membership {membership!r}"
        )
    else:
        assert membership is not None, (
            f"{scenario}: expected role={expected_role}, no membership"
        )
        assert membership["role"] == expected_role

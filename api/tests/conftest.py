"""Test scaffolding.

Strategy:
- One Postgres test database (`pulse_test`) created by the db-init script.
  Alembic migrations run against it once per pytest session.
- Each test gets a single connection wrapped in a transaction that rolls
  back at teardown — fast, isolated, no cross-test leakage.
- The connection opens as the schema owner (superuser in compose, owner
  role in prod). Tests that need to exercise RLS call `become_anon(conn,
  token=...)` to switch the effective role to `pulse_anon` mid-transaction
  via `SET LOCAL ROLE`. Seed data flushed by the superuser is visible
  inside the same transaction after the role switch.
- The FastAPI app under test gets a dependency override pointing at the
  same connection, so endpoint tests share state with the seed fixtures.
"""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from pathlib import Path  # noqa: F401  (used by tmp_uploads_dir fixture)

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fastapi import Depends, Header, HTTPException

from pulse_api import email as email_module
from pulse_api.auth.middleware import get_org_scoped_session
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from pulse_api.db import get_admin_session, get_anon_session, get_session
from pulse_api.email import OutboundEmail
from pulse_api.main import app
from pulse_api.observability import limiter

API_DIR = Path(__file__).resolve().parents[1]


# ── session-scoped: one engine + one Alembic upgrade ──────────────────────


@pytest.fixture(scope="session")
def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    if settings.test_database_url:
        cfg.set_main_option("sqlalchemy.url", settings.test_database_url)
    return cfg


@pytest.fixture(scope="session")
def _migrate(alembic_config: AlembicConfig) -> None:
    """Run alembic upgrade head once against pulse_test, then leave it alone.

    Auto-recovery: if the test DB is left at a revision that doesn't exist
    on this branch (e.g. someone applied a feature-branch migration to it
    and then we switched back to main), alembic raises CommandError with
    'Can't locate revision'. In that case we drop + recreate `pulse_test`
    and retry once — the alternative is hand-running psql every time
    branches diverge on migrations.
    """
    from alembic.util.exc import CommandError as AlembicCommandError

    original = settings.database_url
    if settings.test_database_url:
        settings.database_url = settings.test_database_url
    try:
        try:
            alembic_command.upgrade(alembic_config, "head")
        except AlembicCommandError as exc:
            if "locate revision" not in str(exc).lower():
                raise
            import asyncio

            from scripts.reset_test_db import reset as reset_test_db

            asyncio.run(reset_test_db())
            alembic_command.upgrade(alembic_config, "head")
    finally:
        settings.database_url = original


@pytest.fixture(scope="session")
async def engine(_migrate: None) -> AsyncIterator[AsyncEngine]:
    url = settings.test_database_url or settings.database_url
    eng = create_async_engine(url, pool_pre_ping=True)
    yield eng
    await eng.dispose()


# ── per-test: transaction-rollback connection + session ───────────────────


@pytest.fixture
async def db_conn(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()


@pytest.fixture
async def db(db_conn: AsyncConnection) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(bind=db_conn, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


async def become_anon(conn: AsyncConnection, *, token: str | None = None) -> None:
    """Flip the open transaction's effective role to `pulse_anon`.

    After this call, RLS policies fire as if the request came in on a
    pulse_anon connection — the production pattern. If `token` is given,
    set the session-local GUC that `pulse_request_token()` reads.

    The role switch is bound to the current transaction (SET LOCAL); it
    rolls back automatically when the test's outer transaction rolls back.
    Note: once switched, you can't seed more rows in this transaction — do
    all seeding before calling this.
    """
    await conn.execute(text("set local role pulse_anon"))
    if token is not None:
        # set_config(name, value, is_local=true) is the parameter-binding
        # equivalent of SET LOCAL. SET LOCAL itself does not accept $-params.
        await conn.execute(
            text("select set_config('pulse.token', :t, true)"),
            {"t": token},
        )


async def become_member(conn: AsyncConnection, *, org_id: str) -> None:
    """Flip the open transaction's effective role to `pulse_member`.

    Mirrors `become_anon`: switches the role and sets the
    ``pulse.org_id`` GUC so org-scoped RLS policies fire. Use this in
    multi-tenant isolation tests that need to prove a member of org A
    cannot read rows tagged with org B's ``org_id``.

    Args:
        conn: The test's open `AsyncConnection`.
        org_id: UUID string of the active organization.
    """
    await conn.execute(text("set local role pulse_member"))
    await conn.execute(
        text("select set_config('pulse.org_id', :o, true)"),
        {"o": org_id},
    )


# ── HTTP client wired so the app shares db_conn's transaction ─────────────


@pytest.fixture
async def client(
    db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    # Reset the global rate limiter at the start of each test so a fresh
    # budget is available. slowapi keeps in-process state, which would
    # otherwise carry across tests in the same session.
    limiter.reset()
    """Async HTTP client whose app routes use db_conn's transaction.

    Both `get_session` and `get_admin_session` are overridden to bind to
    `db_conn`. Sessions joined to a connection with an active transaction
    do NOT commit that transaction when `session.commit()` is called —
    they only end the session's own work — so route handlers can commit
    freely and the outer rollback at teardown still wipes everything out.
    """
    async def _override_session() -> AsyncIterator[AsyncSession]:
        # If a prior request in the same test flipped to pulse_anon,
        # RESET ROLE brings us back to the connection's session_user
        # (the owner role in dev). Harmless if no flip happened.
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(bind=db_conn, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            yield session

    async def _override_anon_session(
        x_pulse_token: str | None = Header(default=None, alias="X-Pulse-Token"),
    ) -> AsyncIterator[AsyncSession]:
        if not x_pulse_token:
            raise HTTPException(status_code=401, detail="missing token")
        # Flip db_conn's effective role to pulse_anon and set the GUCs the
        # helper functions read (pulse.token + pulse.org_id). All three
        # are SET LOCAL — they roll back with the outer transaction at
        # teardown. This matches the production `get_anon_session` shape
        # exactly, including the org_id lookup from the client row.
        await db_conn.execute(text("set local role pulse_anon"))
        await db_conn.execute(
            text("select set_config('pulse.token', :t, true)"),
            {"t": x_pulse_token},
        )
        org_id = (
            await db_conn.execute(
                text(
                    "select coalesce((select org_id::text from public.engagements "
                    "where token = :t limit 1), '')"
                ),
                {"t": x_pulse_token},
            )
        ).scalar_one()
        await db_conn.execute(
            text("select set_config('pulse.org_id', :o, true)"),
            {"o": org_id or ""},
        )
        factory = async_sessionmaker(bind=db_conn, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            yield session

    # `_touch_last_used` in production opens a brand-new admin-engine
    # session so the request's injected session isn't committed mid-request.
    # In tests, that would (a) write to the dev `pulse` DB (admin_engine is
    # built at module import from the non-test URL) and (b) be invisible
    # to the test's rollback transaction. Redirect it through db_conn so
    # the write rolls back at teardown and is visible to the test's `db`
    # session.
    from pulse_api.auth import api_keys as _api_keys_lib
    from pulse_api.repos import api_keys as _api_keys_repo

    async def _patched_touch_last_used(api_key_id) -> None:
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as touch_session:
            await _api_keys_repo.mark_used(touch_session, api_key_id)
            await touch_session.commit()

    monkeypatch.setattr(_api_keys_lib, "_touch_last_used", _patched_touch_last_used)

    from pulse_api.auth.middleware import get_current_org_member

    async def _override_org_scoped_session(
        org_member=Depends(get_current_org_member),
    ) -> AsyncIterator[AsyncSession]:
        """Bind ``get_org_scoped_session`` to ``db_conn`` for tests.

        Production opens a brand-new ``pulse_member`` connection per
        request and flips the GUC. In tests we share the rolled-back
        connection: flip the effective role to ``pulse_member``, set the
        GUC to the resolved membership's ``org_id``, and yield a
        session bound to ``db_conn``.

        ``org_member`` comes from FastAPI's dep graph — the same
        ``get_current_org_member`` that production uses. We re-evaluate
        it via the Depends() machinery so the tests don't have to
        re-implement the resolution logic.
        """
        _, membership = org_member
        # The cookie-auth path in `_override_session` set role=anon for
        # a prior request inside this test; reset before flipping.
        await db_conn.execute(text("reset role"))
        await db_conn.execute(text("set local role pulse_member"))
        await db_conn.execute(
            text("select set_config('pulse.org_id', :o, true)"),
            {"o": str(membership.org_id)},
        )
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_admin_session] = _override_session
    app.dependency_overrides[get_anon_session] = _override_anon_session
    app.dependency_overrides[get_org_scoped_session] = _override_org_scoped_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_admin_session, None)
    app.dependency_overrides.pop(get_anon_session, None)
    app.dependency_overrides.pop(get_org_scoped_session, None)


# ── seed fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def axiolo_org(db: AsyncSession) -> dict[str, str]:
    """Resolve the Axiolo org row created by migration 0004.

    Idempotent — re-running tests against the same DB volume reuses the
    same id. Returns ``{"id": str, "slug": "axiolo"}``. Every seed
    fixture below depends on this so newly-inserted tenant rows can
    carry a valid ``org_id``.

    Side effect: sets ``pulse.org_id`` to the Axiolo org id on the
    current transaction so the column DEFAULTs that 0004 installs on
    ``responses`` and ``uploads`` populate correctly when tests insert
    raw rows without an explicit ``org_id``. The default is overridden
    by ``become_anon`` / ``become_member`` in tests that need it.
    """
    row = (
        await db.execute(
            text(
                "select id::text, slug from public.organizations "
                "where slug = 'axiolo' limit 1"
            ),
        )
    ).mappings().one_or_none()
    if row is None:
        # Defensive — should never fire because the migration inserts
        # the row. If it does, surface a clear failure instead of an
        # opaque NOT NULL violation on the next seed.
        raise RuntimeError(
            "Axiolo org missing from test DB — has migration 0004 run?"
        )
    await db.execute(
        text("select set_config('pulse.org_id', :o, true)"),
        {"o": row["id"]},
    )
    return dict(row)


async def _seed_engagement(
    db: AsyncSession, *, org_id: str, name: str
) -> dict[str, str]:
    """Seed a client + an engagement owned by it, returning the engagement
    row plus ``name`` (the client's name) + ``client_id``.

    Post-0013 the engagement no longer carries its own ``name`` — the
    owning ``clients`` row does. Tests still read ``["name"]`` to mean the
    customer-facing name, so we surface the client name under that key.
    """
    token = secrets.token_hex(8)
    client = (
        await db.execute(
            text(
                "insert into public.clients (org_id, name) "
                "values (cast(:org as uuid), :n) "
                "on conflict (org_id, name) do update set name = excluded.name "
                "returning id::text"
            ),
            {"org": org_id, "n": name},
        )
    ).mappings().one()
    row = (
        await db.execute(
            text(
                "insert into public.engagements (client_id, token, org_id) "
                "values (cast(:cid as uuid), :t, cast(:org as uuid)) "
                "returning id::text, token"
            ),
            {"cid": client["id"], "t": token, "org": org_id},
        )
    ).mappings().one()
    return {
        **dict(row),
        "name": name,
        "client_id": client["id"],
        "org_id": org_id,
    }


@pytest.fixture
async def seed_client(
    db: AsyncSession, axiolo_org: dict[str, str]
) -> dict[str, str]:
    return await _seed_engagement(db, org_id=axiolo_org["id"], name="Renee")


@pytest.fixture
async def other_seeded_client(
    db: AsyncSession, axiolo_org: dict[str, str]
) -> dict[str, str]:
    return await _seed_engagement(db, org_id=axiolo_org["id"], name="Josh")


@pytest.fixture
async def seed_cards(
    db: AsyncSession,
    seed_client: dict[str, str],
    axiolo_org: dict[str, str],
) -> list[dict[str, str]]:
    """One card per response_type for seed_client. Useful for parametrized tests."""
    types = [
        "confirm-edit", "single-select", "multi-select", "short-text",
        "long-text", "file-upload", "document-link", "contact-share",
    ]
    cards: list[dict[str, str]] = []
    for i, rt in enumerate(types, start=1):
        row = (
            await db.execute(
                text(
                    "insert into public.cards "
                    "(engagement_id, order_index, category, title, context, "
                    " question, response_type, org_id) "
                    "values (cast(:cid as uuid), :idx, 'Test', :t, 'ctx', "
                    "        'q?', :rt, cast(:org as uuid)) "
                    "returning id::text, response_type, title"
                ),
                {
                    "cid": seed_client["id"],
                    "idx": i,
                    "t": f"Card {rt}",
                    "rt": rt,
                    "org": axiolo_org["id"],
                },
            )
        ).mappings().one()
        cards.append(dict(row))
    return cards


@pytest.fixture
async def client_authed(client: AsyncClient, seed_client: dict[str, str]) -> AsyncClient:
    client.headers["X-Pulse-Token"] = seed_client["token"]
    return client


@pytest.fixture
async def seed_user(db: AsyncSession) -> dict[str, str]:
    """Insert a verified operator user with no org membership.

    Models a user who exists but is not (yet) attached to any org —
    can hit ``/api/auth/me`` but every ``/api/admin/*`` call yields
    403. Tests that need the user attached to Axiolo should use
    ``seed_admin_user`` or attach a membership explicitly.

    ``password_hash`` matches the returned ``password`` string so
    login-flow tests can sign in.
    """
    from pulse_api.auth.password import hash_password

    pw = "correct-horse-battery-staple"
    row = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, email_verified_at) "
                "values (:e, :h, :n, now()) "
                "returning id::text, email"
            ),
            {"e": "operator@example.com", "h": hash_password(pw), "n": "Operator"},
        )
    ).mappings().one()
    return {**dict(row), "password": pw}


@pytest.fixture
async def seed_admin_user(
    db: AsyncSession, axiolo_org: dict[str, str]
) -> dict[str, str]:
    """Insert a verified user with an owner membership on Axiolo.

    After PR 2 there is no ``users.is_admin`` column — admin powers come
    from the ``organization_memberships`` row with ``role = 'owner'``.
    ``users.last_active_org_id`` is set to Axiolo so the session cookie
    minted by ``admin_session_cookie`` resolves to a member-scoped
    session without requiring a switch-org call first.

    Returned dict includes ``org_id`` and ``membership_id`` for tests
    that want to assert against the active org or role.
    """
    from pulse_api.auth.password import hash_password

    pw = "admin-pass-12345678"
    row = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, "
                " last_active_org_id, email_verified_at) "
                "values (:e, :h, :n, cast(:org as uuid), now()) "
                "returning id::text, email"
            ),
            {
                "e": "admin@example.com",
                "h": hash_password(pw),
                "n": "Admin",
                "org": axiolo_org["id"],
            },
        )
    ).mappings().one()
    user_id = row["id"]
    membership_row = (
        await db.execute(
            text(
                "insert into public.organization_memberships "
                "(org_id, user_id, role) "
                "values (cast(:org as uuid), cast(:uid as uuid), 'owner') "
                "on conflict (org_id, user_id) do update set role = 'owner' "
                "returning id::text"
            ),
            {"org": axiolo_org["id"], "uid": user_id},
        )
    ).mappings().one()
    return {
        **dict(row),
        "password": pw,
        "org_id": axiolo_org["id"],
        "membership_id": membership_row["id"],
    }


@pytest.fixture
def admin_session_cookie(seed_admin_user: dict[str, str]) -> str:
    """A signed session cookie value for the seeded admin.

    Fast path that bypasses ``/api/auth/login`` — use when the test
    isn't about the login flow itself. Carries ``active_org_id =
    axiolo`` so ``get_current_org_member`` resolves immediately without
    needing to backfill from ``users.last_active_org_id``.
    """
    return encode_session(
        seed_admin_user["id"], seed_admin_user["org_id"]
    )


@pytest.fixture
async def admin_authed(
    client: AsyncClient, admin_session_cookie: str
) -> AsyncClient:
    client.cookies.set(settings.session_cookie_name, admin_session_cookie)
    return client


@pytest.fixture(scope="session")
async def mcp_session_manager() -> AsyncIterator[None]:
    """Run the FastMCP session manager exactly once for the test session.

    The MCP runtime is a singleton (``mcp_server.mcp.session_manager``)
    backed by an anyio task group. Starting it twice — e.g. once from
    ``test_mcp.py`` and again from ``test_mcp_org_scope.py`` — deadlocks
    when the second ``async with mcp.session_manager.run()`` waits for
    the cancel scope of the first task group, which is still alive.

    Putting the fixture in the shared conftest makes both MCP test
    modules share one manager. Both files request this fixture via
    ``mcp_runtime``; one task hosts the manager for the lifetime of
    the suite.
    """
    from pulse_api.mcp import server as _mcp_server

    _ = _mcp_server.mcp.session_manager  # force lazy init
    started = asyncio.Event()
    shutdown = asyncio.Event()

    async def _host() -> None:
        async with _mcp_server.mcp.session_manager.run():
            started.set()
            await shutdown.wait()

    task = asyncio.create_task(_host(), name="mcp-session-manager")
    await started.wait()
    try:
        yield
    finally:
        shutdown.set()
        await task


@pytest.fixture(autouse=True)
def tmp_uploads_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point `settings.upload_dir` at a per-test tempdir so file writes
    can't leak across tests or pollute the production volume. Autouse so
    every test gets the isolation by default; tests that don't touch
    uploads pay nothing for it."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    return upload_dir


@pytest.fixture
def captured_emails(monkeypatch: pytest.MonkeyPatch) -> list[OutboundEmail]:
    """Replace email_module.send_email with an in-memory capture.

    Tests assert against this list to verify what would have been sent —
    no SMTP, no flakiness. Includes the verification + password-reset
    routes' calls automatically because they import send_email from
    `pulse_api.email`.
    """
    captured: list[OutboundEmail] = []

    async def _capture(to: str, subject: str, body: str) -> None:
        captured.append(OutboundEmail(to=to, subject=subject, body=body))

    monkeypatch.setattr(email_module, "send_email", _capture)
    return captured

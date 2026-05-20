"""Endpoint tests for `POST /api/admin/clients/{id}/push-clickup` and
`PATCH /api/admin/clients/{id}/clickup-list`.

Provider HTTP mocked via respx. The on-disk file flow uses the autouse
`tmp_uploads_dir` fixture from conftest.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import crypto, storage
from pulse_api.clickup import CLICKUP_BASE
from pulse_api.config import settings


@pytest.fixture(autouse=True)
def with_encryption_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "encryption_keys", Fernet.generate_key().decode())
    crypto.reset_keys_cache()
    yield
    crypto.reset_keys_cache()


@pytest.fixture
async def connected_admin(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict,
) -> AsyncClient:
    """Seed: the admin has a stored ClickUp token (decrypts to 'tok')."""
    enc = crypto.encrypt("tok")
    await db.execute(
        text("update public.users set clickup_access_token_enc = :t where id = cast(:i as uuid)"),
        {"t": enc, "i": seed_admin_user["id"]},
    )
    await db.flush()
    return admin_authed


@pytest.fixture
async def engagement_with_list(
    db: AsyncSession, seed_client: dict, seed_cards: list[dict],
) -> dict:
    """Bind the seeded engagement to ClickUp list_id=901."""
    await db.execute(
        text(
            "update public.clients set clickup_list_id = '901', clickup_list_name = 'Q3 onboarding' "
            "where id = cast(:c as uuid)"
        ),
        {"c": seed_client["id"]},
    )
    await db.flush()
    return seed_client


# ── PATCH /clients/{id}/clickup-list ──────────────────────────────────────


@pytest.mark.parametrize(
    "input_value, expected_list_id",
    [
        ("https://app.clickup.com/12345/v/li/901234567", "901234567"),
        ("901234567", "901234567"),
        ("https://app.clickup.com/9/v/li/42/board", "42"),
    ],
)
async def test_set_clickup_list_parses_input(
    connected_admin: AsyncClient,
    seed_client: dict,
    db: AsyncSession,
    respx_mock: respx.Router,
    input_value: str,
    expected_list_id: str,
) -> None:
    # Mock the list-name lookup
    respx_mock.get(f"{CLICKUP_BASE}/list/{expected_list_id}").mock(
        return_value=httpx.Response(200, json={"id": expected_list_id, "name": "Q3 onboarding"})
    )

    r = await connected_admin.patch(
        f"/api/admin/clients/{seed_client['id']}/clickup-list",
        json={"url_or_id": input_value},
    )
    assert r.status_code == 200
    assert r.json()["clickup_list_id"] == expected_list_id
    assert r.json()["clickup_list_name"] == "Q3 onboarding"


async def test_set_clickup_list_empty_clears_binding(
    connected_admin: AsyncClient,
    seed_client: dict,
    db: AsyncSession,
) -> None:
    # Pre-set a list_id, then clear it
    await db.execute(
        text(
            "update public.clients set clickup_list_id = '777', clickup_list_name = 'old' "
            "where id = cast(:c as uuid)"
        ),
        {"c": seed_client["id"]},
    )
    await db.flush()

    r = await connected_admin.patch(
        f"/api/admin/clients/{seed_client['id']}/clickup-list",
        json={"url_or_id": ""},
    )
    assert r.status_code == 200
    assert r.json()["clickup_list_id"] is None


async def test_set_clickup_list_unknown_engagement_404(connected_admin: AsyncClient) -> None:
    import uuid
    r = await connected_admin.patch(
        f"/api/admin/clients/{uuid.uuid4()}/clickup-list",
        json={"url_or_id": "12345"},
    )
    assert r.status_code == 404


# ── POST /clients/{id}/push-clickup ────────────────────────────────────────


async def test_push_creates_tasks_for_all_cards(
    connected_admin: AsyncClient,
    engagement_with_list: dict,
    seed_cards: list[dict],
    respx_mock: respx.Router,
    db: AsyncSession,
) -> None:
    # Mock create_task for every call. ClickUp returns a unique task_id
    # via a respx side effect.
    counter = {"n": 0}

    def _create(request):
        counter["n"] += 1
        return httpx.Response(200, json={"id": f"task{counter['n']}", "name": "x"})

    respx_mock.post(f"{CLICKUP_BASE}/list/901/task").mock(side_effect=_create)

    r = await connected_admin.post(
        f"/api/admin/clients/{engagement_with_list['id']}/push-clickup"
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert len(body["created"]) == 8  # one per seeded card
    assert body["updated"] == []
    assert body["errors"] == []

    # clickup_task_id back-references stored
    task_ids = (
        await db.execute(
            text(
                "select clickup_task_id from public.cards where client_id = cast(:c as uuid) "
                "order by order_index"
            ),
            {"c": engagement_with_list["id"]},
        )
    ).scalars().all()
    assert all(t is not None for t in task_ids)


async def test_re_push_updates_instead_of_creating(
    connected_admin: AsyncClient,
    engagement_with_list: dict,
    seed_cards: list[dict],
    respx_mock: respx.Router,
    db: AsyncSession,
) -> None:
    # Pre-set clickup_task_id on every card → push should UPDATE not CREATE.
    for c in seed_cards:
        await db.execute(
            text("update public.cards set clickup_task_id = :t where id = cast(:i as uuid)"),
            {"t": f"existing-{c['order_index']}", "i": c["id"]},
        )
    await db.flush()

    create_route = respx_mock.post(f"{CLICKUP_BASE}/list/901/task").mock(
        return_value=httpx.Response(200, json={"id": "should-not-be-called"})
    )
    update_route = respx_mock.put(host="api.clickup.com", path__startswith="/api/v2/task/").mock(
        return_value=httpx.Response(200, json={"id": "x", "name": "x"})
    )

    r = await connected_admin.post(
        f"/api/admin/clients/{engagement_with_list['id']}/push-clickup"
    )
    assert r.status_code == 200
    assert len(r.json()["updated"]) == 8
    assert r.json()["created"] == []
    assert not create_route.called
    assert update_route.call_count == 8


async def test_push_uploads_attachments_for_file_cards(
    connected_admin: AsyncClient,
    engagement_with_list: dict,
    seed_cards: list[dict],
    respx_mock: respx.Router,
    db: AsyncSession,
    tmp_uploads_dir,
) -> None:
    # Seed an upload on the file-upload card with a real file on disk
    file_card = next(c for c in seed_cards if c["response_type"] == "file-upload")
    rel = storage.build_storage_path(
        client_id=engagement_with_list["id"], card_id=file_card["id"], filename="deck.pdf"
    )
    storage.write_upload(relative_path=rel, content=b"fake pdf bytes")
    await db.execute(
        text(
            "insert into public.uploads "
            "(card_id, client_id, file_name, file_size_bytes, storage_path, mime_type) "
            "values (cast(:k as uuid), cast(:c as uuid), 'deck.pdf', 14, :sp, 'application/pdf')"
        ),
        {"k": file_card["id"], "c": engagement_with_list["id"], "sp": rel},
    )
    await db.flush()

    # Mock task create + attachment upload
    respx_mock.post(f"{CLICKUP_BASE}/list/901/task").mock(
        return_value=httpx.Response(200, json={"id": "task-file"})
    )
    att_route = respx_mock.post(f"{CLICKUP_BASE}/task/task-file/attachment").mock(
        return_value=httpx.Response(200, json={"id": "att1"})
    )

    r = await connected_admin.post(
        f"/api/admin/clients/{engagement_with_list['id']}/push-clickup"
    )
    assert r.status_code == 200
    assert r.json()["attached"] == 1
    assert att_route.called
    # Verify the multipart body actually contained our test bytes
    sent_body = att_route.calls.last.request.read()
    assert b"fake pdf bytes" in sent_body


async def test_push_without_token_returns_400(
    admin_authed: AsyncClient,
    engagement_with_list: dict,
) -> None:
    """Admin is logged in but hasn't connected ClickUp."""
    r = await admin_authed.post(
        f"/api/admin/clients/{engagement_with_list['id']}/push-clickup"
    )
    assert r.status_code == 400
    assert "not connected" in r.json()["detail"].lower()


async def test_push_without_list_id_returns_400(
    connected_admin: AsyncClient,
    seed_client: dict,
) -> None:
    """Operator connected but the engagement has no clickup_list_id."""
    r = await connected_admin.post(
        f"/api/admin/clients/{seed_client['id']}/push-clickup"
    )
    assert r.status_code == 400
    assert "clickup_list_id" in r.json()["detail"]


async def test_push_partial_failure_collects_errors(
    connected_admin: AsyncClient,
    engagement_with_list: dict,
    seed_cards: list[dict],
    respx_mock: respx.Router,
) -> None:
    """ClickUp returns 400 on some create calls (e.g. bad status string).
    Other cards still get pushed; errors are surfaced per-card."""
    counter = {"n": 0}

    def _flaky_create(request):
        counter["n"] += 1
        # Half succeed, half 400 with an "invalid status" message
        if counter["n"] % 2 == 0:
            return httpx.Response(400, json={"err": "Status 'Axiolo Review' is not valid for this list"})
        return httpx.Response(200, json={"id": f"task{counter['n']}"})

    respx_mock.post(f"{CLICKUP_BASE}/list/901/task").mock(side_effect=_flaky_create)

    r = await connected_admin.post(
        f"/api/admin/clients/{engagement_with_list['id']}/push-clickup"
    )
    assert r.status_code == 200  # the route itself succeeded
    body = r.json()
    assert len(body["created"]) == 4
    assert len(body["errors"]) == 4
    # Each error has a card_id and a message containing the invalid status text
    for e in body["errors"]:
        assert "card_id" in e
        assert "Status" in e["error"]


async def test_push_rejects_anonymous(client: AsyncClient, engagement_with_list: dict) -> None:
    r = await client.post(f"/api/admin/clients/{engagement_with_list['id']}/push-clickup")
    assert r.status_code == 401

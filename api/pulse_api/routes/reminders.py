"""Public (unauthenticated) reminder endpoints.

The unsubscribe link in recipient invite/reminder emails lands here. The
signed ``pulse-reminder-unsubscribe`` token IS the credential — it names
exactly one recipient and can't be forged without ``SESSION_SECRET`` — so
the route flips that recipient's ``unsubscribed_at`` on a BYPASSRLS admin
session (the same cross-org token-redemption path invite-accept uses;
there is no operator or recipient session here).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import reminders
from pulse_api.db import get_admin_session

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


class UnsubscribeRequest(BaseModel):
    token: str


@router.post("/unsubscribe")
async def unsubscribe(
    req: UnsubscribeRequest,
    session: AsyncSession = Depends(get_admin_session),
) -> dict[str, bool]:
    """Stop sending reminders to the recipient named by the signed token.
    Idempotent — re-clicking keeps the original ``unsubscribed_at``. A
    missing/forged/expired token is a 400; a valid token for an
    already-deleted recipient is a harmless no-op (still ``ok``)."""
    recipient_id = reminders.parse_unsubscribe_token(req.token)
    if recipient_id is None:
        raise HTTPException(status_code=400, detail="invalid or expired link")
    await session.execute(
        text(
            "update public.recipients "
            "set unsubscribed_at = coalesce(unsubscribed_at, now()) "
            "where id = cast(:rid as uuid)"
        ),
        {"rid": recipient_id},
    )
    await session.commit()
    return {"ok": True}

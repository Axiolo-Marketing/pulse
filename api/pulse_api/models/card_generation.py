import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, Numeric
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class CardGeneration(SQLModel, table=True):
    """One reactive-cards generation attempt — lifecycle record, dedup
    lock, and LLM cost ledger entry (migration 0017).

    Written only by the generation engine (PR 2), on a BYPASSRLS session
    — the ``pulse_member``/``pulse_anon`` grants are SELECT-only. The
    unique ``(response_id, trigger_hash)`` constraint is the idempotency
    claim: a re-save of an identical correction can't create a second
    generation for the same triggering response.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization (cost-ledger aggregation key).
        engagement_id: Owning engagement.
        recipient_id: The respondent whose correction triggered this call
            — also the audience for any cards it produces.
        response_id: The correction `Response` that triggered generation.
        card_id: The triggering card (the one being corrected).
        trigger_hash: sha256 of the normalized correction text — the
            other half of the idempotency key.
        status: One of `pending|completed|skipped|failed`.
        model: The Anthropic model id used for this call, or `None` if
            the call never reached the API.
        error: Human-readable failure reason when `status == "failed"`.
        input_tokens, output_tokens: Token usage from the API response's
            `usage` object, or `None` when no response was received.
        cost_usd: Dollar estimate computed at call time from the
            module-level model price map. `None` for unknown models or
            calls with no usage to price.
        created_card_ids: IDs of the cards this generation produced (may
            be empty for `skipped`/`failed`).
        created_at: Insert timestamp (naive UTC).
        completed_at: When the generation reached a terminal status.
    """

    __tablename__ = "card_generations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    engagement_id: uuid.UUID = Field(foreign_key="engagements.id", index=True)
    recipient_id: uuid.UUID = Field(foreign_key="recipients.id", index=True)
    response_id: uuid.UUID = Field(foreign_key="responses.id")
    card_id: uuid.UUID = Field(foreign_key="cards.id")
    trigger_hash: str
    status: str = "pending"
    model: str | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = Field(default=None, sa_column=Column(Numeric(10, 6)))
    created_card_ids: list[uuid.UUID] = Field(
        default_factory=list, sa_column=Column(ARRAY(UUID(as_uuid=True)))
    )
    created_at: datetime = Field(default_factory=utcnow_naive)
    completed_at: datetime | None = None

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    """Naive UTC datetime — replaces deprecated `datetime.utcnow()`.

    Why naive: SQLModel's default mapping for `datetime` is
    `TIMESTAMP WITHOUT TIME ZONE`, even though our Postgres columns are
    `timestamptz`. asyncpg refuses tz-aware values for that prepared-statement
    type. Postgres still stores UTC correctly because the column is
    timestamptz — the session's default tz (UTC) is applied at write time.
    Reads come back tz-naive in UTC for the same reason.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def as_naive_utc(value: datetime) -> datetime:
    """Coerce a datetime to naive UTC so it compares against ``utcnow_naive``.

    Most columns map through ``TIMESTAMP WITHOUT TIME ZONE`` and read back
    naive, but some ``timestamptz`` columns surfaced through the ORM come
    back tz-aware (asyncpg attaches the session tz). Mixing the two in a
    ``<`` comparison raises ``TypeError``. This normalizes either shape to
    naive UTC.

    Args:
        value: A naive-UTC or tz-aware datetime.

    Returns:
        The equivalent naive-UTC datetime.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)

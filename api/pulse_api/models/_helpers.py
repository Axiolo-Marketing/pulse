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

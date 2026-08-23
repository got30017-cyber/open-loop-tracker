from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return current UTC as a naive value for consistent SQLite storage."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_naive_utc(value: datetime) -> datetime:
    """Normalize an API clock value to the naive UTC used by persistence."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)

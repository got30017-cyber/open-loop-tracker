from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return current UTC as a naive value for consistent SQLite storage."""
    return datetime.now(UTC).replace(tzinfo=None)

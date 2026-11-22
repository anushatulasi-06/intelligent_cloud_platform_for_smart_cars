"""Small time helpers shared by the C1 services."""
from datetime import datetime, timezone


def as_utc(value: datetime | None) -> datetime | None:
    """Datetimes from query strings often have no timezone; treat those as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def start_of_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

"""Turn models into JSON-ready dicts. Datetimes go out as UTC ISO strings with 'Z'."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import SQLModel


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def parse_dt(value: str | datetime | None) -> datetime | None:
    """Accept ISO strings with or without offset (naive = UTC); return aware UTC."""
    if value is None or value == "":
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_dict(obj: SQLModel | None, **extra: Any) -> dict | None:
    if obj is None:
        return None
    out = {}
    for key, value in obj.model_dump().items():
        out[key] = iso(value) if isinstance(value, datetime) else value
    out.update(extra)
    return out

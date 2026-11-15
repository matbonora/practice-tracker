"""Tables and the rules that belong to them (tell, don't ask).

All datetimes are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .errors import Invalid

ITEM_STATUSES = ("want", "learning", "polishing", "repertoire", "retired")
LOG_TYPES = ("insight", "problem", "idea", "task")
FIELD_TYPES = ("text", "number", "choice", "multi")

REVIEW_FIRST_DAYS = 2
REVIEW_MAX_DAYS = 60
REVIEW_FACTORS = {2: 1.2, 3: 2.0, 4: 3.0}  # rating 1 resets to 1 day


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(default: Any = None) -> Any:
    return Field(default_factory=lambda: (list(default) if isinstance(default, list) else dict(default or {})),
                 sa_column=Column(JSON, nullable=False))


class Hobby(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: str = "#4f6bed"
    weekly_goal_min: int = 120
    archived_at: Optional[datetime] = None


class Kind(SQLModel, table=True):
    """Template for items: which fields, focus options and segment values exist."""

    id: Optional[int] = Field(default=None, primary_key=True)
    hobby_id: int = Field(foreign_key="hobby.id", index=True)
    name: str
    item_fields: list[dict] = _json([])
    segment_fields: list[dict] = _json([])
    focus_options: list[str] = _json([])
    review_enabled: bool = False

    def check_item_attributes(self, values: dict) -> dict:
        return _check_fields(self.item_fields, values, f"item field of kind '{self.name}'")

    def check_segment_params(self, values: dict) -> dict:
        return _check_fields(self.segment_fields, values, f"segment value of kind '{self.name}'")

    def check_focus(self, focus: list[str]) -> list[str]:
        if self.focus_options:
            unknown = [f for f in focus if f not in self.focus_options]
            if unknown:
                raise Invalid(f"unknown focus {unknown} for kind '{self.name}'; options: {self.focus_options}")
        return list(focus)


def _check_fields(defs: list[dict], values: dict, what: str) -> dict:
    by_name = {d["name"]: d for d in defs}
    out = {}
    for key, value in (values or {}).items():
        d = by_name.get(key)
        if d is None:
            raise Invalid(f"unknown {what}: '{key}'; known: {sorted(by_name)}")
        kind, options = d.get("type", "text"), d.get("options")
        if value is None:
            out[key] = None
            continue
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise Invalid(f"{what} '{key}' must be a number")
        elif kind == "choice":
            if options and value not in options:
                raise Invalid(f"{what} '{key}' must be one of {options}")
        elif kind == "multi":
            if not isinstance(value, list) or (options and any(v not in options for v in value)):
                raise Invalid(f"{what} '{key}' must be a list from {options}")
        elif not isinstance(value, str):
            raise Invalid(f"{what} '{key}' must be text")
        out[key] = value
    return out


class Area(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hobby_id: int = Field(foreign_key="hobby.id", index=True)
    name: str
    target_share: float = 0.0  # 0..1
    default_kind_id: Optional[int] = Field(default=None, foreign_key="kind.id")
    is_warmup: bool = False
    sort: int = 0
    archived_at: Optional[datetime] = None


class Item(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    area_id: int = Field(foreign_key="area.id", index=True)
    kind_id: Optional[int] = Field(default=None, foreign_key="kind.id")
    title: str
    attributes: dict = _json({})
    status: str = "learning"
    review_due: Optional[datetime] = None
    review_interval_days: Optional[float] = None
    review_lapses: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    archived_at: Optional[datetime] = None

    def set_status(self, status: str, now: datetime, review_enabled: bool) -> None:
        if status not in ITEM_STATUSES:
            raise Invalid(f"status must be one of {ITEM_STATUSES}")
        entering_repertoire = status == "repertoire" and self.status != "repertoire"
        self.status = status
        if entering_repertoire and review_enabled:
            self.review_interval_days = REVIEW_FIRST_DAYS
            self.review_due = now + timedelta(days=REVIEW_FIRST_DAYS)
        if status == "retired":
            self.review_due = None

    def review(self, rating: int, now: datetime) -> None:
        """Simple spaced review: 1 resets to 1 day, 2/3/4 multiply the interval."""
        if rating not in (1, 2, 3, 4):
            raise Invalid("review rating must be 1 (again), 2 (hard), 3 (good) or 4 (easy)")
        interval = self.review_interval_days or REVIEW_FIRST_DAYS
        if rating == 1:
            interval = 1
            self.review_lapses += 1
        else:
            interval = max(1.0, interval * REVIEW_FACTORS[rating])
        self.review_interval_days = min(REVIEW_MAX_DAYS, round(interval, 1))
        self.review_due = now + timedelta(days=self.review_interval_days)


class PracticeSession(SQLModel, table=True):
    """A timed block. ended_at is None while the timer runs."""

    __tablename__ = "session"
    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow, index=True)
    ended_at: Optional[datetime] = Field(default=None, index=True)
    hobby_id: Optional[int] = Field(default=None, foreign_key="hobby.id")
    source: str = "manual"
    preset: dict = _json({})  # {"area_id": .., "item_id": ..} given at start
    splits: list = _json([])  # ISO timestamps from "next block"
    rating: Optional[int] = None
    note: str = ""

    @property
    def running(self) -> bool:
        return self.ended_at is None

    def minutes(self, now: datetime | None = None) -> float:
        end = self.ended_at or now or utcnow()
        return max(0.0, (end - self.started_at).total_seconds() / 60)

    def is_forgotten(self, now: datetime, forgotten_hours: float) -> bool:
        return self.running and self.minutes(now) > forgotten_hours * 60

    def split(self, now: datetime) -> None:
        if not self.running:
            raise Invalid("timer is not running")
        self.splits = [*self.splits, now.isoformat()]

    def stop(self, now: datetime, trim_s: float = 0) -> None:
        if not self.running:
            raise Invalid("timer is not running")
        self.ended_at = max(self.started_at, now - timedelta(seconds=trim_s or 0))


class Segment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    area_id: int = Field(foreign_key="area.id")
    item_id: Optional[int] = Field(default=None, foreign_key="item.id", index=True)
    minutes: float
    focus: list[str] = _json([])
    params: dict = _json({})
    review_rating: Optional[int] = None
    note: str = ""
    next_focus: str = ""


class Milestone(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hobby_id: int = Field(foreign_key="hobby.id")
    item_id: Optional[int] = Field(default=None, foreign_key="item.id")
    date: datetime = Field(default_factory=utcnow)
    title: str
    note: str = ""


class LogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hobby_id: Optional[int] = Field(default=None, foreign_key="hobby.id")
    item_id: Optional[int] = Field(default=None, foreign_key="item.id")
    created_at: datetime = Field(default_factory=utcnow)
    type: str = "insight"
    text: str
    done_at: Optional[datetime] = None
    source: str = "manual"


class Attachment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_type: str  # item | segment | milestone | log
    owner_id: int
    path: str
    mime: str = "application/octet-stream"
    caption: str = ""
    created_at: datetime = Field(default_factory=utcnow)

"""MCP server for agents. Task-shaped tools; each one is a thin wrapper around Tracker.

Everything written here is attributed to source 'agent'. There are no delete tools:
structure is archived, never removed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Callable, Iterator, Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from .errors import Ambiguous, DomainError
from .models import Hobby, PracticeSession
from .serialize import to_dict
from .service import Tracker
from .stats import Stats

SOURCE = "agent"

INSTRUCTIONS = """\
Practice tracker for hobbies (piano, drawing, ...). Start with get_overview to learn the
hobbies, areas, kinds (their fields and focus options) and items. Hobby, area and item
arguments accept an id or a name; if a name is ambiguous the error lists candidates - ask
the user or pass an id. Durations are minutes. Times are ISO 8601 (local offset or UTC).
Nothing can be deleted through these tools; use archived=true or status='retired'.
"""


class SegmentArg(BaseModel):
    area: Optional[int | str] = Field(None, description="Area id or name; optional if item is given")
    item: Optional[int | str] = Field(None, description="Item id or name")
    minutes: float = Field(gt=0)
    focus: list[str] = Field(default_factory=list, description="Values from the kind's focus_options")
    params: dict[str, Any] = Field(default_factory=dict, description="Values for the kind's segment_fields")
    review_rating: Optional[int] = Field(None, ge=1, le=4,
                                         description="Repertoire play-through: 1 again, 2 hard, 3 good, 4 easy")
    note: str = ""
    next_focus: str = Field("", description="What to work on next time for this item")


class FieldArg(BaseModel):
    name: str
    type: Literal["text", "number", "choice", "multi"] = "text"
    options: Optional[list[str]] = None


def build_mcp(make_tracker: Callable[[], Tracker]) -> MCPServer:
    mcp = MCPServer(name="practice-tracker", instructions=INSTRUCTIONS, version="0.1.0")

    @contextmanager
    def session() -> Iterator[Tracker]:
        t = make_tracker()
        try:
            yield t
        except Ambiguous as e:
            raise ToolError(f"{e} Candidates: {e.candidates}") from e
        except DomainError as e:
            raise ToolError(str(e)) from e
        finally:
            t.db.close()

    def segs(items: list[SegmentArg]) -> list[dict]:
        return [s.model_dump() for s in items]

    @mcp.tool()
    def get_overview(hobby: Optional[str] = None) -> dict:
        """Hobbies with areas (target and 14-day actual share), kinds (fields, focus options), active
        items, weekly streak; plus running timer, inbox count, due reviews and open tasks."""
        with session() as t:
            return t.overview(t.resolve_hobby(hobby) if hobby else None)

    @mcp.tool()
    def timer(action: Literal["start", "stop", "status", "discard", "split"],
              hobby: Optional[str] = None, area: Optional[str] = None, item: Optional[str] = None,
              note: str = "") -> dict:
        """Control the single practice timer. start takes optional hobby/area/item; stop puts the
        session in the inbox unless it was started with an item (then it is assigned)."""
        with session() as t:
            if action == "start":
                return t.timer_start(hobby, area, item, note, source=SOURCE)
            if action == "stop":
                return t.timer_stop()
            if action == "discard":
                return t.timer_discard()
            if action == "split":
                return t.timer_split()
            return t.timer_status()

    @mcp.tool()
    def log_session(segments: list[SegmentArg], hobby: Optional[str] = None,
                    started_at: Optional[str] = None, ended_at: Optional[str] = None,
                    minutes: Optional[float] = None, rating: Optional[int] = None, note: str = "") -> dict:
        """Record a finished session in one call. Without started_at the session ends now (or at
        ended_at) and lasts `minutes` or the sum of segment minutes. rating = session quality 1..5."""
        with session() as t:
            s = t.log_session(segs(segments), hobby, started_at, ended_at, minutes, rating, note, SOURCE)
            return t.session_detail(s)

    @mcp.tool()
    def list_inbox() -> list[dict]:
        """Finished sessions (timer, MIDI auto-timer) that still need areas/items assigned."""
        with session() as t:
            return [t.session_detail(s) for s in t.inbox()]

    @mcp.tool()
    def assign_session(session_id: int, segments: list[SegmentArg], rating: Optional[int] = None,
                       note: Optional[str] = None) -> dict:
        """Set the segments of a finished session (replaces existing ones). Minutes may not exceed
        the session length; the rest stays 'unassigned'."""
        with session() as t:
            return t.session_detail(t.assign(t.get(PracticeSession, session_id), segs(segments), rating, note))

    @mcp.tool()
    def recent_sessions(days: int = 7, hobby: Optional[str] = None) -> list[dict]:
        """Sessions of the last N days with their segments, newest first."""
        with session() as t:
            start = t.clock() - timedelta(days=days)
            h = t.resolve_hobby(hobby) if hobby else None
            return [t.session_detail(s) for s in t.list_sessions(start, None, h)]

    @mcp.tool()
    def upsert_hobby(name: str, new_name: Optional[str] = None, color: Optional[str] = None,
                     weekly_goal_min: Optional[int] = None, archived: Optional[bool] = None) -> dict:
        """Create a hobby, or update it if `name` exists (use new_name to rename)."""
        with session() as t:
            existing = _find(t, lambda: t.resolve_hobby(name), name)
            fields = dict(name=new_name or name, color=color, weekly_goal_min=weekly_goal_min, archived=archived)
            return to_dict(t.save_hobby(existing, **fields))

    @mcp.tool()
    def upsert_kind(hobby: str, name: str, new_name: Optional[str] = None,
                    item_fields: Optional[list[FieldArg]] = None, segment_fields: Optional[list[FieldArg]] = None,
                    focus_options: Optional[list[str]] = None, review_enabled: Optional[bool] = None) -> dict:
        """Create or update an item template: item_fields (per item), segment_fields (per practice
        segment, e.g. bpm), focus_options and whether spaced review applies (repertoire pieces)."""
        with session() as t:
            h = t.resolve_hobby(hobby)
            existing = _find(t, lambda: t.resolve_kind(name, h), name)
            dump = (lambda fs: [f.model_dump(exclude_none=True) for f in fs] if fs is not None else None)
            return to_dict(t.save_kind(h, existing, name=new_name or name, item_fields=dump(item_fields),
                                       segment_fields=dump(segment_fields), focus_options=focus_options,
                                       review_enabled=review_enabled))

    @mcp.tool()
    def upsert_area(hobby: str, name: str, new_name: Optional[str] = None,
                    target_share: Optional[float] = None, default_kind: Optional[str] = None,
                    is_warmup: Optional[bool] = None, sort: Optional[int] = None,
                    archived: Optional[bool] = None) -> dict:
        """Create or update a practice area. target_share is 0..1 (e.g. 0.15 = 15 % of practice time)."""
        with session() as t:
            h = t.resolve_hobby(hobby)
            existing = _find(t, lambda: t.resolve_area(name, h), name)
            return to_dict(t.save_area(h, existing, name=new_name or name, target_share=target_share,
                                       default_kind=default_kind, is_warmup=is_warmup, sort=sort,
                                       archived=archived))

    @mcp.tool()
    def upsert_item(title: str, area: Optional[str] = None, hobby: Optional[str] = None,
                    new_title: Optional[str] = None, kind: Optional[str] = None,
                    attributes: Optional[dict[str, Any]] = None,
                    status: Optional[Literal["want", "learning", "polishing", "repertoire", "retired"]] = None,
                    archived: Optional[bool] = None) -> dict:
        """Create an item (piece, scale, artwork, ...) in an area, or update it if the title exists.
        Setting status 'repertoire' starts spaced review for kinds with review enabled."""
        with session() as t:
            h = t.resolve_hobby(hobby) if hobby else None
            a = t.resolve_area(area, h) if area else None
            existing = _find(t, lambda: t.resolve_item(title, h, a), title)
            if existing is None and a is None:
                raise ToolError("new item needs an area")
            fields = dict(title=new_title or title, kind=kind, attributes=attributes, status=status,
                          archived=archived)
            if existing is not None:
                return to_dict(t.save_item(existing, area=a, **fields))
            return to_dict(t.save_item(None, area=a, **fields))

    @mcp.tool()
    def get_item(item: str, hobby: Optional[str] = None) -> dict:
        """Item details: attributes, status, review schedule, total minutes, last next-focus note, history."""
        with session() as t:
            return t.item_detail(t.resolve_item(item, t.resolve_hobby(hobby) if hobby else None))

    @mcp.tool()
    def review_item(item: str, rating: int) -> dict:
        """Record a repertoire play-through without a session: 1 again, 2 hard, 3 good, 4 easy."""
        with session() as t:
            return to_dict(t.review_item(t.resolve_item(item), rating))

    @mcp.tool()
    def add_log_entry(text: str, type: Literal["insight", "problem", "idea", "task"] = "insight",
                      hobby: Optional[str] = None, item: Optional[str] = None) -> dict:
        """Rapid-log line for the log book. 'task' entries show as open until done."""
        with session() as t:
            return to_dict(t.add_log(text, type, hobby, item, source=SOURCE))

    @mcp.tool()
    def add_milestone(title: str, hobby: Optional[str] = None, item: Optional[str] = None,
                      note: str = "", date: Optional[str] = None) -> dict:
        """Dated achievement (finished piece, finished painting). Files are attached via the app."""
        with session() as t:
            return to_dict(t.add_milestone(title, hobby, item, note, date))

    @mcp.tool()
    def get_stats(period: Literal["day", "week", "month"] = "week",
                  group: Literal["hobby", "area", "item"] = "area", days: int = 28,
                  hobby: Optional[str] = None) -> dict:
        """Minutes per period and group for the last N days, plus area split and streak per hobby."""
        with session() as t:
            st = Stats(t)
            h = t.resolve_hobby(hobby) if hobby else None
            end = st.today() + timedelta(days=1)
            hobbies = [h] if h else _active_hobbies(t)
            return {
                "minutes": st.minutes(end - timedelta(days=days), end, period, group, h),
                "split_14d": {x.name: st.area_split(x) for x in hobbies},
                "streak": {x.name: st.streak(x) for x in hobbies},
            }

    return mcp


def _find(t: Tracker, lookup: Callable[[], Any], name: str):
    """Upsert lookup: the record whose name is exactly `name` (or id), else None."""
    try:
        found = lookup()
    except Ambiguous as e:
        # _resolve prefers a unique exact match, so here: no match, or only partial ones -> new record
        if sum(c["name"].casefold() == name.strip().casefold() for c in e.candidates) > 1:
            raise
        return None
    label = getattr(found, "title", None) or found.name
    if str(name).strip().isdigit() or label.casefold() == name.strip().casefold():
        return found
    return None


def _active_hobbies(t: Tracker) -> list[Hobby]:
    from sqlmodel import select

    return list(t.db.exec(select(Hobby).where(Hobby.archived_at.is_(None))).all())


"""Domain service: the one code path behind the REST API, the MCP server and the UI.

Routes and MCP tools stay thin: parse input, call one method here, return the result.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

from sqlmodel import Session, SQLModel, col, select

from .config import Settings
from .errors import Ambiguous, Conflict, Invalid, NotFound
from .models import (
    LOG_TYPES,
    Area,
    Attachment,
    Hobby,
    Item,
    Kind,
    LogEntry,
    Milestone,
    PracticeSession,
    Segment,
    utcnow,
)
from .serialize import parse_dt, to_dict

M = TypeVar("M", bound=SQLModel)
Ref = int | str | None


class Tracker:
    def __init__(self, db: Session, settings: Settings, clock: Callable[[], datetime] = utcnow):
        self.db = db
        self.settings = settings
        self.clock = clock

    # ------------------------------------------------------------------ lookup

    def get(self, model: type[M], id_: int) -> M:
        obj = self.db.get(model, id_)
        if obj is None:
            raise NotFound(f"{model.__name__} {id_} not found")
        return obj

    def _resolve(self, model: type[M], ref: Ref, label: Callable[[M], str], query) -> M:
        """Find by id, exact name (case-insensitive) or unique substring."""
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
            return self.get(model, int(ref))
        if not ref:
            raise Invalid(f"{model.__name__.lower()} is required")
        needle = str(ref).strip().casefold()
        rows = list(self.db.exec(query).all())
        exact = [r for r in rows if label(r).casefold() == needle]
        hits = exact or [r for r in rows if needle in label(r).casefold()]
        if len(hits) == 1:
            return hits[0]
        candidates = [{"id": r.id, "name": label(r)} for r in (hits or rows)][:20]
        what = "several" if hits else "no"
        raise Ambiguous(f"'{ref}' matches {what} {model.__name__.lower()}s; pass an id", candidates)

    def resolve_hobby(self, ref: Ref) -> Hobby:
        return self._resolve(Hobby, ref, lambda h: h.name, select(Hobby).where(Hobby.archived_at.is_(None)))

    def resolve_area(self, ref: Ref, hobby: Hobby | None = None) -> Area:
        q = select(Area).where(Area.archived_at.is_(None))
        if hobby:
            q = q.where(Area.hobby_id == hobby.id)
        return self._resolve(Area, ref, lambda a: a.name, q)

    def resolve_item(self, ref: Ref, hobby: Hobby | None = None, area: Area | None = None) -> Item:
        q = select(Item).where(Item.archived_at.is_(None))
        if area:
            q = q.where(Item.area_id == area.id)
        elif hobby:
            q = q.join(Area).where(Area.hobby_id == hobby.id)
        return self._resolve(Item, ref, lambda i: i.title, q)

    def resolve_kind(self, ref: Ref, hobby: Hobby) -> Kind:
        return self._resolve(Kind, ref, lambda k: k.name, select(Kind).where(Kind.hobby_id == hobby.id))

    def kind_for(self, item: Item | None, area: Area) -> Kind | None:
        kind_id = (item.kind_id if item else None) or area.default_kind_id
        return self.db.get(Kind, kind_id) if kind_id else None

    # --------------------------------------------------------------- structure

    def save_hobby(self, hobby: Hobby | None = None, **fields: Any) -> Hobby:
        hobby = hobby or Hobby(name=fields.get("name") or "")
        if not (fields.get("name") or hobby.name):
            raise Invalid("hobby name is required")
        for key in ("name", "color", "weekly_goal_min"):
            if fields.get(key) is not None:
                setattr(hobby, key, fields[key])
        if fields.get("archived") is not None:
            hobby.archived_at = self.clock() if fields["archived"] else None
        return self._commit(hobby)

    def save_kind(self, hobby: Hobby, kind: Kind | None = None, **fields: Any) -> Kind:
        kind = kind or Kind(hobby_id=hobby.id, name=fields.get("name") or "")
        if not (fields.get("name") or kind.name):
            raise Invalid("kind name is required")
        for key in ("name", "item_fields", "segment_fields", "focus_options", "review_enabled"):
            if fields.get(key) is not None:
                setattr(kind, key, fields[key])
        for defs in (kind.item_fields, kind.segment_fields):
            for d in defs:
                if "name" not in d or d.get("type", "text") not in ("text", "number", "choice", "multi"):
                    raise Invalid(f"bad field definition {d}; need name and type text|number|choice|multi")
        return self._commit(kind)

    def save_area(self, hobby: Hobby, area: Area | None = None, **fields: Any) -> Area:
        area = area or Area(hobby_id=hobby.id, name=fields.get("name") or "")
        if not (fields.get("name") or area.name):
            raise Invalid("area name is required")
        for key in ("name", "target_share", "is_warmup", "sort"):
            if fields.get(key) is not None:
                setattr(area, key, fields[key])
        if not 0 <= area.target_share <= 1:
            raise Invalid("target_share must be between 0 and 1")
        if fields.get("default_kind") is not None:
            area.default_kind_id = self.resolve_kind(fields["default_kind"], hobby).id
        if fields.get("archived") is not None:
            area.archived_at = self.clock() if fields["archived"] else None
        return self._commit(area)

    def save_item(self, item: Item | None = None, *, area: Area | None = None, **fields: Any) -> Item:
        if item is None:
            if area is None or not fields.get("title"):
                raise Invalid("new item needs area and title")
            item = Item(area_id=area.id, kind_id=area.default_kind_id, title=fields["title"], status="want")
            fields.setdefault("status", "learning")
        elif area is not None:
            item.area_id = area.id
        area = self.get(Area, item.area_id)
        hobby = self.get(Hobby, area.hobby_id)
        if fields.get("kind") is not None:
            item.kind_id = self.resolve_kind(fields["kind"], hobby).id
        kind = self.kind_for(item, area)
        if fields.get("title"):
            item.title = fields["title"]
        if fields.get("attributes") is not None:
            merged = {**item.attributes, **fields["attributes"]}
            item.attributes = kind.check_item_attributes(merged) if kind else {}
        if fields.get("status") is not None:
            item.set_status(fields["status"], self.clock(), bool(kind and kind.review_enabled))
        if fields.get("archived") is not None:
            item.archived_at = self.clock() if fields["archived"] else None
        return self._commit(item)

    def list_items(self, hobby: Hobby | None = None, include_archived: bool = False) -> list[Item]:
        q = select(Item).join(Area)
        if hobby:
            q = q.where(Area.hobby_id == hobby.id)
        if not include_archived:
            q = q.where(Item.archived_at.is_(None))
        return list(self.db.exec(q.order_by(Area.sort, Item.title)).all())

    def item_detail(self, item: Item) -> dict:
        rows = self.db.exec(
            select(Segment, PracticeSession)
            .join(PracticeSession, PracticeSession.id == Segment.session_id)
            .where(Segment.item_id == item.id)
            .order_by(col(PracticeSession.started_at).desc())
        ).all()
        last_focus = next((s.next_focus for s, _ in rows if s.next_focus), "")
        return to_dict(
            item,
            total_minutes=round(sum(s.minutes for s, _ in rows), 1),
            last_practised=to_dict(rows[0][1])["started_at"] if rows else None,
            next_focus=last_focus,
            history=[to_dict(s, started_at=to_dict(p)["started_at"]) for s, p in rows[:50]],
        )

    # ------------------------------------------------------------------- timer

    def running(self) -> PracticeSession | None:
        return self.db.exec(select(PracticeSession).where(PracticeSession.ended_at.is_(None))).first()

    def timer_status(self) -> dict:
        now = self.clock()
        s = self.running()
        if s is None:
            return {"running": False}
        return {
            "running": True,
            "session": to_dict(s),
            "elapsed_min": round(s.minutes(now), 1),
            "forgotten": s.is_forgotten(now, self.settings.forgotten_hours),
        }

    def timer_start(self, hobby: Ref = None, area: Ref = None, item: Ref = None, note: str = "",
                    source: str = "manual", if_idle: bool = False) -> dict:
        current = self.running()
        if current is not None:
            if if_idle:
                return {"started": False, **self.timer_status()}
            raise Conflict(f"a timer is already running (session {current.id})")
        h = self.resolve_hobby(hobby) if hobby else None
        a = self.resolve_area(area, h) if area else None
        i = self.resolve_item(item, h, a) if item else None
        if i and not a:
            a = self.get(Area, i.area_id)
        if a and not h:
            h = self.get(Hobby, a.hobby_id)
        preset = {k: v for k, v in (("area_id", a and a.id), ("item_id", i and i.id)) if v}
        session = PracticeSession(started_at=self.clock(), hobby_id=h and h.id, source=source,
                                  preset=preset, note=note or "")
        self._commit(session)
        return {"started": True, **self.timer_status()}

    def timer_split(self) -> dict:
        s = self._require_running()
        s.split(self.clock())
        self._commit(s)
        return self.timer_status()

    def timer_discard(self) -> dict:
        s = self._require_running()
        self.db.delete(s)
        self.db.commit()
        return {"discarded": s.id}

    def timer_stop(self, source: str | None = None, trim_s: float = 0) -> dict:
        """Stop the timer. With source given (automation), only a timer from that source is stopped."""
        s = self.running()
        if s is None:
            if source:
                return {"outcome": "not_running"}
            raise Conflict("no timer is running")
        if source and s.source != source:
            return {"outcome": "kept", "session": to_dict(s)}
        s.stop(self.clock(), trim_s)
        auto_stop = source is not None and source in self.settings.auto_sources
        if auto_stop and s.minutes() * 60 < self.settings.min_auto_session_s:
            self.db.delete(s)
            self.db.commit()
            return {"outcome": "discarded", "reason": "shorter than minimum auto session"}
        self._commit(s)
        if s.preset.get("area_id") and not s.splits:
            self.assign(s, [{"area": s.preset["area_id"], "item": s.preset.get("item_id"),
                             "minutes": round(s.minutes(), 1)}])
            return {"outcome": "assigned", "session": self.session_detail(s)}
        return {"outcome": "inbox", "session": self.session_detail(s)}

    def _require_running(self) -> PracticeSession:
        s = self.running()
        if s is None:
            raise Conflict("no timer is running")
        return s

    # ---------------------------------------------------------------- sessions

    def session_detail(self, s: PracticeSession) -> dict:
        segs = self.segments_of(s)
        assigned = sum(x.minutes for x in segs)
        return to_dict(
            s,
            minutes=round(s.minutes(self.clock()), 1),
            unassigned_min=round(max(0.0, s.minutes(self.clock()) - assigned), 1),
            segments=[to_dict(x) for x in segs],
        )

    def segments_of(self, s: PracticeSession) -> list[Segment]:
        return list(self.db.exec(select(Segment).where(Segment.session_id == s.id).order_by(Segment.id)).all())

    def list_sessions(self, start: datetime | None = None, end: datetime | None = None,
                      hobby: Hobby | None = None, limit: int = 100) -> list[PracticeSession]:
        q = select(PracticeSession)
        if start:
            q = q.where(PracticeSession.started_at >= start)
        if end:
            q = q.where(PracticeSession.started_at < end)
        if hobby:
            q = q.where(PracticeSession.hobby_id == hobby.id)
        return list(self.db.exec(q.order_by(col(PracticeSession.started_at).desc()).limit(limit)).all())

    def inbox(self) -> list[PracticeSession]:
        assigned = select(Segment.session_id)
        q = (select(PracticeSession)
             .where(PracticeSession.ended_at.is_not(None))
             .where(col(PracticeSession.id).not_in(assigned))
             .order_by(col(PracticeSession.started_at).desc()))
        return list(self.db.exec(q).all())

    def assign(self, s: PracticeSession, segments: list[dict], rating: int | None = None,
               note: str | None = None) -> PracticeSession:
        """Replace the session's segments. Each segment: area?, item?, minutes, focus, params, ..."""
        if s.running:
            raise Invalid("stop the timer before assigning")
        hobby = self.db.get(Hobby, s.hobby_id) if s.hobby_id else None
        built: list[Segment] = []
        for raw in segments:
            seg, hobby = self._build_segment(raw, hobby)
            built.append(seg)
        total = sum(x.minutes for x in built)
        if total > s.minutes() + 0.5:
            raise Invalid(f"segments add up to {total:.1f} min but the session lasts {s.minutes():.1f} min")
        if rating is not None:
            if rating not in range(1, 6):
                raise Invalid("session rating must be 1..5")
            s.rating = rating
        if note is not None:
            s.note = note
        s.hobby_id = hobby.id if hobby else s.hobby_id
        for old in self.segments_of(s):
            self.db.delete(old)
        for seg in built:
            seg.session_id = s.id
            self.db.add(seg)
            if seg.review_rating and seg.item_id:
                self._apply_review(self.get(Item, seg.item_id), seg.review_rating, s.ended_at)
        self._commit(s)
        return s

    def _build_segment(self, raw: dict, hobby: Hobby | None) -> tuple[Segment, Hobby]:
        item = self.resolve_item(raw["item"], hobby) if raw.get("item") else None
        if raw.get("area"):
            area = self.resolve_area(raw["area"], hobby)
        elif item:
            area = self.get(Area, item.area_id)
        else:
            raise Invalid("each segment needs an area or an item")
        if item and item.area_id != area.id:
            raise Invalid(f"item '{item.title}' is not in area '{area.name}'")
        if hobby and area.hobby_id != hobby.id:
            raise Invalid("all segments of a session must belong to the same hobby")
        hobby = hobby or self.get(Hobby, area.hobby_id)
        minutes = raw.get("minutes")
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            raise Invalid("segment minutes must be a positive number")
        kind = self.kind_for(item, area)
        rating = raw.get("review_rating")
        if rating is not None and rating not in (1, 2, 3, 4):
            raise Invalid("review_rating must be 1..4")
        seg = Segment(
            session_id=0, area_id=area.id, item_id=item.id if item else None, minutes=float(minutes),
            focus=kind.check_focus(raw.get("focus") or []) if kind else list(raw.get("focus") or []),
            params=kind.check_segment_params(raw.get("params") or {}) if kind else {},
            review_rating=rating, note=raw.get("note") or "", next_focus=raw.get("next_focus") or "",
        )
        return seg, hobby

    def _apply_review(self, item: Item, rating: int, when: datetime | None) -> None:
        area = self.get(Area, item.area_id)
        kind = self.kind_for(item, area)
        if item.status == "repertoire" and kind and kind.review_enabled:
            item.review(rating, when or self.clock())
            self.db.add(item)

    def log_session(self, segments: list[dict], hobby: Ref = None, started_at: str | datetime | None = None,
                    ended_at: str | datetime | None = None, minutes: float | None = None,
                    rating: int | None = None, note: str = "", source: str = "manual") -> PracticeSession:
        """Record a finished session in one call."""
        start, end = parse_dt(started_at), parse_dt(ended_at)
        total = minutes or sum(float(s.get("minutes") or 0) for s in segments)
        if start is None or end is None:
            if not total and (start is None or end is None):
                raise Invalid("give minutes, segment minutes, or both started_at and ended_at")
            if start is None:
                end = end or self.clock()
                start = end - timedelta(minutes=total)
            else:
                end = start + timedelta(minutes=total)
        if start >= end:
            raise Invalid("started_at must be before ended_at")
        h = self.resolve_hobby(hobby) if hobby else None
        s = PracticeSession(started_at=start, ended_at=end, hobby_id=h and h.id, source=source, note=note)
        self.db.add(s)
        self.db.flush()
        try:
            return self.assign(s, segments, rating=rating)
        except Exception:
            self.db.rollback()
            raise

    def update_session(self, s: PracticeSession, started_at=None, ended_at=None, rating=None,
                       note=None) -> PracticeSession:
        if started_at:
            s.started_at = parse_dt(started_at)
        if ended_at:
            if s.running:
                raise Invalid("use timer stop for a running session")
            s.ended_at = parse_dt(ended_at)
        if s.ended_at and s.ended_at < s.started_at:
            raise Invalid("ended_at must be after started_at")
        if rating is not None:
            s.rating = rating
        if note is not None:
            s.note = note
        return self._commit(s)

    def delete_session(self, s: PracticeSession) -> None:
        for seg in self.segments_of(s):
            self.db.delete(seg)
        self.db.delete(s)
        self.db.commit()

    # ------------------------------------------------- reviews, log, milestones

    def due_reviews(self, hobby: Hobby | None = None) -> list[Item]:
        now = self.clock()
        return [i for i in self.list_items(hobby)
                if i.status == "repertoire" and i.review_due and i.review_due <= now]

    def review_item(self, item: Item, rating: int) -> Item:
        area = self.get(Area, item.area_id)
        kind = self.kind_for(item, area)
        if item.status != "repertoire" or not (kind and kind.review_enabled):
            raise Invalid("only repertoire items of a kind with review enabled can be reviewed")
        item.review(rating, self.clock())
        return self._commit(item)

    def add_log(self, text: str, type: str = "insight", hobby: Ref = None, item: Ref = None,
                source: str = "manual") -> LogEntry:
        if type not in LOG_TYPES:
            raise Invalid(f"log type must be one of {LOG_TYPES}")
        if not text.strip():
            raise Invalid("log text is empty")
        h = self.resolve_hobby(hobby) if hobby else None
        i = self.resolve_item(item, h) if item else None
        if i and not h:
            h = self.get(Hobby, self.get(Area, i.area_id).hobby_id)
        return self._commit(LogEntry(text=text.strip(), type=type, hobby_id=h and h.id,
                                     item_id=i and i.id, source=source))

    def list_log(self, hobby: Hobby | None = None, open_tasks: bool = False, limit: int = 200) -> list[LogEntry]:
        q = select(LogEntry)
        if hobby:
            q = q.where(LogEntry.hobby_id == hobby.id)
        if open_tasks:
            q = q.where(LogEntry.type == "task", LogEntry.done_at.is_(None))
        return list(self.db.exec(q.order_by(col(LogEntry.created_at).desc()).limit(limit)).all())

    def set_log_done(self, entry: LogEntry, done: bool) -> LogEntry:
        entry.done_at = self.clock() if done else None
        return self._commit(entry)

    def add_milestone(self, title: str, hobby: Ref = None, item: Ref = None, note: str = "",
                      date: str | None = None) -> Milestone:
        h = self.resolve_hobby(hobby) if hobby else None
        i = self.resolve_item(item, h) if item else None
        if i and not h:
            h = self.get(Hobby, self.get(Area, i.area_id).hobby_id)
        if h is None:
            raise Invalid("milestone needs a hobby or an item")
        return self._commit(Milestone(title=title, note=note, hobby_id=h.id, item_id=i and i.id,
                                      date=parse_dt(date) or self.clock()))

    def add_attachment(self, owner_type: str, owner_id: int, filename: str, data: bytes,
                       mime: str, caption: str = "") -> Attachment:
        owners = {"item": Item, "segment": Segment, "milestone": Milestone, "log": LogEntry}
        if owner_type not in owners:
            raise Invalid(f"owner_type must be one of {sorted(owners)}")
        self.get(owners[owner_type], owner_id)
        now = self.clock()
        ext = "".join(c for c in (filename.rsplit(".", 1)[-1] if "." in filename else "bin") if c.isalnum())[:8]
        rel = f"{now:%Y/%m}/{owner_type}-{owner_id}-{now:%Y%m%d%H%M%S%f}.{ext or 'bin'}"
        path = self.settings.media_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._commit(Attachment(owner_type=owner_type, owner_id=owner_id, path=rel,
                                       mime=mime or "application/octet-stream", caption=caption))

    def list_attachments(self, owner_type: str, owner_id: int) -> list[Attachment]:
        return list(self.db.exec(select(Attachment).where(
            Attachment.owner_type == owner_type, Attachment.owner_id == owner_id)).all())

    # --------------------------------------------------------------- overview

    def overview(self, hobby: Hobby | None = None) -> dict:
        from .stats import Stats  # local import: stats depends on this module

        stats = Stats(self)
        hobbies = [hobby] if hobby else list(self.db.exec(select(Hobby).where(Hobby.archived_at.is_(None))))
        out = []
        for h in hobbies:
            areas = self.db.exec(select(Area).where(Area.hobby_id == h.id, Area.archived_at.is_(None))
                                 .order_by(Area.sort)).all()
            kinds = self.db.exec(select(Kind).where(Kind.hobby_id == h.id)).all()
            split = {r["area_id"]: r for r in stats.area_split(h, days=14)}
            items = [i for i in self.list_items(h) if i.status != "retired"]
            out.append(to_dict(
                h,
                areas=[to_dict(a, actual_share_14d=split.get(a.id, {}).get("actual_share", 0)) for a in areas],
                kinds=[to_dict(k) for k in kinds],
                items=[{"id": i.id, "title": i.title, "area_id": i.area_id, "kind_id": i.kind_id,
                        "status": i.status} for i in items],
                streak=stats.streak(h),
            ))
        return {
            "hobbies": out,
            "timer": self.timer_status(),
            "inbox_count": len(self.inbox()),
            "reviews_due": [{"id": i.id, "title": i.title, "due": to_dict(i)["review_due"]}
                            for i in self.due_reviews(hobby)],
            "open_tasks": [to_dict(e) for e in self.list_log(hobby, open_tasks=True, limit=20)],
        }

    # ----------------------------------------------------------------- helpers

    def _commit(self, obj: M) -> M:
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

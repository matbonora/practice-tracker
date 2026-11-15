"""REST API. Every route is: parse -> one Tracker call -> serialize."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from .models import (
    Area,
    Attachment,
    Hobby,
    Item,
    Kind,
    LogEntry,
    Milestone,
    PracticeSession,
    Segment,
)
from .serialize import parse_dt, to_dict
from .service import Tracker
from .stats import Stats
from .system import request_update, update_status

router = APIRouter(prefix="/api")


def tracker(request: Request):
    """One DB session per request; auth already checked by the middleware."""
    t = request.app.state.make_tracker()
    try:
        yield t
    finally:
        t.db.close()


T = Depends(tracker)


def _hobby(t: Tracker, ref) -> Hobby | None:
    return t.resolve_hobby(ref) if ref else None


# ------------------------------------------------------------------ bodies

class TimerStart(BaseModel):
    hobby: Optional[int | str] = None
    area: Optional[int | str] = None
    item: Optional[int | str] = None
    note: str = ""
    source: str = "manual"
    if_idle: bool = False


class TimerStop(BaseModel):
    source: Optional[str] = None
    trim_s: float = 0


class SegmentIn(BaseModel):
    area: Optional[int | str] = None
    item: Optional[int | str] = None
    minutes: float
    focus: list[str] = []
    params: dict[str, Any] = {}
    review_rating: Optional[int] = None
    note: str = ""
    next_focus: str = ""


class SessionIn(BaseModel):
    hobby: Optional[int | str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    minutes: Optional[float] = None
    rating: Optional[int] = None
    note: str = ""
    segments: list[SegmentIn] = []
    source: str = "manual"


class SessionPatch(BaseModel):
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    rating: Optional[int] = None
    note: Optional[str] = None


class AssignIn(BaseModel):
    segments: list[SegmentIn]
    rating: Optional[int] = None
    note: Optional[str] = None


class HobbyIn(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    weekly_goal_min: Optional[int] = None
    archived: Optional[bool] = None


class FieldDef(BaseModel):
    name: str
    type: Literal["text", "number", "choice", "multi"] = "text"
    options: Optional[list[str]] = None


class KindIn(BaseModel):
    hobby: Optional[int | str] = None
    name: Optional[str] = None
    item_fields: Optional[list[FieldDef]] = None
    segment_fields: Optional[list[FieldDef]] = None
    focus_options: Optional[list[str]] = None
    review_enabled: Optional[bool] = None


class AreaIn(BaseModel):
    hobby: Optional[int | str] = None
    name: Optional[str] = None
    target_share: Optional[float] = Field(default=None, ge=0, le=1)
    default_kind: Optional[int | str] = None
    is_warmup: Optional[bool] = None
    sort: Optional[int] = None
    archived: Optional[bool] = None


class ItemIn(BaseModel):
    area: Optional[int | str] = None
    hobby: Optional[int | str] = None
    title: Optional[str] = None
    kind: Optional[int | str] = None
    attributes: Optional[dict[str, Any]] = None
    status: Optional[str] = None
    archived: Optional[bool] = None


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=4)


class LogIn(BaseModel):
    text: str
    type: str = "insight"
    hobby: Optional[int | str] = None
    item: Optional[int | str] = None


class LogPatch(BaseModel):
    done: bool


class MilestoneIn(BaseModel):
    title: str
    hobby: Optional[int | str] = None
    item: Optional[int | str] = None
    note: str = ""
    date: Optional[str] = None


def _fields(model: BaseModel) -> dict:
    data = model.model_dump(exclude_unset=True)
    for key in ("item_fields", "segment_fields"):
        if data.get(key) is not None:
            data[key] = [{k: v for k, v in d.items() if v is not None} for d in data[key]]
    return data


def _segments(segs: list[SegmentIn]) -> list[dict]:
    return [s.model_dump() for s in segs]


# --------------------------------------------------------------- overview

@router.get("/overview")
def overview(hobby: Optional[str] = None, t: Tracker = T):
    return t.overview(_hobby(t, hobby))


@router.get("/summary")
def summary(t: Tracker = T):
    return Stats(t).summary()


# ------------------------------------------------------------------ timer

@router.get("/timer")
def timer_status(t: Tracker = T):
    return t.timer_status()


@router.post("/timer/start")
def timer_start(body: TimerStart = TimerStart(), t: Tracker = T):
    return t.timer_start(**body.model_dump())


@router.post("/timer/stop")
def timer_stop(body: TimerStop = TimerStop(), t: Tracker = T):
    return t.timer_stop(source=body.source, trim_s=body.trim_s)


@router.post("/timer/toggle")
def timer_toggle(body: TimerStart = TimerStart(), t: Tracker = T):
    if t.running():
        return t.timer_stop()
    return t.timer_start(**body.model_dump())


@router.post("/timer/split")
def timer_split(t: Tracker = T):
    return t.timer_split()


@router.delete("/timer")
def timer_discard(t: Tracker = T):
    return t.timer_discard()


# --------------------------------------------------------------- sessions

@router.get("/inbox")
def inbox(t: Tracker = T):
    return [t.session_detail(s) for s in t.inbox()]


@router.get("/sessions")
def list_sessions(start: Optional[str] = None, end: Optional[str] = None, hobby: Optional[str] = None,
                  limit: int = 100, t: Tracker = T):
    rows = t.list_sessions(parse_dt(start), parse_dt(end), _hobby(t, hobby), limit)
    return [t.session_detail(s) for s in rows]


@router.post("/sessions", status_code=201)
def log_session(body: SessionIn, t: Tracker = T):
    data = body.model_dump()
    data["segments"] = _segments(body.segments)
    return t.session_detail(t.log_session(**data))


@router.get("/sessions/{sid}")
def get_session(sid: int, t: Tracker = T):
    return t.session_detail(t.get(PracticeSession, sid))


@router.patch("/sessions/{sid}")
def patch_session(sid: int, body: SessionPatch, t: Tracker = T):
    return t.session_detail(t.update_session(t.get(PracticeSession, sid), **body.model_dump()))


@router.put("/sessions/{sid}/segments")
def assign(sid: int, body: AssignIn, t: Tracker = T):
    s = t.assign(t.get(PracticeSession, sid), _segments(body.segments), rating=body.rating, note=body.note)
    return t.session_detail(s)


@router.delete("/sessions/{sid}", status_code=204)
def delete_session(sid: int, t: Tracker = T):
    t.delete_session(t.get(PracticeSession, sid))


# -------------------------------------------------------------- structure

@router.get("/hobbies")
def list_hobbies(include_archived: bool = False, t: Tracker = T):
    q = select(Hobby) if include_archived else select(Hobby).where(Hobby.archived_at.is_(None))
    return [to_dict(h) for h in t.db.exec(q).all()]


@router.post("/hobbies", status_code=201)
def create_hobby(body: HobbyIn, t: Tracker = T):
    return to_dict(t.save_hobby(None, **_fields(body)))


@router.patch("/hobbies/{hid}")
def patch_hobby(hid: int, body: HobbyIn, t: Tracker = T):
    return to_dict(t.save_hobby(t.get(Hobby, hid), **_fields(body)))


@router.get("/kinds")
def list_kinds(hobby: Optional[str] = None, t: Tracker = T):
    q = select(Kind)
    if hobby:
        q = q.where(Kind.hobby_id == t.resolve_hobby(hobby).id)
    return [to_dict(k) for k in t.db.exec(q).all()]


@router.post("/kinds", status_code=201)
def create_kind(body: KindIn, t: Tracker = T):
    data = _fields(body)
    return to_dict(t.save_kind(t.resolve_hobby(data.pop("hobby", None)), None, **data))


@router.patch("/kinds/{kid}")
def patch_kind(kid: int, body: KindIn, t: Tracker = T):
    kind = t.get(Kind, kid)
    data = _fields(body)
    data.pop("hobby", None)
    return to_dict(t.save_kind(t.get(Hobby, kind.hobby_id), kind, **data))


@router.get("/areas")
def list_areas(hobby: Optional[str] = None, include_archived: bool = False, t: Tracker = T):
    q = select(Area).order_by(Area.hobby_id, Area.sort)
    if hobby:
        q = q.where(Area.hobby_id == t.resolve_hobby(hobby).id)
    if not include_archived:
        q = q.where(Area.archived_at.is_(None))
    return [to_dict(a) for a in t.db.exec(q).all()]


@router.post("/areas", status_code=201)
def create_area(body: AreaIn, t: Tracker = T):
    data = _fields(body)
    return to_dict(t.save_area(t.resolve_hobby(data.pop("hobby", None)), None, **data))


@router.patch("/areas/{aid}")
def patch_area(aid: int, body: AreaIn, t: Tracker = T):
    area = t.get(Area, aid)
    data = _fields(body)
    data.pop("hobby", None)
    return to_dict(t.save_area(t.get(Hobby, area.hobby_id), area, **data))


@router.get("/items")
def list_items(hobby: Optional[str] = None, include_archived: bool = False, t: Tracker = T):
    return [to_dict(i) for i in t.list_items(_hobby(t, hobby), include_archived)]


@router.post("/items", status_code=201)
def create_item(body: ItemIn, t: Tracker = T):
    data = _fields(body)
    h = _hobby(t, data.pop("hobby", None))
    area = t.resolve_area(data.pop("area", None), h)
    return to_dict(t.save_item(None, area=area, **data))


@router.get("/items/{iid}")
def get_item(iid: int, t: Tracker = T):
    item = t.get(Item, iid)
    return {**t.item_detail(item), "attachments": [to_dict(a) for a in t.list_attachments("item", iid)]}


@router.patch("/items/{iid}")
def patch_item(iid: int, body: ItemIn, t: Tracker = T):
    item = t.get(Item, iid)
    data = _fields(body)
    data.pop("hobby", None)
    area_ref = data.pop("area", None)
    area = t.resolve_area(area_ref, t.get(Hobby, t.get(Area, item.area_id).hobby_id)) if area_ref else None
    return to_dict(t.save_item(item, area=area, **data))


@router.post("/items/{iid}/review")
def review_item(iid: int, body: ReviewIn, t: Tracker = T):
    return to_dict(t.review_item(t.get(Item, iid), body.rating))


@router.get("/reviews/due")
def reviews_due(hobby: Optional[str] = None, t: Tracker = T):
    return [to_dict(i) for i in t.due_reviews(_hobby(t, hobby))]


# ------------------------------------------------------ log and milestones

@router.get("/log")
def list_log(hobby: Optional[str] = None, open_tasks: bool = False, limit: int = 200, t: Tracker = T):
    return [to_dict(e) for e in t.list_log(_hobby(t, hobby), open_tasks, limit)]


@router.post("/log", status_code=201)
def add_log(body: LogIn, t: Tracker = T):
    return to_dict(t.add_log(**body.model_dump()))


@router.patch("/log/{lid}")
def patch_log(lid: int, body: LogPatch, t: Tracker = T):
    return to_dict(t.set_log_done(t.get(LogEntry, lid), body.done))


@router.get("/milestones")
def list_milestones(hobby: Optional[str] = None, t: Tracker = T):
    q = select(Milestone).order_by(Milestone.date.desc())
    if hobby:
        q = q.where(Milestone.hobby_id == t.resolve_hobby(hobby).id)
    return [to_dict(m, attachments=[to_dict(a) for a in t.list_attachments("milestone", m.id)])
            for m in t.db.exec(q).all()]


@router.post("/milestones", status_code=201)
def add_milestone(body: MilestoneIn, t: Tracker = T):
    return to_dict(t.add_milestone(**body.model_dump()))


@router.post("/attachments", status_code=201)
async def upload(owner_type: str = Form(...), owner_id: int = Form(...), caption: str = Form(""),
                 file: UploadFile = File(...), t: Tracker = T):
    data = await file.read()
    return to_dict(t.add_attachment(owner_type, owner_id, file.filename or "upload", data,
                                    file.content_type or "", caption))


@router.get("/attachments")
def list_attachments(owner_type: str, owner_id: int, t: Tracker = T):
    return [to_dict(a) for a in t.list_attachments(owner_type, owner_id)]


@router.get("/attachments/{aid}/file")
def attachment_file(aid: int, t: Tracker = T):
    a = t.get(Attachment, aid)
    path = (t.settings.media_dir / a.path).resolve()
    if not path.is_relative_to(t.settings.media_dir.resolve()) or not path.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(path, media_type=a.mime)


# ------------------------------------------------------------------- stats

@router.get("/stats/minutes")
def stats_minutes(start: Optional[date] = None, end: Optional[date] = None, period: str = "week",
                  group: str = "hobby", hobby: Optional[str] = None, t: Tracker = T):
    st = Stats(t)
    end = end or st.today() + timedelta(days=1)
    start = start or end - timedelta(days=84)
    return st.minutes(start, end, period, group, _hobby(t, hobby))


@router.get("/stats/split")
def stats_split(hobby: str, days: int = 14, t: Tracker = T):
    return Stats(t).area_split(t.resolve_hobby(hobby), days)


@router.get("/stats/streak")
def stats_streak(hobby: str, t: Tracker = T):
    return Stats(t).streak(t.resolve_hobby(hobby))


# ------------------------------------------------------------------ system

class UpdateIn(BaseModel):
    force: bool = False


@router.get("/system/update")
def get_update_status(request: Request):
    return update_status(request.app.state.settings)


@router.post("/system/update", status_code=202)
def post_update(request: Request, body: UpdateIn = UpdateIn()):
    return request_update(request.app.state.settings, body.force)


# ------------------------------------------------------------------ export

@router.get("/export.json")
def export_json(t: Tracker = T):
    tables = {"hobbies": Hobby, "kinds": Kind, "areas": Area, "items": Item, "sessions": PracticeSession,
              "segments": Segment, "milestones": Milestone, "log": LogEntry, "attachments": Attachment}
    return {name: [to_dict(o) for o in t.db.exec(select(model)).all()] for name, model in tables.items()}


@router.get("/export.csv", response_class=PlainTextResponse)
def export_csv(t: Tracker = T):
    """One row per segment (plus one row per unassigned remainder)."""
    names = {m: {o.id: getattr(o, "title", None) or o.name for o in t.db.exec(select(m)).all()}
             for m in (Hobby, Area, Item)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session_id", "started_at", "ended_at", "hobby", "area", "item", "minutes", "focus",
                "params", "review_rating", "note", "session_rating", "source"])
    for s in t.list_sessions(limit=100000):
        d = t.session_detail(s)
        rows = d["segments"] + ([{"minutes": d["unassigned_min"]}] if d["unassigned_min"] > 0 else [])
        for seg in rows:
            w.writerow([s.id, d["started_at"], d["ended_at"], names[Hobby].get(s.hobby_id, ""),
                        names[Area].get(seg.get("area_id"), "unassigned"), names[Item].get(seg.get("item_id"), ""),
                        seg["minutes"], ";".join(seg.get("focus", [])), seg.get("params", ""),
                        seg.get("review_rating") or "", seg.get("note", ""), s.rating or "", s.source])
    return buf.getvalue()

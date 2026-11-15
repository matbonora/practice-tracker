"""Statistics: one pass over sessions and segments, grouped in Python.

Single user, small data: no SQL tricks needed. A session counts on the local date it started.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlmodel import select

from .errors import Invalid
from .models import Area, Hobby, Item, PracticeSession, Segment
from .service import Tracker

PERIODS = ("day", "week", "month")
GROUPS = ("hobby", "area", "item")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Stats:
    def __init__(self, tracker: Tracker):
        self.t = tracker
        self.tz = tracker.settings.timezone

    # ---------------------------------------------------------------- basics

    def local_date(self, dt_utc: datetime) -> date:
        return dt_utc.astimezone(self.tz).date()

    def local_midnight_utc(self, d: date) -> datetime:
        return datetime.combine(d, time.min, self.tz).astimezone(timezone.utc)

    def today(self) -> date:
        return self.local_date(self.t.clock())

    def _rows(self, start: date, end: date, hobby: Hobby | None = None):
        """Yield (local_date, session, minutes, segments) for sessions starting in [start, end)."""
        now = self.t.clock()
        q = select(PracticeSession).where(
            PracticeSession.started_at >= self.local_midnight_utc(start),
            PracticeSession.started_at < self.local_midnight_utc(end),
        )
        if hobby:
            q = q.where(PracticeSession.hobby_id == hobby.id)
        sessions = list(self.t.db.exec(q).all())
        ids = [s.id for s in sessions]
        segs: dict[int, list[Segment]] = defaultdict(list)
        if ids:
            for seg in self.t.db.exec(select(Segment).where(Segment.session_id.in_(ids))).all():
                segs[seg.session_id].append(seg)
        for s in sessions:
            yield self.local_date(s.started_at), s, s.minutes(now), segs[s.id]

    # --------------------------------------------------------------- minutes

    def minutes(self, start: date, end: date, period: str = "week", group: str = "hobby",
                hobby: Hobby | None = None) -> list[dict]:
        if period not in PERIODS or group not in GROUPS:
            raise Invalid(f"period must be one of {PERIODS}, group one of {GROUPS}")
        names = self._names(group)
        totals: dict[tuple[str, object], float] = defaultdict(float)
        for d, s, mins, segs in self._rows(start, end, hobby):
            key = _period_key(d, period)
            if group == "hobby":
                totals[(key, s.hobby_id)] += mins
                continue
            assigned = 0.0
            for seg in segs:
                totals[(key, seg.area_id if group == "area" else seg.item_id)] += seg.minutes
                assigned += seg.minutes
            if mins - assigned > 0.05:
                totals[(key, None)] += mins - assigned
        return [
            {"period": k, "id": gid, "name": names.get(gid, "unassigned"), "minutes": round(m, 1)}
            for (k, gid), m in sorted(totals.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))
        ]

    def _names(self, group: str) -> dict:
        model, attr = {"hobby": (Hobby, "name"), "area": (Area, "name"), "item": (Item, "title")}[group]
        return {o.id: getattr(o, attr) for o in self.t.db.exec(select(model)).all()}

    def total_minutes(self, start: date, end: date, hobby: Hobby | None = None) -> float:
        return round(sum(m for _, _, m, _ in self._rows(start, end, hobby)), 1)

    # ---------------------------------------------------------------- splits

    def area_split(self, hobby: Hobby, days: int = 14) -> list[dict]:
        end = self.today() + timedelta(days=1)
        per_area: dict[int, float] = defaultdict(float)
        for _, _, _, segs in self._rows(end - timedelta(days=days), end, hobby):
            for seg in segs:
                per_area[seg.area_id] += seg.minutes
        total = sum(per_area.values())
        areas = self.t.db.exec(select(Area).where(Area.hobby_id == hobby.id, Area.archived_at.is_(None))
                               .order_by(Area.sort)).all()
        return [{
            "area_id": a.id, "name": a.name, "minutes": round(per_area[a.id], 1),
            "target_share": a.target_share,
            "actual_share": round(per_area[a.id] / total, 3) if total else 0.0,
        } for a in areas]

    # --------------------------------------------------------------- streaks

    def streak(self, hobby: Hobby) -> dict:
        """Weeks (Mon-Sun, local) reaching the weekly goal. A missed day never breaks anything."""
        today = self.today()
        this_week = week_start(today)
        first = self.t.db.exec(select(PracticeSession.started_at).where(PracticeSession.hobby_id == hobby.id)
                               .order_by(PracticeSession.started_at)).first()
        weekly: dict[date, float] = defaultdict(float)
        days_this_week: set[date] = set()
        if first is not None:
            for d, _, mins, _ in self._rows(week_start(self.local_date(first)), today + timedelta(days=1), hobby):
                weekly[week_start(d)] += mins
                if week_start(d) == this_week and mins > 0:
                    days_this_week.add(d)
        goal = hobby.weekly_goal_min
        met = {w for w, m in weekly.items() if goal and m >= goal}

        current, w = 0, this_week if this_week in met else this_week - timedelta(weeks=1)
        while w in met:
            current += 1
            w -= timedelta(weeks=1)
        longest = run = 0
        if weekly:
            w = min(weekly)
            while w <= this_week:
                run = run + 1 if w in met else 0
                longest = max(longest, run)
                w += timedelta(weeks=1)
        return {
            "weekly_goal_min": goal,
            "this_week_min": round(weekly.get(this_week, 0.0), 1),
            "days_this_week": len(days_this_week),
            "current_weeks": current,
            "longest_weeks": longest,
            "this_week_met": this_week in met,
        }

    # --------------------------------------------------------------- summary

    def summary(self) -> dict:
        """Small object for dashboards and Home Assistant."""
        today = self.today()
        tomorrow = today + timedelta(days=1)
        hobbies = list(self.t.db.exec(select(Hobby).where(Hobby.archived_at.is_(None))).all())
        timer = self.t.timer_status()
        return {
            "running": timer["running"],
            "elapsed_min": timer.get("elapsed_min", 0),
            "running_hobby_id": timer["session"]["hobby_id"] if timer["running"] else None,
            "today_min": self.total_minutes(today, tomorrow),
            "week_min": self.total_minutes(week_start(today), tomorrow),
            "inbox": len(self.t.inbox()),
            "reviews_due": len(self.t.due_reviews()),
            "hobbies": [{"id": h.id, "name": h.name, **self.streak(h)} for h in hobbies],
        }


def _period_key(d: date, period: str) -> str:
    if period == "day":
        return d.isoformat()
    if period == "week":
        return week_start(d).isoformat()
    return d.strftime("%Y-%m")

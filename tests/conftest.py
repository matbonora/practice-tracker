from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from practice_tracker.config import Settings
from practice_tracker.db import make_engine, new_session
from practice_tracker.presets import load_presets_if_empty
from practice_tracker.service import Tracker

PRESETS = Path(__file__).resolve().parents[1] / "presets"


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, presets_dir=PRESETS,
                    tokens={"test-token": "web", "agent-token": "agent"},
                    timezone=ZoneInfo("Europe/Zurich"))


@pytest.fixture
def clock():
    return Clock(datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc))  # Monday 18:00 Zurich


@pytest.fixture
def tracker(settings, clock):
    engine = make_engine(settings.db_url)
    db = new_session(engine)
    load_presets_if_empty(db, settings.presets_dir)
    yield Tracker(db, settings, clock)
    db.close()

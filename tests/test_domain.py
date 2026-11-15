import pytest

from practice_tracker.errors import Ambiguous, Conflict, Invalid
from practice_tracker.stats import Stats


def test_presets_loaded(tracker):
    piano = tracker.resolve_hobby("piano")
    assert piano.name == "Piano"
    assert len([i for i in tracker.list_items(piano)]) == 24
    assert tracker.resolve_area("scales", piano).is_warmup


def test_resolve_ambiguous_returns_candidates(tracker):
    with pytest.raises(Ambiguous) as e:
        tracker.resolve_item("major")
    assert len(e.value.candidates) >= 12


def test_timer_start_stop_goes_to_inbox(tracker, clock):
    tracker.timer_start(hobby="Piano")
    with pytest.raises(Conflict):
        tracker.timer_start()
    clock.advance(minutes=25)
    out = tracker.timer_stop()
    assert out["outcome"] == "inbox"
    assert out["session"]["minutes"] == 25
    assert len(tracker.inbox()) == 1


def test_timer_with_item_auto_assigns(tracker, clock):
    tracker.timer_start(item="D minor")
    clock.advance(minutes=10)
    out = tracker.timer_stop()
    assert out["outcome"] == "assigned"
    assert out["session"]["segments"][0]["minutes"] == 10
    assert tracker.inbox() == []


def test_auto_source_rules(tracker, clock):
    # manual timer is never touched by the MIDI automation
    tracker.timer_start(hobby="Piano")
    assert tracker.timer_start(source="midi", if_idle=True)["started"] is False
    assert tracker.timer_stop(source="midi")["outcome"] == "kept"
    tracker.timer_discard()
    # short MIDI session is discarded, long one kept
    tracker.timer_start(hobby="Piano", source="midi", if_idle=True)
    clock.advance(seconds=60)
    assert tracker.timer_stop(source="midi")["outcome"] == "discarded"
    tracker.timer_start(hobby="Piano", source="midi", if_idle=True)
    clock.advance(minutes=30)
    assert tracker.timer_stop(source="midi")["outcome"] == "inbox"
    assert tracker.timer_stop(source="midi")["outcome"] == "not_running"


def test_assign_validates(tracker, clock):
    tracker.timer_start(hobby="Piano")
    clock.advance(minutes=20)
    sid = tracker.timer_stop()["session"]["id"]
    from practice_tracker.models import PracticeSession
    s = tracker.get(PracticeSession, sid)
    with pytest.raises(Invalid):  # too many minutes
        tracker.assign(s, [{"area": "Pieces", "minutes": 30}])
    with pytest.raises(Invalid):  # unknown focus
        tracker.assign(s, [{"item": "C major", "minutes": 5, "focus": ["juggling"]}])
    with pytest.raises(Invalid):  # bad param type
        tracker.assign(s, [{"item": "C major", "minutes": 5, "params": {"bpm": "fast"}}])
    with pytest.raises(Ambiguous):  # area of another hobby is not found in this one
        tracker.assign(s, [{"area": "Studies", "minutes": 5}])
    tracker.assign(s, [
        {"item": "D minor", "minutes": 5, "focus": ["arpeggios"], "params": {"bpm": 80, "hands": "both"}},
        {"area": "Sight reading", "minutes": 10},
    ], rating=4)
    detail = tracker.session_detail(s)
    assert detail["unassigned_min"] == 5
    assert detail["rating"] == 4


def test_review_schedule(tracker, clock):
    piano = tracker.resolve_hobby("Piano")
    piece = tracker.save_item(area=tracker.resolve_area("Pieces", piano), title="Nocturne op. 9/2",
                              attributes={"composer": "Chopin"})
    assert piece.status == "learning" and piece.review_due is None
    tracker.save_item(piece, status="repertoire")
    assert piece.review_interval_days == 2
    clock.advance(days=3)
    assert [i.id for i in tracker.due_reviews()] == [piece.id]
    tracker.log_session([{"item": piece.id, "minutes": 4, "review_rating": 3}], hobby="Piano")
    assert piece.review_interval_days == 4
    tracker.review_item(piece, 1)
    assert piece.review_interval_days == 1 and piece.review_lapses == 1
    scale = tracker.resolve_item("C major")
    with pytest.raises(Invalid):
        tracker.review_item(scale, 3)


def test_stats_and_streak(tracker, clock):
    piano = tracker.resolve_hobby("Piano")  # goal 150 min/week
    # three weeks back: 160, 0 (gap), then 2 weeks of 160, plus this week 50
    for weeks_ago, minutes in [(4, 160), (2, 160), (1, 160), (0, 50)]:
        clock_back = clock.now
        clock.advance(weeks=-weeks_ago)
        tracker.log_session([{"area": "Pieces", "minutes": minutes}], hobby="Piano")
        clock.now = clock_back
    st = Stats(tracker)
    streak = st.streak(piano)
    assert streak["current_weeks"] == 2  # this week not met yet, does not break
    assert streak["longest_weeks"] == 2
    assert streak["this_week_min"] == 50
    rows = st.minutes(st.today().replace(day=1), st.today().replace(day=28), period="week", group="area")
    assert any(r["name"] == "Pieces" for r in rows)
    split = {r["name"]: r for r in st.area_split(piano)}
    assert split["Pieces"]["actual_share"] == 1.0
    assert st.summary()["week_min"] == 50


def test_log_and_milestone(tracker):
    e = tracker.add_log("Bar 12 LH jumps: practise in rhythms", type="task", item="C major")
    assert e.hobby_id == tracker.resolve_hobby("Piano").id
    assert len(tracker.list_log(open_tasks=True)) == 1
    tracker.set_log_done(e, True)
    assert tracker.list_log(open_tasks=True) == []
    m = tracker.add_milestone("First full run", item="C major")
    assert m.hobby_id == e.hobby_id
    with pytest.raises(Invalid):
        tracker.add_log("x", type="rant")


def test_log_session_time_window(tracker, clock):
    s = tracker.log_session([{"area": "Pieces", "minutes": 20}], hobby="Piano", started_at="2026-11-10T18:00:00+01:00")
    assert s.minutes() == 20
    s = tracker.log_session([], hobby="Piano", minutes=15)
    assert s.ended_at == clock.now and s.minutes() == 15
    with pytest.raises(Invalid):
        tracker.log_session([], hobby="Piano")

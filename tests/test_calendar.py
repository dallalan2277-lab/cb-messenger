"""바탕화면 캘린더 데이터·위치 계산 테스트 (Windows 없이)"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors import message_from_text
from app.config import Settings
from app.desktop_calendar.api import CalendarAPI, load_holidays
from app.desktop_calendar.widget import clamp_to_areas, initial_geometry
from app.llm.base import LLMProvider
from app.pipeline import build_pipeline
from app.storage import Storage


def _api(tmp_path):
    return CalendarAPI(Storage(str(tmp_path / "t.db")), tmp_path / "prefs.json")


def test_month_grid_has_6_weeks_and_holidays(tmp_path):
    api = _api(tmp_path)
    m = api.month(2026, 9)
    assert len(m["weeks"]) == 6 and all(len(w) == 7 for w in m["weeks"])
    days = {d["date"]: d for w in m["weeks"] for d in w}
    assert days["2026-09-25"]["holiday"] == "추석"
    assert m["weeks"][0][0]["weekday"] == 6            # 기본: 일요일 시작
    api.set_prefs({"week_start": 1})
    assert api.month(2026, 9)["weeks"][0][0]["weekday"] == 0


def test_holidays_file():
    h = load_holidays()
    assert h["2026-06-03"] == "전국동시지방선거" and h["2027-02-09"].startswith("대체공휴일")


def test_add_update_delete_and_validation(tmp_path):
    api = _api(tmp_path)
    r = api.add({"title": "학부모 상담", "date": "2026-11-10", "start_time": "14:00", "location": "교실"})
    eid = r["id"]
    ev = api.month(2026, 11)["weeks"]
    found = [e for w in ev for d in w for e in d["events"]]
    assert found[0]["title"] == "학부모 상담" and found[0]["status"] == "confirmed"
    api.update(eid, {"start_time": "15:00"})
    assert [e for w in api.month(2026, 11)["weeks"] for d in w for e in d["events"]][0]["start_time"] == "15:00"
    with pytest.raises(ValueError):
        api.add({"title": "", "date": "2026-11-10"})
    with pytest.raises(ValueError):
        api.add({"title": "x", "date": "2026-13-40"})
    with pytest.raises(ValueError):
        api.add({"title": "x", "date": "2026-11-10", "start_time": "25:00"})
    assert api.delete(eid)["ok"]


class EventAI(LLMProvider):
    name = "stub"
    def complete(self, system, user, max_tokens=1500):
        d = (date.today() + timedelta(days=3)).isoformat()
        return ('{"importance":"중요","category":"회의","summary":"성적위원회 참석",'
                '"events":[{"title":"학업성적관리위원회","date":"%s","start_time":"15:30"}]}' % d)


def test_ai_events_become_suggestions_then_confirm(tmp_path, monkeypatch):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.analyze_model = s.digest_model = "fake"
    s.fallback_model, s.skip_ai_keywords, s.skip_ai_min_chars = "", [], 0
    s.include_senders, s.exclude_senders, s.redact_names = [], [], []
    s.archive_local_enabled = s.archive_gdrive_enabled = False
    s.calendar_auto_suggest, s.calendar_auto_confirm = True, False
    pipe = build_pipeline(s, notify_levels=())
    pipe.analyzer.provider = EventAI("m")
    pipe.handle(message_from_text("성적위원회 회의 참석 바랍니다", sender="교무부장"))
    pipe.handle(message_from_text("성적위원회 회의 참석 바랍니다 (재공지)", sender="교무부장"))   # 같은 일정 → 중복 제안 안 함
    api = CalendarAPI(pipe.storage, tmp_path / "p.json")
    sug = api.suggestions()
    assert len(sug) == 1 and sug[0]["sender"] == "교무부장" and sug[0]["status"] == "suggested"
    msg = api.message(sug[0]["message_id"])
    assert "성적위원회" in msg["body"]
    api.confirm(sug[0]["id"])
    assert api.suggestions() == [] and api.upcoming(14)[0]["status"] == "confirmed"
    api.set_prefs({"show_suggested": False})
    assert api.month(date.today().year, date.today().month)["suggested_count"] == 0


def test_dismissed_hidden(tmp_path):
    st = Storage(str(tmp_path / "t.db"))
    eid = st.add_calendar_event(title="x", date="2026-11-12", status="suggested", origin="ai")
    api = CalendarAPI(st, tmp_path / "p.json")
    api.dismiss(eid)
    assert api.month(2026, 11)["suggested_count"] == 0
    assert all(not d["events"] for w in api.month(2026, 11)["weeks"] for d in w)


def test_initial_geometry_two_thirds_right():
    x, y, w, h = initial_geometry((1920, 0, 1920, 1040), 0.66, "right")
    assert x + w == 1920 * 2 - 12 and abs(w - (1267 - 24)) <= 1 and h == 1040 - 24
    x2, _, w2, _ = initial_geometry((0, 0, 1920, 1040), 0.66, "left")
    assert x2 == 12


def test_clamp_drops_offscreen_geometry():
    areas = [(0, 0, 1920, 1040)]
    assert clamp_to_areas((2500, 100, 1200, 800), areas) is None         # 두 번째 모니터를 뺀 경우
    assert clamp_to_areas((600, 100, 1200, 800), areas) == (600, 100, 1200, 800)


class _FakeRunner:
    started = 0

    def __init__(self, settings, on_change=None):
        self.on_change = on_change
        self.stopped = False

    def start(self):
        _FakeRunner.started += 1
        return self

    def stop(self):
        self.stopped = True


def test_restart_watch_without_watch_mode(tmp_path, monkeypatch):
    import app.runner
    monkeypatch.setattr(app.runner, "Runner", _FakeRunner)
    api = _api(tmp_path)
    assert api.restart_watch() == {"ok": False}


def test_restart_watch_recovers_after_failed_start(tmp_path, monkeypatch):
    """처음 감시 시작이 실패(_runner 없음)해도 [감시 다시 시작]으로 켜져야 함 (예: 설치 직후 로그인 전)"""
    import app.runner
    monkeypatch.setattr(app.runner, "Runner", _FakeRunner)
    api = _api(tmp_path)
    marker = object()
    api._watch_on_change = marker
    assert api.restart_watch() == {"ok": True}
    first = api._runner
    assert isinstance(first, _FakeRunner) and first.on_change is marker
    assert api.restart_watch() == {"ok": True}
    assert first.stopped and api._runner is not first and api._runner.on_change is marker


def test_restart_watch_reports_start_error(tmp_path, monkeypatch):
    import app.runner

    class Boom(_FakeRunner):
        def start(self):
            raise RuntimeError("로그인 필요")

    monkeypatch.setattr(app.runner, "Runner", Boom)
    api = _api(tmp_path)
    api._watch_on_change = lambda: None
    r = api.restart_watch()
    assert r["ok"] is False and "로그인" in r["error"] and api._runner is None

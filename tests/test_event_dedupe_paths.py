import json
import sqlite3

import pytest

from app.analyzer import Analyzer
from app.config import Settings
from app.desktop_calendar import api as calendar_api
from app.desktop_calendar.api import CalendarAPI
from app.llm.base import LLMProvider
from app.models import Message
from app.pipeline import Pipeline
from app.storage import Storage


DAY = "2099-11-20"


class StubProvider(LLMProvider):
    name = "stub"

    def __init__(self):
        super().__init__("stub-model")
        self.event = {"title": "가상 연수 안내", "date": DAY, "start_time": "15:00"}

    def complete(self, system, user, max_tokens=1500):
        return json.dumps(self.complete_json(system, user), ensure_ascii=False)

    def complete_json(self, system, user, max_tokens=1500, schema=None):
        return {"title": "가상 메모", "kind": "안내문", "summary": "가상 연수 안내",
                "importance": "참고", "details": [], "items": [], "action_items": [],
                "events": [dict(self.event)]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings()
    settings.calendar_auto_suggest = True
    settings.calendar_auto_confirm = False
    settings.memo_enabled = True
    settings.analyze_model = "stub"
    settings.fallback_model = ""
    settings.skip_ai_min_chars = 0
    settings.skip_ai_keywords = []
    settings.include_senders = []
    settings.exclude_senders = []
    provider = StubProvider()
    storage = Storage(str(tmp_path / "test.db"))
    api = CalendarAPI(storage, tmp_path / "prefs.json")
    monkeypatch.setattr(calendar_api, "get_settings", lambda reload=False: settings)
    monkeypatch.setattr(calendar_api, "create_provider", lambda current, spec: provider)
    return settings, provider, storage, api


def rows(storage):
    return storage.calendar_events(DAY, DAY, include_dismissed=True)


def message(external_id, body):
    return Message(sender="가상 발신자", body=body, source="eztalk", external_id=external_id)


def test_pipeline_links_similar_messages_and_reanalysis(setup):
    settings, provider, storage, api = setup
    pipe = Pipeline(settings, storage, Analyzer(provider), notify_levels=())
    first = message("one", "가상 연수 안내입니다.")
    second = message("two", "가상 연수 개최 소식입니다.")
    assert pipe.handle(first)
    provider.event["title"] = "가상 연수 개최"
    assert pipe.handle(second)
    assert len(rows(storage)) == 1
    event_id = rows(storage)[0]["id"]
    assert {s["ref_id"] for s in api.event_sources(event_id)} == {first.id, second.id}
    pipe.reanalyze(second.id)
    pipe.reanalyze(second.id)
    assert len(rows(storage)) == 1
    assert pipe._suggest_events(second, Analyzer(provider).analyze(second)) == 0


def test_changed_time_requires_confirmation_even_with_auto_confirm(setup):
    settings, provider, storage, _ = setup
    settings.calendar_auto_confirm = True
    pipe = Pipeline(settings, storage, Analyzer(provider), notify_levels=())
    assert pipe.handle(message("one", "가상 연수는 오후 세 시입니다."))
    provider.event["start_time"] = "16:00"
    assert pipe.handle(message("two", "가상 연수는 오후 네 시로 바뀝니다."))
    first, second = rows(storage)
    assert first["status"] == "confirmed"
    assert second["status"] == "suggested" and second["dup_of"] == first["id"]


def test_memo_create_and_reanalyze_reuse_event(setup):
    _, provider, storage, api = setup
    first = api.memo_create({"mode": "text", "text": "가상 연수 안내", "kind": ""})
    provider.event["title"] = "가상 연수 개최"
    second = api.memo_create({"mode": "text", "text": "가상 연수 개최 소식", "kind": ""})
    assert first["ok"] and second["ok"]
    assert api.memo_reanalyze(second["memo"]["id"])["ok"]
    assert len(rows(storage)) == 1
    assert {s["ref_id"] for s in api.event_sources(rows(storage)[0]["id"])} == {
        first["memo"]["id"], second["memo"]["id"]}


def test_memo_to_event_merges_same_and_links_source(setup):
    _, _, storage, api = setup
    memo_id = storage.add_memo(title="가상 연수", summary="가상 메모 내용")
    event_id = storage.add_calendar_event(title="가상 연수 안내", date=DAY, start_time="15:00",
                                          status="dismissed", origin="ai")
    result = api.memo_to_event(memo_id, {"title": "가상 연수 개최", "date": DAY,
                                         "start_time": "15:00", "end_time": "16:00"})
    assert result["existed"] and result["id"] == event_id
    event = rows(storage)[0]
    assert (event["status"], event["end_time"], event["note"], event["memo_id"]) == (
        "confirmed", "16:00", "가상 메모 내용", memo_id)
    assert [(s["kind"], s["ref_id"], s["relation"]) for s in api.event_sources(event_id)] == [
        ("memo", memo_id, "same")]


def test_memo_to_event_creates_and_marks_related_suggestions(setup):
    _, _, storage, api = setup
    memo_id = storage.add_memo(title="가상 연수")
    proposal = storage.add_calendar_event(title="가상 연수", date=DAY, start_time="15:00",
                                          status="suggested", origin="ai")
    result = api.memo_to_event(memo_id, {"title": "가상 연수", "date": DAY, "start_time": "16:00"})
    assert result["existed"] is False and result["related"] == [proposal]
    events = {e["id"]: e for e in rows(storage)}
    assert events[result["id"]]["status"] == "confirmed"
    assert events[proposal]["dup_of"] == result["id"]
    assert api.event_sources(result["id"])[0]["relation"] == "origin"


def test_memo_to_event_multiple_same_candidates_create_new_event(setup):
    _, _, storage, api = setup
    memo_id = storage.add_memo(title="가상 연수")
    first = storage.add_calendar_event(title="가상 연수", date=DAY, status="suggested", origin="ai")
    second = storage.add_calendar_event(title="가상 연수 안내", date=DAY, status="suggested", origin="manual")
    result = api.memo_to_event(memo_id, {"title": "가상 연수", "date": DAY})
    assert not result["existed"] and result["related"] == [first]
    events = {e["id"]: e for e in rows(storage)}
    assert events[first]["dup_of"] == result["id"] and events[second]["dup_of"] is None


def test_user_event_transaction_rolls_back_if_link_fails(setup):
    _, _, storage, api = setup
    memo_id = storage.add_memo(title="가상 연수")
    storage.conn.execute("CREATE TRIGGER fail_memo_link BEFORE INSERT ON event_sources "
                         "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    storage.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        api.memo_to_event(memo_id, {"date": DAY})
    assert rows(storage) == []


def test_confirm_all_skips_related_and_confirm_separates(setup):
    _, _, storage, api = setup
    original = storage.suggest_event(title="가상 연수", date=DAY, start_time="15:00")
    duplicate = storage.suggest_event(title="가상 연수", date=DAY, start_time="16:00")
    assert api.suggestions()[1]["dup_of"] == original["event_id"]
    assert api.confirm_all() == {"ok": True, "count": 1, "skipped": 1}
    assert api.confirm(duplicate["event_id"])["ok"]
    event = next(e for e in rows(storage) if e["id"] == duplicate["event_id"])
    assert event["status"] == "confirmed" and event["dup_of"] is None


def test_confirm_all_does_not_skip_missing_original(setup):
    _, _, storage, api = setup
    proposal = storage.add_calendar_event(title="가상 연수", date=DAY, status="suggested", origin="ai")
    storage.conn.execute("PRAGMA foreign_keys=OFF")
    storage.conn.execute("UPDATE calendar_events SET dup_of=999999 WHERE id=?", (proposal,))
    storage.conn.commit()
    storage.conn.execute("PRAGMA foreign_keys=ON")
    assert api.confirm_all() == {"ok": True, "count": 1, "skipped": 0}
    assert rows(storage)[0]["status"] == "confirmed"


@pytest.mark.parametrize("action", ["replace", "separate", "ignore"])
def test_resolve_duplicate_api(setup, action):
    _, _, storage, api = setup
    original = storage.suggest_event(title="가상 연수", date=DAY, start_time="15:00")["event_id"]
    proposal = storage.suggest_event(title="가상 연수", date=DAY, start_time="16:00")["event_id"]
    result = api.resolve_duplicate(proposal, action)
    assert result == {"ok": True, "event_id": original if action == "replace" else proposal}
    events = {e["id"]: e for e in rows(storage)}
    if action == "replace":
        assert proposal not in events and events[original]["start_time"] == "16:00"
    elif action == "separate":
        assert events[proposal]["dup_of"] is None and events[proposal]["status"] == "confirmed"
    else:
        assert events[proposal]["status"] == "dismissed" and events[proposal]["dup_of"] == original


def test_suggestions_carry_original_for_comparison(tmp_path):
    from datetime import date, timedelta
    from app.desktop_calendar.api import CalendarAPI
    from app.storage import Storage
    st = Storage(str(tmp_path / "s.db"))
    api = CalendarAPI(st, tmp_path / "prefs.json")
    day = (date.today() + timedelta(days=3)).isoformat()
    old = st.suggest_event(title="가상 협의회", date=day, start_time="14:00", status="confirmed")["event_id"]
    asked = st.suggest_event(title="가상 협의회", date=day, start_time="15:00")
    assert asked["action"] == "asked"
    rows = {r["id"]: r for r in api.suggestions()}
    assert rows[asked["event_id"]]["dup_of"] == old
    assert rows[asked["event_id"]]["dup"]["start_time"] == "14:00"

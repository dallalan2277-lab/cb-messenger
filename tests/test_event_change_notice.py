import pytest

from datetime import datetime
from types import SimpleNamespace

from app.event_match import dates_in_text
from app.storage import Storage
from app.desktop_calendar.api import CalendarAPI
from app.models import Analysis, Event, Message
from app.config import Settings
from app.pipeline import Pipeline
from app.desktop_calendar import api as calendar_api


@pytest.fixture
def db():
    storage = Storage(":memory:")
    yield storage
    storage.close()


def message(db, number=1):
    m = Message(sender="가상 교사", body="가상 안내", external_id=str(number), received_at="2026-11-01")
    db.add_message(m)
    return m.id


def row(db, event_id):
    result = db.conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
    return dict(result) if result else None


@pytest.mark.parametrize("text", ["2026-11-19", "2026.11.19", "11월 19일", "11/19", "11.19.", "19일"])
def test_date_formats(text):
    assert dates_in_text(text, "2026-11-01") == {"2026-11-19"}


def test_date_boundaries_and_false_positives():
    assert dates_in_text("14:30 연락처 010-1234-5678 1-2반", "2026-11-01") == set()
    assert dates_in_text("1월 3일", "2026-12-25") == {"2027-01-03"}
    assert dates_in_text("12월 31일", "2027-01-02") == {"2026-12-31"}
    assert dates_in_text("2026.11.19. 2월 30일", "2026-11-01") == {"2026-11-19"}


def change(db, text, **kwargs):
    return db.suggest_event(title="가상 협의회", date="2026-11-20", source_text=text,
                            source_date="2026-11-01", **kwargs)


def test_changed_date_asks_then_replaces(db):
    old = db.add_calendar_event(title="가상 협의회", date="2026-11-19", status="confirmed")
    msg = message(db)
    result = change(db, "가상 협의회를 19일에서 20일로 연기합니다", message_id=msg, status="confirmed")
    proposal = row(db, result["event_id"])
    assert result["action"] == "asked" and proposal["dup_kind"] == "change"
    assert proposal["dup_of"] == old and proposal["status"] == "suggested"
    assert db.resolve_duplicate(proposal["id"], "replace")["ok"]
    assert row(db, old)["date"] == "2026-11-20"
    assert db.event_sources(old)[0]["relation"] == "change"


@pytest.mark.parametrize("text", ["19일과 20일 가상 협의회 안내", "20일 가상 협의회 연기"])
def test_no_change_without_word_or_old_date(db, text):
    db.add_calendar_event(title="가상 협의회", date="2026-11-19")
    assert change(db, text)["action"] == "created"


def test_ambiguous_old_dates_create(db):
    for title in ("가상 협의회", "가상 협의회 안내"):
        db.add_calendar_event(title=title, date="2026-11-19")
    assert change(db, "19일에서 20일로 연기")['action'] == 'created'


def test_same_date_location_change(db):
    old = db.add_calendar_event(title="가상 협의회", date="2026-11-20", location="가상 교실")
    result = change(db, "20일 장소 변경", location="가상 강당")
    assert result["action"] == "asked" and result["dup_of"] == old
    assert row(db, result["event_id"])["dup_kind"] == ""


def cancel(db, msg):
    return db.detect_cancellation(message_id=msg, text="20일 가상 연수는 취소되었습니다", ref_date="2026-11-01")


@pytest.mark.parametrize("action", ["cancel_event", "keep"])
def test_cancel_resolution_and_repeat(db, tmp_path, action):
    old = db.add_calendar_event(title="가상 연수", date="2026-11-20", start_time="14:00")
    msg = message(db)
    result = cancel(db, msg)
    proposal = row(db, result["event_id"])
    assert result["action"] == "asked" and proposal["dup_kind"] == "cancel" and proposal["dup_of"] == old
    assert proposal["start_time"] == "14:00"
    assert cancel(db, msg)["action"] == "linked"
    api = CalendarAPI(db, tmp_path / "prefs.json")
    assert api.confirm(result["event_id"]) == {"ok": False, "error": "취소 안내는 [일정 취소] 또는 [그대로 두기]로 골라 주세요."}
    assert api.confirm_all()["skipped"] == 1
    for bad in ("replace", "separate", "ignore"):
        assert not db.resolve_duplicate(result["event_id"], bad)["ok"]
    assert db.resolve_duplicate(result["event_id"], action)["ok"]
    if action == "cancel_event":
        assert row(db, old)["status"] == "dismissed" and row(db, result["event_id"]) is None
        assert db.event_sources(old)[0]["relation"] == "change"
        assert cancel(db, msg) is None
    else:
        assert row(db, old)["status"] == "confirmed" and row(db, result["event_id"])["status"] == "dismissed"
        assert cancel(db, msg)["action"] == "linked"


def test_cancel_no_or_ambiguous_or_dismissed(db):
    msg = message(db)
    assert cancel(db, msg) is None
    first = db.add_calendar_event(title="가상 연수", date="2026-11-20", status="dismissed")
    assert cancel(db, msg) is None
    db.add_calendar_event(title="가상 연수", date="2026-11-20")
    db.add_calendar_event(title="가상 연수 안내", date="2026-11-20")
    assert cancel(db, msg) is None
    assert row(db, first)["status"] == "dismissed"


def test_cancel_excludes_message_already_linked(db):
    msg = message(db)
    db.suggest_event(title="가상 연수", date="2026-11-20", message_id=msg)
    assert cancel(db, msg) is None


def test_old_version_insert_has_default(db):
    db.conn.execute("INSERT INTO calendar_events(title,date,status,origin) VALUES (?,?,?,?)",
                    ("가상 일정", "2026-11-20", "confirmed", "manual"))
    assert db.conn.execute("SELECT dup_kind FROM calendar_events").fetchone()[0] == ""


def test_pipeline_source_and_cancellation(db):
    class Analyzer:
        def analyze(self, m, text_for_ai):
            events = [Event(title="가상 협의회", date="2026-11-20")] if "연기" in m.body else []
            return Analysis(message_id=m.id, importance="참고", category="기타", summary="가상", events=events)

    settings = Settings()
    settings.calendar_auto_suggest = True
    settings.calendar_auto_confirm = False
    settings.skip_ai_min_chars = 0
    settings.skip_ai_keywords = []
    pipe = Pipeline(settings, db, Analyzer(), notify_levels=())
    old = db.add_calendar_event(title="가상 협의회", date="2026-11-19")
    m = Message(sender="가상", body="가상 협의회를 19일에서 20일로 연기합니다", received_at="2026-11-01")
    db.add_message(m)
    pipe.analyze_saved(m)
    assert db.conn.execute("SELECT dup_kind FROM calendar_events WHERE dup_of=?", (old,)).fetchone()[0] == "change"
    target = db.add_calendar_event(title="가상 연수", date="2026-11-20")
    m2 = Message(sender="가상", body="20일 가상 연수는 취소되었습니다", received_at="2026-11-01")
    db.add_message(m2)
    pipe.analyze_saved(m2)
    pipe.reanalyze(m2.id)
    assert db.conn.execute("SELECT count(*) FROM calendar_events WHERE dup_of=? AND dup_kind='cancel'", (target,)).fetchone()[0] == 1
    settings.calendar_auto_suggest = False
    target2 = db.add_calendar_event(title="가상 회의", date="2026-11-20")
    m3 = Message(sender="가상", body="20일 가상 회의는 취소되었습니다", received_at="2026-11-01")
    db.add_message(m3)
    pipe.analyze_saved(m3)
    pipe.reanalyze(m3.id)
    assert db.conn.execute("SELECT count(*) FROM calendar_events WHERE dup_of=?", (target2,)).fetchone()[0] == 0


def test_memo_create_and_reanalyze_pass_original_text(db, tmp_path, monkeypatch):
    settings = Settings()
    settings.memo_enabled = True
    now = datetime.now()
    before = now.strftime("%Y-%m-19")
    after = now.strftime("%Y-%m-20")
    old = db.add_calendar_event(title="가상 협의회", date=before)
    result = SimpleNamespace(title="가상 메모", kind="안내문", summary="가상", details=[],
                             items=[], action_items=[], provider="가상", model="가상",
                             events=[Event(title="가상 협의회", date=after)])
    monkeypatch.setattr(calendar_api, "get_settings", lambda reload=False: settings)
    monkeypatch.setattr(calendar_api, "create_provider", lambda *_: object())
    monkeypatch.setattr(calendar_api.memo_service, "organize", lambda *args, **kwargs: result)
    api = CalendarAPI(db, tmp_path / "prefs.json")
    created = api.memo_create({"mode": "text", "text": "가상 협의회를 19일에서 20일로 연기합니다"})
    assert created["ok"]
    proposal = db.conn.execute("SELECT * FROM calendar_events WHERE dup_of=?", (old,)).fetchone()
    assert proposal and proposal["dup_kind"] == "change"
    assert api.memo_reanalyze(created["memo"]["id"])["ok"]
    assert db.conn.execute("SELECT count(*) FROM calendar_events WHERE dup_of=?", (old,)).fetchone()[0] == 1


def _pipe(db, events_for):
    class Analyzer:
        def analyze(self, m, text_for_ai):
            return Analysis(message_id=m.id, importance="참고", category="기타", summary="가상", events=events_for(m))

    settings = Settings()
    settings.calendar_auto_suggest = True
    settings.calendar_auto_confirm = False
    settings.skip_ai_min_chars = 0
    settings.skip_ai_keywords = []
    return Pipeline(settings, db, Analyzer(), notify_levels=())


def test_cancel_notice_event_from_ai_is_not_suggested_again(db):
    """취소 쪽지에서 AI 가 그 일정을 또 뽑아도 새 제안·연결 없이 '취소?' 질문만"""
    pipe = _pipe(db, lambda m: [Event(title="가상 연수", date="2026-11-20")])
    target = db.add_calendar_event(title="가상 연수", date="2026-11-20", status="confirmed")
    m = Message(sender="가상", body="20일 가상 연수는 취소되었습니다", received_at="2026-11-01")
    db.add_message(m)
    pipe.analyze_saved(m)
    rows = db.conn.execute("SELECT id, dup_kind FROM calendar_events WHERE id != ?", (target,)).fetchall()
    assert [r["dup_kind"] for r in rows] == ["cancel"]
    assert row(db, target)["status"] == "confirmed"


@pytest.mark.parametrize("body", ["20일 가상 연수는 변동 없습니다", "20일 가상 연수 문제 없습니다"])
def test_no_change_words_are_not_cancellation(db, body):
    db.add_calendar_event(title="가상 연수", date="2026-11-20", status="confirmed")
    assert db.detect_cancellation(message_id=message(db), text=body, ref_date="2026-11-01") is None


def test_dates_ignore_durations_and_follow_context():
    assert dates_in_text("교육은 3.5시간, 1~2일 소요, 3일간 진행", "2026-11-01") == set()
    assert dates_in_text("2027년 11월 20일", "2026-11-01") == {"2027-11-20"}
    assert dates_in_text("12월 31일에서 1일로 연기", "2026-12-20") == {"2026-12-31", "2027-01-01"}
    assert dates_in_text("19일(목) 회의", "2026-11-01") == {"2026-11-19"}


def test_two_dates_without_move_link_is_not_change(db):
    db.add_calendar_event(title="가상 협의회", date="2026-11-19", status="confirmed")
    r = change(db, "19일 가상 협의회는 그대로, 20일 가상 협의회는 장소만 변경합니다")
    assert r["action"] == "created"


@pytest.mark.parametrize("body", ["20일 가상 연수는 취소하지 않습니다", "20일 가상 연수 신청 취소 안내",
                                  "20일 가상 연수 취소 시 연락 바랍니다"])
def test_not_cancellation_phrases(db, body):
    db.add_calendar_event(title="가상 연수", date="2026-11-20", status="confirmed")
    assert db.detect_cancellation(message_id=message(db), text=body, ref_date="2026-11-01") is None


def test_cancel_word_must_be_near_the_event_name(db):
    db.add_calendar_event(title="가상 연수", date="2026-11-20", status="confirmed")
    body = "20일 가상 연수는 예정대로 진행합니다. " + "안내 " * 20 + "다른 행사는 취소되었습니다."
    assert db.detect_cancellation(message_id=message(db), text=body, ref_date="2026-11-01") is None


def test_cancel_morning_keeps_afternoon_suggestion(db):
    pipe = _pipe(db, lambda m: [Event(title="가상 연수", date="2026-11-20", start_time="14:00")])
    db.add_calendar_event(title="가상 연수", date="2026-11-20", start_time="09:00", status="confirmed")
    m = Message(sender="가상", body="20일 오전 가상 연수는 취소되었고 오후 14시 연수는 정상 진행합니다", received_at="2026-11-01")
    db.add_message(m)
    pipe.analyze_saved(m)
    kinds = sorted(r["dup_kind"] for r in db.conn.execute("SELECT dup_kind FROM calendar_events WHERE dup_of IS NOT NULL"))
    assert kinds == ["", "cancel"]   # 취소 질문 + 오후 일정은 '같은 일인가요?' 질문으로 남음


def test_cancel_can_be_restored(db):
    target = db.add_calendar_event(title="가상 연수", date="2026-11-20", status="confirmed")
    asked = db.detect_cancellation(message_id=message(db), text="20일 가상 연수는 취소되었습니다", ref_date="2026-11-01")
    r = db.resolve_duplicate(asked["event_id"], "cancel_event")
    assert r["prev_status"] == "confirmed" and row(db, target)["status"] == "dismissed"
    assert db.restore_event(target, r["prev_status"])
    assert row(db, target)["status"] == "confirmed"
    assert not db.restore_event(target, "dismissed")

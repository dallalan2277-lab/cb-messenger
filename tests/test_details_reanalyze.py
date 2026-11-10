"""AI '자세히' 정리 · 다시 분석 · 일정→원문 — 가상 예시만 사용 (네트워크·키 없이)."""
import json
import sqlite3
from datetime import datetime

from app.analyzer import SCHEMA, Analyzer, clean_details
from app.collectors import message_from_text
from app.config import Settings
from app.desktop_calendar.api import CalendarAPI
from app.llm.base import LLMProvider
from app.pipeline import Pipeline
from app.storage import Storage

BODY = ("선생님들께, 2학기 교육과정 운영 실태 점검표를 11월 14일(금) 16시까지 업무포털에 올려 주세요. "
        "대상은 1~3학년 담임입니다. 문의는 교무부 내선 213 입니다.")


class Stub(LLMProvider):
    name = "stub"

    def __init__(self, reply):
        super().__init__("stub-model")
        self.reply = reply
        self.calls = 0

    def complete(self, system, user, max_tokens=1500):
        self.calls += 1
        return json.dumps(self.reply, ensure_ascii=False)


def _reply(**over):
    r = {"importance": "중요", "category": "처리 필요", "school_relevance": "학교 업무",
         "school_reason": "담임 업무", "school_evidence": "점검표를 11월 14일(금) 16시까지 업무포털에 올려 주세요",
         "summary": "실태 점검표를 금요일까지 제출", "reason": "제출 요청",
         "details": ["- 제출: 교육과정 운영 실태 점검표 (업무포털)", "마감: 11월 14일(금) 16시",
                     "대상: 1~3학년 담임", "문의: 교무부 내선 213", "인원: 25명 참석"],   # 마지막 줄의 25 는 원문에 없음
         "action_items": ["점검표 제출"], "evidence": "점검표를 11월 14일(금) 16시까지 업무포털에 올려 주세요",
         "deadline": "2026-11-14 16:00",
         "events": [{"title": "실태 점검표 제출 마감", "date": "2026-11-14", "start_time": "16:00",
                     "end_time": "", "location": "", "note": ""}]}
    r.update(over)
    return r


# ---------- 분석 ----------
def test_schema_requires_details():
    assert "details" in SCHEMA["required"] and SCHEMA["properties"]["details"]["items"] == {"type": "string"}


def test_details_kept_and_invented_numbers_dropped():
    a = Analyzer(Stub(_reply())).analyze(message_from_text(BODY), now=datetime(2026, 11, 10))
    assert a.details == ["제출: 교육과정 운영 실태 점검표 (업무포털)", "마감: 11월 14일(금) 16시",
                         "대상: 1~3학년 담임", "문의: 교무부 내선 213"]
    assert a.summary == "실태 점검표를 금요일까지 제출"        # 짧은 요약은 그대로 따로


def test_details_may_use_dates_the_ai_already_converted():
    """'다음 주 금요일' → 2026-11-14 처럼 AI 가 바꾼 날짜(마감·일정에 들어간 것)는 허용"""
    body = "다음 주 금요일까지 독서 감상문을 걷어 주세요."
    reply = _reply(details=["마감: 다음 주 금요일 (2026-11-14)", "할 일: 독서 감상문 걷기", "엉뚱한 날짜 12월 3일"],
                   evidence="독서 감상문을 걷어 주세요", school_evidence="독서 감상문을 걷어 주세요")
    a = Analyzer(Stub(reply)).analyze(message_from_text(body), now=datetime(2026, 11, 10))
    assert a.details == ["마감: 다음 주 금요일 (2026-11-14)", "할 일: 독서 감상문 걷기"]


def test_clean_details_edge_cases():
    assert clean_details(None, "x") == [] and clean_details({"a": 1}, "x") == []
    assert clean_details("첫 줄\n\n- 둘째 줄", "본문") == ["첫 줄", "둘째 줄"]           # 글자 하나로 와도 줄로 나눔
    assert len(clean_details([f"항목 {i}" for i in range(20)], "0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15")) == 8
    assert clean_details(["시각 09시"], "9시") == ["시각 09시"]                          # 앞의 0 은 같은 숫자
    assert clean_details(["a" * 500], "")[0] == "a" * 300


def test_missing_details_from_old_style_reply_is_empty():
    r = _reply()
    del r["details"]
    a = Analyzer(Stub(r)).analyze(message_from_text(BODY), now=datetime(2026, 11, 10))
    assert a.details == []


# ---------- 저장 ----------
def _pipe(tmp_path, reply):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.skip_ai_keywords, s.skip_ai_min_chars, s.redact_names = [], 0, []
    s.include_senders, s.exclude_senders = [], []
    s.calendar_auto_suggest, s.calendar_auto_confirm = True, False
    storage = Storage(s.db_path)
    return Pipeline(s, storage, Analyzer(Stub(reply)), notify_levels=())


def test_details_saved_and_shown_in_item(tmp_path):
    pipe = _pipe(tmp_path, _reply())
    a = pipe.handle(message_from_text(BODY, sender="교무부장"))
    api = CalendarAPI(pipe.storage, tmp_path / "prefs.json")
    item = api.item(a.message_id)
    assert item["details"][0].startswith("제출:") and len(item["details"]) == 4
    assert item["ai_model"] == "stub:stub-model"
    assert api.message(a.message_id)["source"] == "manual"


def test_old_database_gets_details_column(tmp_path):
    db = tmp_path / "old.db"
    Storage(str(db)).close()
    con = sqlite3.connect(db)                                  # 예전 DB 처럼 details 칸을 없앤 표를 만듦
    cols = [r[1] for r in con.execute("PRAGMA table_info(analyses)") if r[1] != "details"]
    con.execute("ALTER TABLE analyses RENAME TO a_old")
    con.execute(f"CREATE TABLE analyses AS SELECT {', '.join(cols)} FROM a_old WHERE 0")
    con.execute("DROP TABLE a_old")
    con.commit()
    con.close()
    s = Storage(str(db))
    assert "details" in [r[1] for r in s.conn.execute("PRAGMA table_info(analyses)")]


# ---------- 다시 분석 ----------
def test_reanalyze_replaces_analysis_and_keeps_one_row(tmp_path):
    old = _reply(details=[], summary="짧은 요약")
    pipe = _pipe(tmp_path, old)
    a = pipe.handle(message_from_text(BODY, sender="교무부장"))
    pipe.analyzer.provider.reply = _reply()
    b = pipe.reanalyze(a.message_id)
    assert b.details and b.summary == "실태 점검표를 금요일까지 제출"
    rows = pipe.storage.conn.execute("SELECT COUNT(*) FROM analyses WHERE message_id=?", (a.message_id,)).fetchone()[0]
    assert rows == 1
    evs = pipe.storage.calendar_events("2026-11-14", "2026-11-14")
    assert len(evs) == 1                                        # 같은 일정은 다시 제안하지 않음


def test_reanalyze_failure_keeps_old_analysis(tmp_path):
    pipe = _pipe(tmp_path, _reply(details=[]))
    a = pipe.handle(message_from_text(BODY, sender="교무부장"))

    def boom(*args, **kw):
        raise RuntimeError("한도 도달")

    pipe.analyzer.provider.complete = boom
    try:
        pipe.reanalyze(a.message_id)
        raise AssertionError("실패해야 함")
    except RuntimeError:
        pass
    assert pipe.storage.get_message(a.message_id)["summary"] == "실태 점검표를 금요일까지 제출"


def test_api_reanalyze_uses_settings_and_reports_errors(tmp_path, monkeypatch):
    import app.config as config
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.analyze_model = s.digest_model = "fake"
    s.fallback_model, s.skip_ai_keywords, s.skip_ai_min_chars, s.redact_names = "", [], 0, []
    s.include_senders, s.exclude_senders = [], []
    monkeypatch.setattr(config, "get_settings", lambda reload=False: s)
    storage = Storage(s.db_path)
    m = message_from_text("내일 오전 9시까지 회의 자료를 제출 바랍니다", sender="교무부장")
    storage.add_message(m)
    api = CalendarAPI(storage, tmp_path / "prefs.json")
    refreshed = []
    api._host = type("H", (), {"refresh": lambda self: refreshed.append(1)})()
    r = api.reanalyze(m.id)
    assert r["ok"] is True and r["model"].startswith("fake") and refreshed
    assert api.item(m.id)["summary"]
    assert "찾을 수 없습니다" in api.reanalyze(99999)["error"]


def test_clean_details_accepts_24h_conversion_of_pm_time():
    src = "11월 18일(수) 오후 3시, 본관 2층 회의실"
    assert clean_details(["일시: 2026-11-18 15:00"], src, "2026-11-18") == ["일시: 2026-11-18 15:00"]
    assert clean_details(["일시: 11월 18일 16:00"], src) == []          # 원문에 없는 시각(4시)은 여전히 뺌

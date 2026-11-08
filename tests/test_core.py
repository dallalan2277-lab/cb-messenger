"""API 키 없이 돌릴 수 있는 기본 테스트:  python -m pytest -q"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analyzer import Analyzer
from app.collectors import JsonFileCollector, message_from_text
from app.config import Settings
from app.extensions.ics_export import export_ics
from app.llm.base import LLMProvider, parse_json_loose
from app.models import Event
from app.pipeline import Pipeline
from app.redact import redact
from app.storage import Storage

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "sample_messages.json"


class StubProvider(LLMProvider):
    """정해진 JSON을 돌려주는 가짜 AI. 마지막 입력을 기록합니다."""
    name = "stub"

    def __init__(self, reply: dict):
        super().__init__("stub-model")
        self.reply = reply
        self.last_user = ""

    def complete(self, system, user, max_tokens=1500):
        self.last_user = user
        return "설명입니다\n```json\n" + json.dumps(self.reply, ensure_ascii=False) + "\n```"


def test_redact():
    t = "3학년 2반 15번 김민수 학생 보호자 010-1234-5678, 학교 043-123-4567, a.b@school.kr"
    r = redact(t, names=["김민수"])
    assert "010-1234-5678" not in r and "043-123-4567" not in r
    assert "a.b@school.kr" not in r and "김민수" not in r and "15번" not in r
    assert "[전화번호]" in r and "[이름1]" in r


def test_parse_json_loose():
    assert parse_json_loose('앞말 {"a": 1} 뒷말') == {"a": 1}
    assert parse_json_loose('```json\n{"a": 2}\n```') == {"a": 2}


def test_analyzer_normalizes():
    p = StubProvider({"importance": "아주급함", "summary": " 요약 ", "action_items": "하나",
                      "evidence": "자료를 금요일까지 제출",
                      "events": [{"title": "회의", "date": "2026-11-12", "start_time": "15:30"},
                                 {"title": "", "date": ""}]})
    a = Analyzer(p).analyze(message_from_text("자료를 금요일까지  제출 바랍니다"), now=datetime(2026, 11, 9))
    assert a.importance == "참고"          # 잘못된 값 → 기본값
    assert a.summary == "요약"
    assert a.action_items == ["하나"]      # 근거가 원문에 있으면(공백 차이 무시) 유지
    assert len(a.events) == 1 and a.events[0].start_time == "15:30"


def test_pipeline_end_to_end(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.redact_enabled = True
    s.redact_names = []
    s.include_senders, s.exclude_senders = [], ["행정실"]
    st = Storage(s.db_path)
    p = StubProvider({"importance": "긴급", "category": "제출", "summary": "오늘 16시까지 입력",
                      "events": [{"title": "회의", "date": "2026-11-12", "start_time": "15:30"}]})
    pipe = Pipeline(s, st, Analyzer(p), notify_levels=())
    results = [pipe.handle(m) for m in JsonFileCollector(SAMPLES).collect_once()]
    assert sum(1 for r in results if r) == 4               # 행정실 제외
    # 같은 파일을 다시 넣어도 중복 저장 안 됨
    again = [pipe.handle(m) for m in JsonFileCollector(SAMPLES).collect_once()]
    assert not any(again)
    rows = st.recent()
    assert len(rows) == 4 and rows[0][1].importance == "긴급"

    out = export_ics([e for _, a in rows for e in a.events], tmp_path / "e.ics")
    txt = out.read_text(encoding="utf-8")
    assert txt.count("BEGIN:VEVENT") == 4 and "DTSTART;TZID=Asia/Seoul:20261112T153000" in txt


def test_redaction_reaches_ai(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.redact_enabled, s.redact_names = True, ["김민수"]
    s.include_senders, s.exclude_senders = [], []
    p = StubProvider({"importance": "중요", "summary": "x"})
    pipe = Pipeline(s, Storage(s.db_path), Analyzer(p), notify_levels=())
    pipe.handle(message_from_text("김민수 보호자 010-9999-8888 연락 바랍니다"))
    assert "김민수" not in p.last_user and "010-9999-8888" not in p.last_user


def test_all_day_event_ics(tmp_path):
    out = export_ics([Event(title="연수 신청 마감", date="2026-11-14")], tmp_path / "a.ics")
    assert "DTSTART;VALUE=DATE:20261114" in out.read_text(encoding="utf-8")


def test_unverified_evidence_is_dropped():
    from app.models import Message
    p = StubProvider({"importance": "긴급", "summary": "s", "action_items": ["송금하기"], "evidence": "지금 바로 송금하세요",
                      "school_relevance": "학교 업무", "school_evidence": "교육청 공문 처리"})
    m = Message(sender="x <x@x.com>", body="[제목] 안내\n\n좋은 하루 되세요", source="mail", subject="안내")
    a = Analyzer(p).analyze(m)
    assert a.action_items == [] and not a.verified and "확인하지 못해" in a.reason
    assert a.school_relevance == "판단 보류"
    assert '"subject": "안내"' in p.last_user and '"source": "메일"' in p.last_user


def test_mail_prompt_has_guard_and_schema_passed():
    from app.analyzer import SCHEMA
    from app.models import Message
    seen = {}

    class P(StubProvider):
        def complete_json(self, system, user, max_tokens=1500, schema=None):
            seen.update(system=system, schema=schema)
            return {"importance": "중요", "summary": "s", "school_relevance": "학교 업무", "school_evidence": "학업성적관리위원회"}
    a = Analyzer(P({})).analyze(Message(sender="a", body="학업성적관리위원회 회의 안내", source="mail", subject="회의"))
    assert "피싱" in seen["system"] and seen["schema"] is SCHEMA
    assert a.school_relevance == "학교 업무" and a.verified
    Analyzer(P({})).analyze(message_from_text("학업성적관리위원회"))
    assert "피싱" not in seen["system"]                       # 쪽지에는 메일 전용 경고 없음

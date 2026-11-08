"""알림 규칙 테스트"""
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Analysis, Message
from app.notify_policy import SEOUL, NotifyPrefs, decide, in_quiet, toast_text, validate

DAY = datetime(2026, 11, 9, 10, 0, tzinfo=SEOUL)


def A(imp="중요", rel="학교 업무", provider="stub"):
    return Analysis(message_id=1, importance=imp, category="처리 필요", summary="회의 자료 제출",
                    action_items=["자료 제출"], school_relevance=rel, provider=provider)


MAIL = Message(sender="교무부 <office@school.kr>", body="x", source="mail", subject="자료 제출 요청")
NOTE = Message(sender="교감", body="x", source="eztalk")


def test_default_school_scope():
    p = NotifyPrefs()
    assert decide(p, MAIL, A(), DAY) == (True, "school")
    assert decide(p, MAIL, A("참고"), DAY)[0]                       # 학교 업무면 참고도 알림 (MailDesk 기본과 같음)
    assert decide(p, MAIL, A(rel="일반"), DAY) == (False, "not_school")
    assert decide(p, MAIL, A(rel="판단 보류"), DAY) == (False, "not_school")
    assert decide(replace(p, high_only=True), MAIL, A("중요"), DAY) == (False, "not_high")


def test_other_scopes_and_flags():
    p = NotifyPrefs(scope="important")
    assert decide(p, NOTE, A("중요", "일반"), DAY)[0] and not decide(p, NOTE, A("참고"), DAY)[0]
    assert decide(NotifyPrefs(scope="all"), MAIL, A("참고", "일반"), DAY)[0]
    assert decide(NotifyPrefs(enabled=False), MAIL, A(), DAY) == (False, "disabled")
    assert decide(NotifyPrefs(), MAIL, A(), DAY, done=True) == (False, "done")
    assert decide(NotifyPrefs(), MAIL, A(provider="rule"), DAY) == (False, "skipped")
    assert decide(NotifyPrefs(sources=("eztalk",)), MAIL, A(), DAY) == (False, "source_off")


def test_quiet_hours_across_midnight():
    p = NotifyPrefs()   # 22:00 ~ 08:00
    at = lambda h, m=0: datetime(2026, 11, 9, h, m, tzinfo=SEOUL)
    assert in_quiet(p, at(22, 0)) and in_quiet(p, at(3)) and in_quiet(p, at(7, 59))
    assert not in_quiet(p, at(8, 0)) and not in_quiet(p, at(21, 59))
    assert decide(p, MAIL, A(), at(23)) == (False, "quiet_hours")
    day = NotifyPrefs(quiet_start="12:00", quiet_end="13:00")
    assert in_quiet(day, at(12, 30)) and not in_quiet(day, at(13, 0))
    assert not in_quiet(replace(p, quiet_enabled=False), at(23))


def test_preview_hidden_by_default():
    t, b = toast_text(NotifyPrefs(), MAIL, A())
    assert "자료 제출 요청" not in t + b and "교무부" not in t + b and "새 메일" in t
    t, b = toast_text(NotifyPrefs(preview=True), MAIL, A())
    assert t == "[중요 · 학교 업무] 교무부" and "자료 제출 요청" in b and "할 일: 자료 제출" in b


def test_validate():
    assert validate("school", "22:00", "08:00") == ""
    assert validate("everything", "22:00", "08:00")
    assert validate("school", "25:00", "08:00") and validate("school", "08:00", "08:00")

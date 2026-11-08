"""알림 규칙 (쪽지·메일 공통) — MailDesk 0.3.0 의 알림 정책을 참고.

- 범위: school(학교 업무만, 기본) / important(긴급·중요) / all(분석된 모든 것)
- 높음만: 켜면 긴급만
- 방해금지: 한국 시각 HH:MM~HH:MM (시작 포함, 끝 제외, 자정 넘김 가능). 막힌 알림은 나중에 몰아서 보내지 않음
- 미리보기: 끄면(기본) 알림에 보낸 사람·제목·요약을 숨김
- 소리: 기본 끔
- 처리 완료·AI 생략(규칙 처리) 건은 알리지 않음. 방해금지·알림 꺼짐이어도 수집과 분석은 계속됨
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import Analysis, Message

SEOUL = timezone(timedelta(hours=9))
SCOPES = ("school", "important", "all")
SCOPE_LABELS = {"school": "학교 업무만", "important": "긴급·중요만", "all": "분석된 모든 것"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class NotifyPrefs:
    enabled: bool = True
    scope: str = "school"
    high_only: bool = False
    sound: bool = False
    quiet_enabled: bool = True
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    preview: bool = False
    sources: tuple = ("eztalk", "mail")

    @classmethod
    def from_settings(cls, s) -> "NotifyPrefs":
        scope = s.notify_scope if s.notify_scope in SCOPES else "school"   # 잘못된 값으로 범위가 넓어지지 않게
        qs = s.notify_quiet_start if TIME_RE.match(s.notify_quiet_start or "") else "22:00"
        qe = s.notify_quiet_end if TIME_RE.match(s.notify_quiet_end or "") else "08:00"
        return cls(enabled=s.notify_enabled, scope=scope, high_only=s.notify_high_only, sound=s.notify_sound,
                   quiet_enabled=s.notify_quiet and qs != qe, quiet_start=qs, quiet_end=qe,
                   preview=s.notify_preview, sources=tuple(s.notify_sources))


def validate(scope: str, quiet_start: str, quiet_end: str) -> str:
    """설정 화면 저장 전 확인. 문제가 있으면 한국어 설명, 없으면 ''"""
    if scope not in SCOPES:
        return "알림 범위는 학교 업무만·긴급·중요만·모든 것 중에서 고르세요."
    if not TIME_RE.match(quiet_start or "") or not TIME_RE.match(quiet_end or ""):
        return "방해금지 시각은 24시간 형식 HH:MM 으로 입력하세요. (예: 22:00)"
    if quiet_start == quiet_end:
        return "방해금지 시작 시각과 끝 시각을 다르게 입력하세요."
    return ""


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet(p: NotifyPrefs, now: datetime) -> bool:
    if not p.quiet_enabled:
        return False
    t = now.astimezone(SEOUL) if now.tzinfo else now
    cur, a, b = t.hour * 60 + t.minute, _minutes(p.quiet_start), _minutes(p.quiet_end)
    return a <= cur < b if a < b else (cur >= a or cur < b)


def decide(p: NotifyPrefs, m: Message, a: Analysis, now: datetime | None = None, done: bool = False) -> tuple[bool, str]:
    """(알릴지, 이유)"""
    now = now or datetime.now(SEOUL)
    if not p.enabled:
        return False, "disabled"
    if m.source not in p.sources and m.source not in ("manual",):
        return False, "source_off"
    if done:
        return False, "done"
    if a.provider == "rule":
        return False, "skipped"
    if in_quiet(p, now):
        return False, "quiet_hours"
    if p.scope == "school":
        if a.school_relevance != "학교 업무":
            return False, "not_school"
        if p.high_only and a.importance != "긴급":
            return False, "not_high"
        return True, "school"
    if p.scope == "important":
        ok = a.importance == "긴급" if p.high_only else a.importance in ("긴급", "중요")
        return (ok, "important" if ok else "not_important")
    if p.high_only and a.importance != "긴급":
        return False, "not_high"
    return True, "all"


def toast_text(p: NotifyPrefs, m: Message, a: Analysis) -> tuple[str, str]:
    kind = "메일" if m.source == "mail" else "쪽지" if m.source == "eztalk" else "메시지"
    tag = f"{a.importance}" + (" · 학교 업무" if a.school_relevance == "학교 업무" else "")
    if not p.preview:
        return f"[{tag}] 새 {kind}", "캘린더 옆 '받은 소식'에서 확인하세요. (알림 미리보기 꺼짐)"
    who = re.sub(r"\s*<[^>]+>", "", m.sender).strip() or m.sender
    title = f"[{tag}] {who}"
    body = (f"{m.subject} — " if m.subject else "") + (a.summary or "")
    if a.action_items:
        body += f"\n할 일: {a.action_items[0]}"
    return title, body[:240]

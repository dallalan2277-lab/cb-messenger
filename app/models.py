"""공통 데이터 구조."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Message:
    """메신저에서 수집한 메시지 1건."""
    sender: str
    body: str
    received_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    room: str = ""                 # 대화방/쪽지함 이름 (알 수 없으면 빈 값)
    source: str = "manual"         # manual | notification | uia | ocr ...
    external_id: str = ""          # 수집기 쪽 고유 ID (중복 방지용)
    id: Optional[int] = None       # DB 저장 후 부여
    subject: str = ""              # 메일 제목 (쪽지는 빈 값). 본문 맨 앞 '[제목] …' 에도 들어 있음
    partial: bool = False          # 본문 일부만 있음 (큰 메일 등)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    """메시지에서 추출한 일정 후보."""
    title: str
    date: str                      # YYYY-MM-DD
    start_time: str = ""           # HH:MM (없으면 종일)
    end_time: str = ""
    location: str = ""
    note: str = ""


@dataclass
class Analysis:
    """AI 분석 결과."""
    message_id: Optional[int]
    importance: str                # 긴급 | 중요 | 참고
    category: str                  # 공문/회의/제출/행사/안내/기타
    summary: str
    action_items: list[str] = field(default_factory=list)
    deadline: str = ""             # YYYY-MM-DD 또는 YYYY-MM-DD HH:MM
    events: list[Event] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    reason: str = ""               # 판단 근거
    evidence: str = ""             # 할 일의 원문 인용 (원문에서 확인된 것만)
    school_relevance: str = "판단 보류"   # 학교 업무 | 일반 | 판단 보류
    school_reason: str = ""
    school_evidence: str = ""
    verified: bool = True          # False = AI 답 일부를 원문 근거가 없어 고침
    details: list[str] = field(default_factory=list)   # 자세히: 무엇·언제·어디·대상·준비물·주의·문의 (3~6줄)

    def to_dict(self) -> dict:
        return asdict(self)


IMPORTANCE_LEVELS = ("긴급", "중요", "참고")

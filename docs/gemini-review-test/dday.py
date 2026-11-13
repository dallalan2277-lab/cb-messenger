"""Gemini 검토 흐름 시험용 예제 (앱에서 import하지 않음, 머지하지 않을 PR).

일정 마감까지 남은 날짜를 'D-3' 같은 배지 글자로 만든다.
"""
from datetime import datetime


def days_until(deadline: datetime, now: datetime | None = None) -> int:
    """마감까지 남은 날 수. 오늘 마감이면 0."""
    now = now or datetime.now()
    return (deadline - now).days


def dday_label(deadline: datetime, now: datetime | None = None) -> str:
    """배지 글자: 남은 날이 있으면 'D-3', 오늘이면 'D-DAY'."""
    days = days_until(deadline, now)
    if days == 0:
        return "D-DAY"
    return f"D-{days}"

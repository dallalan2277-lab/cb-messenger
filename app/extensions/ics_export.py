"""[확장 1단계] 추출한 일정을 .ics 파일로 내보내기.

.ics 파일은 Google 캘린더, Outlook, 네이버 캘린더에서 '가져오기'로 바로 등록할 수 있습니다.
API 연동(자동 등록)은 다음 단계 — HANDOFF.md 참고.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..models import Event


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def event_to_vevent(e: Event) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    lines = ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@cb-messenger-ai", f"DTSTAMP:{stamp}"]
    d = e.date.replace("-", "")
    if e.start_time:
        start = datetime.strptime(f"{e.date} {e.start_time}", "%Y-%m-%d %H:%M")
        end = (datetime.strptime(f"{e.date} {e.end_time}", "%Y-%m-%d %H:%M")
               if e.end_time else start + timedelta(hours=1))
        lines += [f"DTSTART;TZID=Asia/Seoul:{start:%Y%m%dT%H%M%S}",
                  f"DTEND;TZID=Asia/Seoul:{end:%Y%m%dT%H%M%S}"]
    else:
        nxt = (datetime.strptime(e.date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
        lines += [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nxt}"]
    lines.append(f"SUMMARY:{_esc(e.title)}")
    if e.location:
        lines.append(f"LOCATION:{_esc(e.location)}")
    if e.note:
        lines.append(f"DESCRIPTION:{_esc(e.note)}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def export_ics(events: list[Event], path: str | Path) -> Path:
    body = "\r\n".join(event_to_vevent(e) for e in events)
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//cb-messenger-ai//KO\r\n"
            "CALSCALE:GREGORIAN\r\n" + body + "\r\nEND:VCALENDAR\r\n")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p

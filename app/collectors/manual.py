"""테스트용 수집기: JSON 파일이나 직접 입력한 텍스트에서 메시지를 만듭니다."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from ..models import Message
from .base import Collector


def _ext_id(sender: str, body: str, received_at: str) -> str:
    return hashlib.sha1(f"{sender}|{received_at}|{body}".encode("utf-8")).hexdigest()[:16]


class JsonFileCollector(Collector):
    """samples/sample_messages.json 같은 파일을 읽습니다.

    형식: [{"sender": "...", "room": "...", "body": "...", "received_at": "2026-11-09T09:10:00"}, ...]
    """
    name = "json"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def collect_once(self) -> Iterable[Message]:
        items = json.loads(self.path.read_text(encoding="utf-8"))
        for it in items:
            m = Message(sender=it.get("sender", "(알 수 없음)"), body=it["body"],
                        room=it.get("room", ""), source="manual")
            if it.get("received_at"):
                m.received_at = it["received_at"]
            m.external_id = _ext_id(m.sender, m.body, m.received_at)
            yield m


def message_from_text(text: str, sender: str = "(직접 입력)", room: str = "") -> Message:
    m = Message(sender=sender, body=text.strip(), room=room, source="manual")
    m.external_id = _ext_id(m.sender, m.body, m.received_at)
    return m

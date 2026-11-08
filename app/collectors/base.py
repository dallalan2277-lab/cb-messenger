"""메시지 수집기 공통 인터페이스.

메신저에서 메시지를 가져오는 방법(알림 감지, 창 읽기, OCR 등)을
바꾸고 싶으면 Collector 를 상속해 새 수집기를 만들면 됩니다.
나머지 파이프라인(가리기 → 저장 → AI 분석 → 알림)은 그대로 재사용됩니다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterable

from ..models import Message

OnMessage = Callable[[Message], None]


class Collector(ABC):
    name: str = "base"

    @abstractmethod
    def collect_once(self) -> Iterable[Message]:
        """지금 새로 들어온 메시지를 돌려줍니다 (한 번 확인)."""

    def run_forever(self, on_message: OnMessage, interval_sec: float = 3.0) -> None:
        """주기적으로 collect_once 를 호출합니다. Ctrl+C 로 종료."""
        import time
        while True:
            for m in self.collect_once():
                on_message(m)
            time.sleep(interval_sec)

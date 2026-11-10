"""AI 제공자 공통 인터페이스.

새 제공자(예: Gemini, 로컬 LLM)를 추가하려면 LLMProvider 를 상속해
complete() 만 구현하고 factory.py 에 등록하면 됩니다.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"
    supports_images: bool = False

    def __init__(self, model: str):
        self.model = model
        self.reasoning: str = "default"         # default|low|medium|high (지원하는 제공자만 적용)
        self.last_usage: dict = {}              # 마지막 호출의 토큰 사용량 {input_tokens, output_tokens}

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        """시스템 프롬프트와 사용자 입력을 받아 모델의 응답 텍스트를 돌려줍니다."""

    def complete_json(self, system: str, user: str, max_tokens: int = 1500, schema: dict | None = None) -> dict:
        """JSON 응답을 요청하고 dict 로 파싱합니다.
        schema(JSON 스키마)를 지원하는 제공자는 형식을 강제하고, 아니면 프롬프트에 적힌 형식을 따릅니다."""
        text = self.complete(system, user, max_tokens=max_tokens)
        return parse_json_loose(text)

    def complete_json_image(self, system: str, user: str, image_bytes: bytes,
                            mime: str, schema: dict | None = None) -> dict:
        """이미지와 글을 함께 보내 JSON 객체를 받습니다.

        이미지 입력을 구현한 제공자만 이 메서드를 재정의합니다.
        """
        raise LLMError("이 AI 연결은 이미지를 받지 못합니다.")


def parse_json_loose(text: str) -> dict:
    """```json 코드블록이나 앞뒤 설명이 섞여 있어도 JSON 객체를 꺼냅니다."""
    if not text:
        raise LLMError("빈 응답")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"JSON을 찾지 못함: {text[:200]}")
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 파싱 실패: {e}: {text[:200]}") from e

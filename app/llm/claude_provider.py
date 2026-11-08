"""Anthropic(Claude) 제공자."""
from __future__ import annotations

from .base import LLMProvider, LLMError


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY 가 설정되지 않았습니다 (.env 확인)")
        import anthropic  # 필요할 때만 import
        self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        u = getattr(resp, "usage", None)
        self.last_usage = {"input_tokens": getattr(u, "input_tokens", 0) or 0,
                           "output_tokens": getattr(u, "output_tokens", 0) or 0} if u else {}
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

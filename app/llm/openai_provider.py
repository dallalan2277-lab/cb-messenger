"""OpenAI(GPT) 제공자."""
from __future__ import annotations

from .base import LLMProvider, LLMError, parse_json_loose


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        if not api_key:
            raise LLMError("OPENAI_API_KEY 가 설정되지 않았습니다 (.env 확인)")
        from openai import OpenAI  # 필요할 때만 import
        self.client = OpenAI(api_key=api_key)

    def _extra(self) -> dict:
        return {"reasoning_effort": self.reasoning} if self.reasoning != "default" else {}

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        self.last_usage = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                           "output_tokens": getattr(u, "completion_tokens", 0) or 0} if u else {}

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_completion_tokens=max_tokens,
            **self._extra(),
        )
        self._record(resp)
        return resp.choices[0].message.content or ""

    def complete_json(self, system: str, user: str, max_tokens: int = 1500, schema: dict | None = None) -> dict:
        # GPT 는 답변 형식(JSON 스키마)을 강제할 수 있으므로 우선 사용하고, 실패 시 JSON 모드 → 일반 호출 순으로
        if schema:
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "item_assessment", "strict": True, "schema": schema}},
                    max_completion_tokens=max(max_tokens, 3000), **self._extra(),
                )
                self._record(resp)
                return parse_json_loose(resp.choices[0].message.content or "")
            except Exception:
                pass
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=max_tokens,
                **self._extra(),
            )
            self._record(resp)
            return parse_json_loose(resp.choices[0].message.content or "")
        except LLMError:
            raise
        except Exception:
            return super().complete_json(system, user, max_tokens)

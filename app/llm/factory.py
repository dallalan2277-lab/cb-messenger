"""설정에 따라 AI 제공자를 만들어 줍니다.

모델 지정 형식: "제공자" 또는 "제공자:모델"
  chatgpt_plan  ChatGPT Plus/Pro 구독 (Sign in with ChatGPT)       예) chatgpt_plan:gpt-5.6-luna
  claude_cli    Claude Pro/Max 구독 (Claude Code `claude -p`)     예) claude_cli:haiku
  openai        OpenAI API 키 (사용량 결제)                         예) openai:gpt-5-mini
  claude        Anthropic API 키 (사용량 결제)                      예) claude:claude-haiku-5-5
  fake          키 없이 테스트
"""
from __future__ import annotations

import logging

from ..config import Settings
from .base import LLMProvider, LLMError

log = logging.getLogger(__name__)

PROVIDERS = ("chatgpt_plan", "claude_cli", "openai", "claude", "fake")
PROVIDER_LABELS = {
    "chatgpt_plan": "ChatGPT 구독",
    "claude_cli": "Claude 구독",
    "openai": "OpenAI API 키",
    "claude": "Anthropic API 키",
    "fake": "테스트(가짜)",
}


def parse_spec(spec: str) -> tuple[str, str]:
    """'chatgpt_plan:gpt-5.6-luna' → ('chatgpt_plan', 'gpt-5.6-luna')."""
    spec = (spec or "").strip()
    if ":" in spec:
        p, m = spec.split(":", 1)
        return p.strip().lower(), m.strip()
    return spec.lower(), ""


def _build(settings: Settings, name: str, model: str) -> LLMProvider:
    if name in ("openai", "gpt"):
        from .openai_provider import OpenAIProvider
        return OpenAIProvider(settings.openai_api_key, model or settings.openai_model)
    if name in ("claude", "anthropic"):
        from .claude_provider import ClaudeProvider
        return ClaudeProvider(settings.anthropic_api_key, model or settings.claude_model)
    if name in ("chatgpt_plan", "chatgpt"):
        from .chatgpt_plan_provider import ChatGPTPlanProvider
        return ChatGPTPlanProvider(model or settings.chatgpt_plan_model)
    if name in ("claude_cli", "claude_code"):
        from .claude_cli_provider import ClaudeCLIProvider
        return ClaudeCLIProvider(model or settings.claude_cli_model)
    if name == "fake":
        from .fake_provider import FakeProvider
        return FakeProvider(model or "rule-based")
    raise LLMError(f"알 수 없는 AI 제공자: {name} ({', '.join(PROVIDERS)} 중 하나)")


class FallbackProvider(LLMProvider):
    """주 모델이 실패(한도 초과·로그인 만료·네트워크 등)하면 대체 모델로 다시 시도.

    대체 모델을 처음에 준비하지 못했으면(예: Claude Code 미설치) fallback=None 과 build_fallback 을 받아,
    주 모델이 실패할 때마다 다시 준비해 봄 → 나중에 설치해도 프로그램을 다시 켤 필요 없음.
    준비하지 못한 이유는 fallback_error 에 남겨 화면에 경고로 보여 줌."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None,
                 build_fallback=None, fallback_error: str = ""):
        super().__init__(primary.model)
        self.primary, self.fallback = primary, fallback
        self._build_fallback = build_fallback
        self.fallback_error = "" if fallback else fallback_error
        self.name = primary.name
        self.used = primary

    def _ensure_fallback(self) -> LLMProvider | None:
        if self.fallback is None and self._build_fallback is not None:
            try:
                self.fallback = self._build_fallback()
                self.fallback_error = ""
            except LLMError as e:
                self.fallback_error = str(e)
        return self.fallback

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return self._call("complete", system, user, max_tokens)

    def complete_json(self, system: str, user: str, max_tokens: int = 1500, schema: dict | None = None) -> dict:
        return self._call("complete_json", system, user, max_tokens, schema=schema)

    def _call(self, method, *args, **kw):
        try:
            out = getattr(self.primary, method)(*args, **kw)
            self.used = self.primary
        except Exception as e:  # noqa: BLE001
            fb = self._ensure_fallback()
            if fb is None:
                log.warning("주 모델(%s) 실패, 대체 모델을 쓸 수 없음: %s", self.primary.label, self.fallback_error)
                raise LLMError(f"{e} (대체 모델도 쓸 수 없음: {self.fallback_error})") from e
            log.warning("주 모델(%s) 실패 → 대체 모델(%s) 사용: %s", self.primary.label, fb.label, e)
            out = getattr(fb, method)(*args, **kw)
            self.used = fb
        # 실제로 응답한 모델 기준으로 기록
        self.name, self.model, self.last_usage = self.used.name, self.used.model, self.used.last_usage
        return out


def create_provider(settings: Settings, spec: str | None = None, *, with_fallback: bool = True) -> LLMProvider:
    """spec 예: 'chatgpt_plan:gpt-5.6-luna'. 비우면 분석 모델 슬롯 사용."""
    name, model = parse_spec(spec or settings.analyze_model)
    fb, fb_error, build_fb = None, "", None
    if with_fallback and settings.fallback_model and parse_spec(settings.fallback_model) != (name, model):
        def build_fb() -> LLMProvider:
            p = _build(settings, *parse_spec(settings.fallback_model))
            p.reasoning = settings.reasoning
            return p
        try:
            fb = build_fb()
        except LLMError as e:
            fb_error = str(e)
            log.warning("대체 모델을 준비하지 못했습니다 (%s): %s", settings.fallback_model, e)
    try:
        primary = _build(settings, name, model)
    except LLMError as e:
        if fb is None:
            raise
        log.warning("주 모델을 준비하지 못해 대체 모델만 사용합니다: %s", e)  # 예: ChatGPT 로그인 만료
        return fb
    primary.reasoning = settings.reasoning
    if build_fb is None:
        return primary
    return FallbackProvider(primary, fb, build_fallback=build_fb, fallback_error=fb_error)

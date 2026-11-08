"""ChatGPT 구독(Plus/Pro) 사용량으로 GPT 를 호출하는 제공자 — Sign in with ChatGPT.

공식 제약 (https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations):
  - 공개 Responses API(POST /v1/responses)만 사용, ChatGPT backend-api 금지
  - store=false, stream=true 필수, input 은 배열
  - system 역할 메시지 금지 → instructions 사용
  - temperature, max_output_tokens 등은 보내면 안 됨
  - response.completed 이벤트를 받아야 성공으로 처리
"""
from __future__ import annotations

import json

import httpx

from .base import LLMProvider, LLMError, parse_json_loose
from .chatgpt_auth import ChatGPTSession, ChatGPTAuthError, RESOURCE

RESPONSES_URL = f"{RESOURCE}/responses"
MODELS_URL = f"{RESOURCE}/models"
USAGE_LIMIT_CODES = {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}


def list_models(session: ChatGPTSession, http: httpx.Client | None = None) -> list[dict]:
    """로그인한 계정이 쓸 수 있는 모델 목록 [{slug, display_name}] (서버 순서 유지)."""
    http = http or httpx.Client(timeout=30)
    r = http.get(MODELS_URL, headers={"Authorization": f"Bearer {session.access_token()}"})
    r.raise_for_status()
    return [{"slug": m["slug"], "display_name": m.get("display_name", m["slug"])}
            for m in r.json().get("models", []) if m.get("visibility") == "list"]


def parse_sse_text(lines, usage_out: dict | None = None) -> str:
    """Responses 스트림(SSE)에서 텍스트를 모읍니다. completed 가 없으면 오류.
    usage_out 를 넘기면 response.completed 의 토큰 사용량을 채웁니다."""
    chunks: list[str] = []
    completed = False
    for raw in lines:
        line = raw.strip() if isinstance(raw, str) else raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        ev = json.loads(payload)
        t = ev.get("type", "")
        if t == "response.output_text.delta":
            chunks.append(ev.get("delta", ""))
        elif t == "response.completed":
            completed = True
            u = (ev.get("response") or {}).get("usage") or {}
            if usage_out is not None and u:
                usage_out.update({"input_tokens": int(u.get("input_tokens", 0) or 0),
                                  "output_tokens": int(u.get("output_tokens", 0) or 0)})
        elif t in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev.get("error") or {}
            code = err.get("code", "unknown_error")
            if code in USAGE_LIMIT_CODES:
                raise LLMError("ChatGPT 구독 사용 한도에 도달했습니다. 잠시 후 다시 시도하거나 "
                               "ChatGPT 설정에서 앱 사용량을 확인하세요.")
            raise LLMError(f"ChatGPT 응답 실패: {code} {err.get('message', '')}")
        elif t == "response.incomplete":
            raise LLMError("ChatGPT 응답이 중간에 끊겼습니다 (incomplete).")
    if not completed:
        raise LLMError("스트림이 response.completed 없이 끝났습니다.")
    return "".join(chunks)


class ChatGPTPlanProvider(LLMProvider):
    name = "chatgpt_plan"

    def __init__(self, model: str = "", session: ChatGPTSession | None = None,
                 http: httpx.Client | None = None):
        self.session = session or ChatGPTSession()
        self.http = http or httpx.Client(timeout=httpx.Timeout(120, connect=20))
        if not self.session.is_logged_in():
            raise LLMError("ChatGPT 로그인이 필요합니다: python main.py chatgpt-login")
        if not model:  # 비어 있으면 계정이 쓸 수 있는 첫 번째 모델
            models = list_models(self.session, self.http)
            if not models:
                raise LLMError("이 ChatGPT 계정으로 쓸 수 있는 모델이 없습니다.")
            model = models[0]["slug"]
        super().__init__(model)

    def _post(self, body: dict, token: str) -> str:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        with self.http.stream("POST", RESPONSES_URL, json=body, headers=headers) as r:
            if r.status_code == 401:
                raise ChatGPTAuthError("401")
            if r.status_code == 429:
                r.read()
                raise LLMError(f"ChatGPT 구독 사용 한도/속도 제한 (429): {r.text[:200]}")
            if r.status_code >= 400:
                r.read()
                raise LLMError(f"ChatGPT 요청 실패 ({r.status_code}): {r.text[:300]}")
            self.last_usage = {}
            return parse_sse_text(r.iter_lines(), self.last_usage)

    def complete_json(self, system: str, user: str, max_tokens: int = 1500, schema: dict | None = None) -> dict:
        if schema:   # MailDesk 와 같이 Responses API 의 text.format(json_schema, strict) 로 형식 강제
            try:
                return parse_json_loose(self._complete(system, user, {"format": {
                    "type": "json_schema", "name": "item_assessment", "strict": True, "schema": schema}}))
            except LLMError as e:
                if "400" not in str(e):
                    raise
        return parse_json_loose(self._complete(system, user, None))

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return self._complete(system, user, None)

    def _complete(self, system: str, user: str, text_format: dict | None) -> str:
        body = {
            "model": self.model,
            "instructions": system,                       # system 메시지 대신
            "input": [{"role": "user", "content": user}],
            "store": False,
            "stream": True,
            # max_output_tokens / temperature 는 이 경로에서 지원 안 됨 → 보내지 않음
        }
        if self.reasoning != "default":
            body["reasoning"] = {"effort": self.reasoning}   # MailDesk 도 같은 경로에서 사용 (모델에 따라 무시될 수 있음)
        if text_format:
            body["text"] = text_format
        try:
            return self._post(body, self.session.access_token())
        except ChatGPTAuthError as e:
            if str(e) != "401":
                raise LLMError(str(e)) from e
            return self._post(body, self.session.access_token(force_refresh=True))  # 1회 재시도

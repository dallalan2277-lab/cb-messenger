"""구독 연결부 테스트 (네트워크 없이 가짜 서버로 검증):  python -m pytest -q"""
import base64
import hashlib
import json
import os
import stat
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError
from app.llm import chatgpt_auth as ca
from app.llm.chatgpt_plan_provider import ChatGPTPlanProvider, parse_sse_text


# ---------- PKCE / URL / 콜백 ----------
def test_pkce():
    v, c = ca.make_pkce()
    assert "=" not in c and 43 <= len(v) <= 128
    expect = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert c == expect


def test_authorize_url_first_time_and_reauth():
    first = ca.build_authorize_url(client_id=None, host_id="urn:uuid:x", redirect_uri="http://127.0.0.1:1455/auth/callback",
                                   state="s", nonce="n", code_challenge="c")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(first).query)
    assert q["client_id"] == ["dynamic_agent_client"] and q["agent_name_hint"] == ["cb-messenger-ai"]
    assert q["scope"] == [ca.SCOPES] and q["resource"] == ["https://api.openai.com/v1"]
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["http://127.0.0.1:1455/auth/callback"]
    assert "%20" in first  # 공백은 %20 으로 인코딩

    again = ca.build_authorize_url(client_id="oaiapp_1", host_id="urn:uuid:x", redirect_uri="r",
                                   state="s", nonce="n", code_challenge="c", login_hint="a@b.c")
    q2 = urllib.parse.parse_qs(urllib.parse.urlparse(again).query)
    assert q2["client_id"] == ["oaiapp_1"] and "agent_name_hint" not in q2 and q2["login_hint"] == ["a@b.c"]


def test_validate_callback():
    assert ca.validate_callback({"state": "s", "code": "c", "client_id": "oaiapp_9"},
                                expected_state="s", expected_client_id=None) == ("c", "oaiapp_9")
    with pytest.raises(ca.ChatGPTAuthError):
        ca.validate_callback({"state": "x", "code": "c"}, expected_state="s", expected_client_id=None)
    with pytest.raises(ca.ChatGPTAuthError):   # 첫 등록인데 client_id 없음
        ca.validate_callback({"state": "s", "code": "c"}, expected_state="s", expected_client_id=None)
    with pytest.raises(ca.ChatGPTAuthError):   # 다른 client_id
        ca.validate_callback({"state": "s", "code": "c", "client_id": "other"},
                             expected_state="s", expected_client_id="oaiapp_1")
    with pytest.raises(ca.ChatGPTAuthError):   # 거부
        ca.validate_callback({"state": "s", "error": "access_denied"}, expected_state="s", expected_client_id=None)


# ---------- 가짜 OpenAI 서버 ----------
class FakeOpenAI:
    def __init__(self):
        self.token_calls = []
        self.response_bodies = []
        self.n = 0
        self.fail_401_once = False
        self.sse = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth/token"):
            form = dict(urllib.parse.parse_qsl(req.content.decode()))
            self.token_calls.append(form)
            self.n += 1
            return httpx.Response(200, json={
                "access_token": f"at{self.n}", "refresh_token": f"rt{self.n}", "id_token": "idt",
                "token_type": "Bearer", "expires_in": 3600,
                "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"})
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [
                {"slug": "hidden", "visibility": "hide"}, {"slug": "gpt-x", "display_name": "GPT X", "visibility": "list"}]})
        if req.url.path == "/v1/responses":
            if self.fail_401_once:
                self.fail_401_once = False
                return httpx.Response(401, text="expired")
            self.response_bodies.append((json.loads(req.content), req.headers["authorization"]))
            sse = self.sse or (
                'data: {"type":"response.output_text.delta","delta":"{\\"importance\\":"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"\\"긴급\\"}"}\n\n'
                'data: {"type":"response.completed","response":{}}\n\n')
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)


def _session(tmp_path, fake):
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    verifier = lambda tok, client_id, nonce: {"sub": "user-1", "email": "t@school.kr", "iss": "https://auth.openai.com"}
    return ca.ChatGPTSession(tmp_path, http=http, id_token_verifier=verifier), http


def _simulate_browser(url: str):
    """브라우저 대신: 사용자가 동의했다고 보고 콜백 URL 호출."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    cb = q["redirect_uri"] + "?" + urllib.parse.urlencode(
        {"code": "authcode", "state": q["state"], "client_id": "oaiapp_123",
         "scope": ca.SCOPES})
    threading.Thread(target=lambda: urllib.request.urlopen(cb).read(), daemon=True).start()


def test_login_refresh_and_inference(tmp_path):
    fake = FakeOpenAI()
    s, http = _session(tmp_path, fake)
    rec = s.login(open_browser=_simulate_browser, timeout=10)
    assert rec["client_id"] == "oaiapp_123" and rec["email"] == "t@school.kr"
    ex = fake.token_calls[0]
    assert ex["grant_type"] == "authorization_code" and ex["client_id"] == "oaiapp_123"
    assert ex["redirect_uri"].startswith("http://127.0.0.1:") and ex["redirect_uri"].endswith("/auth/callback")
    assert ex["resource"] == "https://api.openai.com/v1" and "code_verifier" in ex
    assert s.host_id().startswith("urn:uuid:") and s.is_logged_in()

    # 만료 임박 → 자동 갱신 (refresh 토큰 교체 확인)
    data = s.load(); data["saved_at"] = int(time.time()) - 3500; s._save(data)
    assert s.access_token() == "at2"
    rf = fake.token_calls[1]
    assert rf == {"grant_type": "refresh_token", "client_id": "oaiapp_123", "refresh_token": "rt1",
                  "resource": "https://api.openai.com/v1"}
    assert s.load()["refresh_token"] == "rt2"

    # 모델 자동 선택 + Responses 호출 형식
    p = ChatGPTPlanProvider(session=s, http=http)
    assert p.model == "gpt-x"
    out = p.complete_json("시스템", "사용자")
    assert out == {"importance": "긴급"}
    body, auth = fake.response_bodies[-1]
    assert auth == "Bearer at2"
    assert body["store"] is False and body["stream"] is True and body["instructions"] == "시스템"
    assert body["input"] == [{"role": "user", "content": "사용자"}]
    assert "temperature" not in body and "max_output_tokens" not in body

    # 401 → 강제 갱신 후 1회 재시도
    fake.fail_401_once = True
    p.complete("a", "b")
    assert fake.response_bodies[-1][1] == "Bearer at3"

    # 로그아웃 후에는 client_id 만 남음
    s.logout()
    assert not s.is_logged_in() and s.load()["client_id"] == "oaiapp_123"


def test_sse_errors():
    with pytest.raises(LLMError, match="한도"):
        parse_sse_text(['data: {"type":"response.output_text.delta","delta":"x"}',
                        'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}'])
    with pytest.raises(LLMError, match="completed"):
        parse_sse_text(['data: {"type":"response.output_text.delta","delta":"x"}'])


# ---------- Claude CLI ----------
@pytest.mark.skipif(sys.platform == "win32", reason="가짜 claude 스크립트는 유닉스 셸용")
def test_claude_cli_provider(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, os\n"
        "inp = sys.stdin.read()\n"
        "assert '-p' in sys.argv and '--system-prompt' in sys.argv\n"
        "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
        "print(json.dumps({'type':'result','is_error':False,'result':'```json\\n{\"summary\": \"'+inp+'\"}\\n```'}))\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-removed")
    from app.llm.claude_cli_provider import ClaudeCLIProvider
    assert ClaudeCLIProvider().complete_json("sys", "안녕") == {"summary": "안녕"}

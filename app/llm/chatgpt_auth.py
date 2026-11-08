"""Sign in with ChatGPT (오픈소스·로컬 앱용 ChatGPT 플랜 사용) 로그인/토큰 관리.

OpenAI 공식 문서 기준 (2026-11 확인):
  https://developers.openai.com/siwc/token-sharing-open-source/sign-in
  https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
  https://developers.openai.com/siwc/token-sharing-open-source/token-reference

흐름:
  1) 127.0.0.1 루프백 서버를 먼저 띄우고 브라우저로 authorize 페이지 열기 (PKCE S256)
     - 첫 로그인: client_id=dynamic_agent_client + agent_name_hint → 콜백으로 발급된 client_id(oaiapp_...) 수신
     - 재로그인: 저장된 client_id 사용
  2) 콜백의 code 를 token 엔드포인트에서 교환 (client secret 없음)
  3) ID 토큰 검증 (서명·iss·aud·exp·nonce) 후 자격 증명 저장
  4) access token 은 1시간짜리 → 만료 전 refresh_token 으로 갱신 (갱신 토큰은 매번 교체됨)

⚠ 실제 계정으로는 아직 검증하지 않았습니다. (HANDOFF.md 참고)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
OIDC_DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
REGISTRATION_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "cb-messenger-ai"          # 앱 이름 (항상 같은 값 사용)
CALLBACK_PATH = "/auth/callback"        # 경로는 절대 바꾸지 말 것 (포트는 바뀌어도 됨)
DEFAULT_PORT = 1455
REFRESH_MARGIN_SEC = 300                # 만료 5분 전 갱신
MANAGE_USAGE_URL = "https://chatgpt.com/#settings"


class ChatGPTAuthError(RuntimeError):
    pass


def default_store_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "cb-messenger-ai"


# ---------------- PKCE / 유틸 ----------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """(verifier, challenge) — challenge = base64url(SHA256(verifier)), 패딩 없음."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(*, client_id: str | None, host_id: str, redirect_uri: str,
                        state: str, nonce: str, code_challenge: str,
                        id_token_hint: str = "", login_hint: str = "") -> str:
    first_time = not client_id
    params = {
        "client_id": client_id or REGISTRATION_CLIENT_ID,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    if first_time:
        params["agent_name_hint"] = AGENT_NAME
    else:
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        if login_hint:
            params["login_hint"] = login_hint
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def validate_callback(params: dict[str, str], *, expected_state: str,
                      expected_client_id: str | None) -> tuple[str, str]:
    """콜백 파라미터 검증 → (code, client_id)."""
    if params.get("state") != expected_state:
        raise ChatGPTAuthError("state 불일치 — 로그인을 다시 시도하세요.")
    if params.get("error"):
        raise ChatGPTAuthError(f"로그인이 취소되었거나 거부되었습니다: {params['error']}")
    code = params.get("code")
    if not code:
        raise ChatGPTAuthError("콜백에 code 가 없습니다.")
    cb_client = params.get("client_id", "")
    if expected_client_id:
        if cb_client and cb_client != expected_client_id:
            raise ChatGPTAuthError("콜백의 client_id 가 저장된 값과 다릅니다.")
        return code, expected_client_id
    if not cb_client:
        raise ChatGPTAuthError("첫 등록인데 발급된 client_id 가 없습니다 (등록 미완료).")
    return code, cb_client


def has_plan_scope(scope_str: str) -> bool:
    return PLAN_SCOPE in (scope_str or "").split()


# ---------------- 루프백 콜백 서버 ----------------
class _CallbackServer:
    def __init__(self, port: int):
        self.params: dict[str, str] | None = None
        self.event = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                url = urllib.parse.urlparse(self.path)
                if url.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                outer.params = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                ok = "code" in outer.params
                body = ("<h2>로그인 완료</h2><p>이 창을 닫고 프로그램으로 돌아가세요.</p>" if ok
                        else "<h2>로그인 실패</h2><p>프로그램에서 다시 시도하세요.</p>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                outer.event.set()

            def log_message(self, *a):  # 조용히
                pass

        self.httpd = HTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{CALLBACK_PATH}"  # localhost 로 바꾸지 말 것

    def wait(self, timeout: float) -> dict[str, str]:
        if not self.event.wait(timeout):
            raise ChatGPTAuthError("로그인 시간이 초과되었습니다.")
        return self.params or {}


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


# ---------------- ID 토큰 검증 ----------------
def verify_id_token(id_token: str, *, client_id: str, nonce: str,
                    http: httpx.Client | None = None) -> dict:
    """OpenAI JWKS 로 서명 검증 + iss/aud/exp/nonce 확인. PyJWT[crypto] 필요."""
    try:
        import jwt  # PyJWT
    except ImportError as e:
        raise ChatGPTAuthError("pip install \"PyJWT[crypto]\" 가 필요합니다.") from e
    http = http or httpx.Client(timeout=20)
    try:  # 표준 OIDC 디스커버리 (⚠ 실제 주소 응답은 아직 미확인)
        conf = http.get(OIDC_DISCOVERY_URL).raise_for_status().json()
    except Exception:
        conf = {"jwks_uri": "https://auth.openai.com/.well-known/jwks.json",
                "issuer": "https://auth.openai.com"}
    jwks = jwt.PyJWKClient(conf["jwks_uri"])
    key = jwks.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(id_token, key, algorithms=["RS256", "ES256"], audience=client_id,
                        issuer=conf["issuer"], options={"require": ["exp", "iss", "aud", "sub"]})
    if claims.get("nonce") != nonce:
        raise ChatGPTAuthError("ID 토큰 nonce 불일치")
    return claims


# ---------------- 자격 증명 저장/갱신 ----------------
class ChatGPTSession:
    """로그인·저장·자동 갱신을 담당. access_token() 으로 유효한 토큰을 얻습니다."""

    def __init__(self, store_dir: Path | str | None = None, http: httpx.Client | None = None,
                 id_token_verifier=verify_id_token):
        self.dir = Path(store_dir) if store_dir else default_store_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cred_path = self.dir / "chatgpt_credentials.json"
        self.host_path = self.dir / "chatgpt_host_id.txt"
        self.http = http or httpx.Client(timeout=30)
        self._verify = id_token_verifier
        self._lock = threading.Lock()

    # ---- 저장 ----
    def host_id(self) -> str:
        """이 PC 고유의 불투명 ID (urn:uuid). 한 번 만들면 계속 재사용."""
        if self.host_path.exists():
            return self.host_path.read_text(encoding="utf-8").strip()
        hid = f"urn:uuid:{uuid.uuid4()}"
        self.host_path.write_text(hid, encoding="utf-8")
        return hid

    def load(self) -> dict | None:
        if not self.cred_path.exists():
            return None
        return json.loads(self.cred_path.read_text(encoding="utf-8"))

    def _save(self, rec: dict) -> None:
        tmp = self.cred_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.cred_path)  # 원자적 교체

    def logout(self) -> None:
        """자격 증명 삭제 (client_id 는 다음 로그인 때 재사용하기 위해 남김)."""
        rec = self.load()
        if rec:
            self._save({"client_id": rec.get("client_id", ""), "email": rec.get("email", "")})

    def is_logged_in(self) -> bool:
        rec = self.load()
        return bool(rec and rec.get("refresh_token") and has_plan_scope(rec.get("scopes", "")))

    # ---- 로그인 ----
    def login(self, open_browser=webbrowser.open, timeout: float = 300) -> dict:
        prev = self.load() or {}
        client_id = prev.get("client_id") or None
        verifier, challenge = make_pkce()
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        with _CallbackServer(_free_port(DEFAULT_PORT)) as srv:   # 브라우저 열기 전에 서버 먼저
            url = build_authorize_url(
                client_id=client_id, host_id=self.host_id(), redirect_uri=srv.redirect_uri,
                state=state, nonce=nonce, code_challenge=challenge,
                id_token_hint=prev.get("id_token", "") if prev.get("refresh_token") else "",
                login_hint=prev.get("email", "") if client_id else "",
            )
            open_browser(url)
            params = srv.wait(timeout)
            redirect_uri = srv.redirect_uri
        code, client_id = validate_callback(params, expected_state=state, expected_client_id=client_id)
        return self._exchange(code, verifier, redirect_uri, client_id, nonce)

    def _exchange(self, code: str, verifier: str, redirect_uri: str, client_id: str, nonce: str) -> dict:
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE,
        })
        if r.status_code != 200:
            raise ChatGPTAuthError(f"토큰 교환 실패 ({r.status_code}): {r.text[:300]} — 다시 로그인하세요.")
        tok = r.json()
        claims = self._verify(tok["id_token"], client_id=client_id, nonce=nonce)
        prev = self.load() or {}
        if prev.get("subject") and prev["subject"] != claims["sub"]:
            raise ChatGPTAuthError("이전과 다른 ChatGPT 계정입니다. 먼저 로그아웃하세요.")
        scopes = tok.get("scope", "")
        if not has_plan_scope(scopes):
            raise ChatGPTAuthError("ChatGPT 플랜 사용 권한이 허용되지 않았습니다 (Plus/Pro 필요).")
        rec = {
            "email": claims.get("email", ""), "issuer": claims.get("iss", ""),
            "subject": claims["sub"], "client_id": client_id, "ext_agent_host_id": self.host_id(),
            "id_token": tok["id_token"], "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token", ""), "token_type": tok.get("token_type", "Bearer"),
            "expires_in": tok.get("expires_in", 3600), "scopes": scopes, "saved_at": int(time.time()),
        }
        self._save(rec)
        return rec

    # ---- 갱신 ----
    def _refresh(self, rec: dict) -> dict:
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": rec["client_id"],
            "refresh_token": rec["refresh_token"], "resource": RESOURCE,   # scope 생략 = 기존 권한 유지
        })
        if r.status_code != 200:
            raise ChatGPTAuthError(f"토큰 갱신 실패 ({r.status_code}). 'chatgpt-login' 으로 다시 로그인하세요.")
        tok = r.json()
        # access/만료/권한/교체된 refresh 토큰을 한꺼번에 교체
        rec.update({
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token", rec["refresh_token"]),
            "expires_in": tok.get("expires_in", 3600),
            "scopes": tok.get("scope", rec.get("scopes", "")),
            "saved_at": int(time.time()),
        })
        if tok.get("id_token"):
            rec["id_token"] = tok["id_token"]
        self._save(rec)
        return rec

    def access_token(self, force_refresh: bool = False) -> str:
        with self._lock:  # 같은 세션의 동시 갱신 방지 (refresh 토큰이 교체되므로)
            rec = self.load()
            if not rec or not rec.get("refresh_token"):
                raise ChatGPTAuthError("ChatGPT 로그인이 필요합니다: python main.py chatgpt-login")
            expires_at = rec["saved_at"] + int(rec.get("expires_in", 3600))
            if force_refresh or time.time() > expires_at - REFRESH_MARGIN_SEC:
                rec = self._refresh(rec)
            if not has_plan_scope(rec.get("scopes", "")):
                raise ChatGPTAuthError("ChatGPT 플랜 사용 권한이 없습니다. 다시 로그인하세요.")
            return rec["access_token"]

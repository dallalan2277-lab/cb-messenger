"""Claude 구독(Pro/Max)으로 호출하는 제공자 — 공식 Claude Code CLI 의 `claude -p` 를 실행.

Anthropic 정책상 구독 로그인 토큰을 꺼내 직접 API 를 부르는 것은 금지지만,
본인 PC에서 정식 Claude Code 프로그램을 본인 구독으로 실행하는 것은 허용됩니다.
이 제공자는 토큰을 건드리지 않고 `claude` 명령만 실행합니다.

준비 (Windows PowerShell):
  irm https://claude.ai/install.ps1 | iex      # Claude Code 설치
  claude                                       # 한 번 실행해서 구독 계정으로 로그인 후 종료
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile

from pathlib import Path

from .base import LLMProvider, LLMError, parse_json_loose


def find_claude() -> str | None:
    """claude 명령 위치. PATH 에 없으면 공식 설치 위치도 찾아봄
    (설치 직후엔 이미 켜져 있던 프로그램의 PATH 에 새 폴더가 아직 안 들어 있음)."""
    exe = shutil.which("claude")
    if exe:
        return exe
    home = Path.home()
    appdata = os.environ.get("APPDATA", "")
    for p in (home / ".local" / "bin" / "claude.exe",          # 공식 설치 (install.ps1)
              home / ".local" / "bin" / "claude",
              Path(appdata) / "npm" / "claude.cmd" if appdata else None):  # npm 설치
        if p is not None and p.is_file():
            return str(p)
    return None


class ClaudeCLIProvider(LLMProvider):
    name = "claude_cli"
    supports_images = True

    def __init__(self, model: str = "", timeout_sec: int = 180):
        super().__init__(model or "(claude code 기본)")
        self._model_arg = model
        self.timeout = timeout_sec
        self.exe = find_claude()
        if not self.exe:
            raise LLMError("Claude Code 가 설치돼 있지 않습니다 (README 'Claude 구독으로 쓰기' 참고).")
        self._workdir = tempfile.mkdtemp(prefix="cbmsg-claude-")  # 빈 폴더에서 실행 (다른 파일 접근 방지)

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        cmd = [self.exe, "-p", "--output-format", "json", "--system-prompt", system,
               "--max-turns", "1"]
        if self._model_arg:
            cmd += ["--model", self._model_arg]
        env = dict(os.environ)
        # API 키가 환경에 있으면 Claude Code 가 구독 대신 API 키로 과금하므로 제거
        env.pop("ANTHROPIC_API_KEY", None)
        try:
            p = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                               timeout=self.timeout, cwd=self._workdir, env=env,
                               # 창 없는 캘린더에서 부를 때 검은 창이 번쩍이지 않게
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise LLMError("Claude Code 응답 시간이 초과되었습니다.") from e
        if p.returncode != 0 and not p.stdout.strip():
            raise LLMError(f"Claude Code 실행 실패: {p.stderr.strip()[:300]} (로그인 상태를 확인하세요: claude)")
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.stdout  # 구버전 등 텍스트 출력
        u = out.get("usage") or {}
        self.last_usage = {"input_tokens": int(u.get("input_tokens", 0) or 0)
                           + int(u.get("cache_read_input_tokens", 0) or 0)
                           + int(u.get("cache_creation_input_tokens", 0) or 0),
                           "output_tokens": int(u.get("output_tokens", 0) or 0)}
        if out.get("is_error"):
            self._raise_result_error(out)
        return str(out.get("result", ""))

    @staticmethod
    def _raise_result_error(out: dict) -> None:
        msg = str(out.get("result", ""))
        if "not logged in" in msg.lower() or "/login" in msg:
            raise LLMError("Claude Code 에 로그인돼 있지 않습니다. 명령 프롬프트에서 claude 를 한 번 실행해 로그인하세요.")
        raise LLMError(f"Claude Code 오류: {msg[:300]}")

    def complete_json_image(self, system: str, user: str, image_bytes: bytes,
                            mime: str, schema: dict | None = None) -> dict:
        cmd = [self.exe, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose", "--max-turns", "1",
               "--system-prompt", system]
        if self._model_arg:
            cmd += ["--model", self._model_arg]
        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": mime,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }},
                    {"type": "text", "text": user},
                ],
            },
        }
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        try:
            p = subprocess.run(
                cmd, input=json.dumps(payload, ensure_ascii=False) + "\n",
                capture_output=True, text=True, encoding="utf-8",
                timeout=self.timeout, cwd=self._workdir, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError("Claude Code 응답 시간이 초과되었습니다.") from e
        if p.returncode != 0 and not p.stdout.strip():
            raise LLMError(f"Claude Code 실행 실패: {p.stderr.strip()[:300]} (로그인 상태를 확인하세요: claude)")

        result = None
        for line in p.stdout.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        if result is None:
            raise LLMError("Claude Code 응답에서 결과를 찾지 못했습니다.")

        u = result.get("usage") or {}
        self.last_usage = {
            "input_tokens": int(u.get("input_tokens", 0) or 0)
            + int(u.get("cache_read_input_tokens", 0) or 0)
            + int(u.get("cache_creation_input_tokens", 0) or 0),
            "output_tokens": int(u.get("output_tokens", 0) or 0),
        }
        if result.get("is_error"):
            self._raise_result_error(result)
        return parse_json_loose(str(result.get("result", "")))


# ───────── 설정 화면용: 설치·로그인 상태 확인, 로그인 창 띄우기 ─────────
# 공식 Claude Code 명령(`claude auth status` / `claude auth login`)만 씁니다.
# 로그인 정보(토큰) 파일을 이 프로그램이 직접 읽거나 쓰지 않습니다 (Anthropic 약관).
INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex"   # Anthropic 공식 설치 명령 (Windows PowerShell)


def _windows() -> bool:
    return os.name == "nt"


def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # 구독 로그인 상태를 봐야 하므로 (complete() 와 같은 조건)
    return env


def claude_status(timeout: float = 20, run=subprocess.run) -> dict:
    """{installed, logged_in, email, plan, error} — `claude auth status` 의 결과를 옮겨 적음."""
    out = {"installed": False, "logged_in": False, "email": "", "plan": "", "error": ""}
    exe = find_claude()
    if not exe:
        return out
    out["installed"] = True
    try:
        p = run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, env=_clean_env(), stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        out["error"] = "Claude Code 가 응답하지 않습니다. 잠시 후 [다시 확인]을 눌러 보세요."
        return out
    except OSError as e:
        out["error"] = f"Claude Code 를 실행하지 못했습니다: {e}"
        return out
    try:
        data = json.loads(p.stdout or "")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        # 옛 버전(auth 명령 없음) 등 — 로그인 여부를 알 수 없음
        out["error"] = "로그인 상태를 확인하지 못했습니다. Claude Code 를 최신 버전으로 설치해 보세요."
        return out
    out["logged_in"] = bool(data.get("loggedIn"))
    if out["logged_in"]:
        out["email"] = str(data.get("email") or "")[:200]
        out["plan"] = str(data.get("subscriptionType") or "")[:40]
    return out


def open_claude_login(popen=subprocess.Popen) -> None:
    """새 콘솔 창에서 `claude auth login` 을 띄움 — 로그인은 사용자가 그 창·브라우저에서 직접 함."""
    if not _windows():
        raise LLMError("Claude 로그인 창은 Windows 에서만 띄울 수 있습니다. 터미널에서 claude auth login 을 실행하세요.")
    exe = find_claude()
    if not exe:
        raise LLMError("Claude Code 가 설치돼 있지 않습니다. 먼저 설치하세요.")
    try:
        popen([exe, "auth", "login"], env=_clean_env(),
              creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as e:
        raise LLMError(f"Claude 로그인 창을 열지 못했습니다: {e}") from e


def open_claude_install(popen=subprocess.Popen) -> None:
    """새 PowerShell 창에서 공식 설치 명령을 실행 — 진행 상황이 사용자에게 그대로 보임."""
    if not _windows():
        raise LLMError("자동 설치는 Windows 에서만 됩니다. https://claude.com/claude-code 의 안내를 따르세요.")
    try:
        popen(["powershell", "-NoProfile", "-NoExit", "-Command",
               "Write-Host 'Claude Code 를 설치합니다. 파일이 커서 몇 분 걸릴 수 있습니다 (화면이 멈춘 것처럼 보여도 기다리세요).'; "
               + INSTALL_COMMAND
               + "; Write-Host ''; Write-Host '끝났습니다. 이 창을 닫고 설정 화면에서 [다시 확인]을 누르세요.'"],
              creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as e:
        raise LLMError(f"설치 창을 열지 못했습니다: {e}") from e

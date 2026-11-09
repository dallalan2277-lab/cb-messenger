"""Claude 구독(Pro/Max)으로 호출하는 제공자 — 공식 Claude Code CLI 의 `claude -p` 를 실행.

Anthropic 정책상 구독 로그인 토큰을 꺼내 직접 API 를 부르는 것은 금지지만,
본인 PC에서 정식 Claude Code 프로그램을 본인 구독으로 실행하는 것은 허용됩니다.
이 제공자는 토큰을 건드리지 않고 `claude` 명령만 실행합니다.

준비 (Windows PowerShell):
  irm https://claude.ai/install.ps1 | iex      # Claude Code 설치
  claude                                       # 한 번 실행해서 구독 계정으로 로그인 후 종료
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from pathlib import Path

from .base import LLMProvider, LLMError


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
            msg = str(out.get("result", ""))
            if "not logged in" in msg.lower() or "/login" in msg:
                raise LLMError("Claude Code 에 로그인돼 있지 않습니다. 명령 프롬프트에서 claude 를 한 번 실행해 로그인하세요.")
            raise LLMError(f"Claude Code 오류: {msg[:300]}")
        return str(out.get("result", ""))

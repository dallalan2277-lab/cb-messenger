"""설정 화면(settings.html)의 데이터 API — 화면이 window.pywebview.api.* 로 부릅니다.

tkinter 설정 창(gui.py SettingsDialog)과 같은 값을 읽고 씁니다 (.env 는 save_env, 메일 비밀번호는 keyring).
창 조작(폴더 고르기·닫기)은 _window(pywebview 창)가 있을 때만 하고, 나머지는 Windows 없이도 테스트할 수 있습니다.

지키는 것:
- 비밀번호·토큰은 돌려주지도, 로그에 남기지도 않음. 메일 비밀번호는 keyring 에만 저장.
- Claude 구독은 공식 Claude Code 명령(auth status / auth login)만 실행. 로그인 정보 파일을 직접 읽지 않음.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import __version__

REASONING_LABELS = {"default": "기본값", "low": "낮음", "medium": "중간", "high": "높음"}
MODEL_HINTS = {
    "chatgpt_plan": [],
    "claude_cli": ["haiku", "sonnet", "opus"],
    "openai": ["gpt-5-mini", "gpt-5"],
    "claude": ["claude-haiku-5-5", "claude-sonnet-5-5"],
    "fake": [],
}
MAIL_INTERVALS = ("1", "2", "5", "10")
_MODEL_RE = re.compile(r"[A-Za-z0-9._\-\[\]]{1,80}")


def _b(v) -> str:
    return "true" if v else "false"


def _one_line(v, limit: int = 500) -> str:
    """.env 한 줄에 들어갈 값 — 줄바꿈을 없애 다른 설정 줄을 끼워 넣지 못하게."""
    return " ".join(str(v or "").split())[:limit]


def _path_line(v, limit: int = 500) -> str:
    """폴더 경로 — 이름 안의 공백은 그대로 두고(연속 공백도 다른 폴더임) 줄바꿈만 없앰."""
    return "".join(ch for ch in str(v or "") if ch not in "\r\n").strip()[:limit]


def _comma_lines(v, limit: int = 1000) -> str:
    """여러 줄 입력 → '가, 나, 다' 한 줄 (tkinter 설정 창과 같은 규칙)."""
    parts = [" ".join(x.split()) for x in str(v or "").splitlines()]
    return ", ".join(x for x in parts if x)[:limit]


class SettingsAPI:
    _CLAUDE_TTL = 30     # Claude 상태는 30초 동안 다시 묻지 않음 (화면이 자주 불러도 프로세스를 계속 띄우지 않게)
    _MODELS_TTL = 600

    def __init__(self, prefs_path: str | Path, on_saved: Callable[[], dict] | None = None,
                 usage_fn: Callable[[str], list[dict]] | None = None, in_calendar: bool = False):
        # pywebview 는 공개 속성을 화면에 노출하므로 내부 값은 모두 _ 로 시작
        self._prefs_path = Path(prefs_path)
        self._on_saved = on_saved          # 저장 뒤 부름 (캘린더: 감시 다시 시작). {'ok': bool} 를 돌려줌
        self._usage_fn = usage_fn
        self._in_calendar = bool(in_calendar)
        self._window = None                # pywebview 창 (settings_window 가 넣어 줌)
        self._gpt_lock = threading.Lock()
        self._gpt = {"busy": False, "error": ""}
        self._claude_cache = None          # (시각, 결과)
        self._models_cache = None          # (시각, options)
        self._models_lock = threading.Lock()   # 모델 목록을 동시에 두 번 묻지 않게 (토큰 갱신이 겹치면 로그인이 깨질 수 있음)

    # ---------- 읽기 ----------
    def _theme(self) -> str:
        try:
            p = json.loads(self._prefs_path.read_text(encoding="utf-8"))
            return "light" if p.get("theme") == "light" else "dark"
        except (OSError, ValueError, AttributeError):
            return "dark"

    def load(self) -> dict:
        from ..config import REASONING_LEVELS, get_settings
        from ..extensions.archive import DRIVE_SUBDIR, find_gdrive_root
        from ..llm.factory import PROVIDERS, PROVIDER_LABELS
        from ..notify_policy import SCOPE_LABELS
        s = get_settings(reload=True)
        root = find_gdrive_root()
        gdrive_hint = ("비워 두면 자동: " + str(Path(root) / DRIVE_SUBDIR)) if root else (
            "드라이브 폴더를 자동으로 찾지 못했습니다. Google Drive 데스크톱을 설치하거나 폴더를 직접 고르세요.")
        interval = f"{s.mail_interval_min:g}"
        return {
            "version": __version__, "theme": self._theme(), "in_calendar": self._in_calendar,
            "providers": [{"id": p, "label": PROVIDER_LABELS[p]} for p in PROVIDERS],
            "reasoning_levels": [{"id": lv, "label": REASONING_LABELS.get(lv, lv)} for lv in REASONING_LEVELS],
            "model_hints": MODEL_HINTS,
            "models": {"analyze": s.analyze_model, "digest": s.digest_model, "fallback": s.fallback_model,
                       "reasoning": s.reasoning if s.reasoning in REASONING_LEVELS else "default"},
            "skip": {"keywords": ",".join(s.skip_ai_keywords), "min_chars": int(s.skip_ai_min_chars)},
            "archive": {"local_on": s.archive_local_enabled, "local_dir": s.archive_local_dir,
                        "gdrive_on": s.archive_gdrive_enabled, "gdrive_dir": s.archive_gdrive_dir,
                        "gdrive_hint": gdrive_hint, "gdrive_redact": s.archive_gdrive_redact,
                        "format": s.archive_format if s.archive_format in ("txt", "md") else "txt",
                        "summary_on": s.archive_include_summary},
            "memo": {"enabled": s.memo_enabled,
                     "read_mode": s.memo_read_mode if s.memo_read_mode in ("ocr", "image") else "ocr"},
            "mail": {"on": s.mail_enabled, "account": s.mail_account,
                     "interval": interval if interval in MAIL_INTERVALS else "2",
                     "password_saved": self._mail_saved(s.mail_account)},
            "notify": {"on": s.notify_enabled,
                       "scope": s.notify_scope if s.notify_scope in SCOPE_LABELS else "school",
                       "scopes": [{"id": k, "label": v} for k, v in SCOPE_LABELS.items()],
                       "high_only": s.notify_high_only,
                       "eztalk": "eztalk" in s.notify_sources, "mail": "mail" in s.notify_sources,
                       "sound": s.notify_sound, "preview": s.notify_preview, "quiet": s.notify_quiet,
                       "quiet_start": s.notify_quiet_start, "quiet_end": s.notify_quiet_end},
            "school": {"name": s.school_name, "duties": s.school_duties, "senders": s.school_senders,
                       "role": s.user_role, "my_names": ", ".join(s.my_names)},
        }

    @staticmethod
    def _mail_saved(account: str) -> bool:
        if not account:
            return False
        try:
            from ..secrets_store import load_password
            return bool(load_password(account))
        except Exception:  # noqa: BLE001  (keyring 을 못 쓰는 환경)
            return False

    # ---------- 저장 ----------
    @staticmethod
    def _spec(value, allow_empty: bool = False) -> str:
        """'제공자' 또는 '제공자:모델' 만 통과. 잘못되면 ValueError(한국어)."""
        from ..llm.factory import PROVIDERS, parse_spec
        value = str(value or "").strip()
        if not value:
            if allow_empty:
                return ""
            raise ValueError("분석 모델을 선택하세요.")
        provider, model = parse_spec(value)
        if provider not in PROVIDERS:
            raise ValueError(f"알 수 없는 AI 제공자입니다: {provider}")
        if model and not _MODEL_RE.fullmatch(model):
            raise ValueError("모델 이름에는 영문·숫자·점·밑줄·하이픈만 쓸 수 있습니다.")
        return f"{provider}:{model}" if model else provider

    def save(self, data: dict) -> dict:
        from ..config import REASONING_LEVELS, save_env
        from ..notify_policy import validate
        d = data if isinstance(data, dict) else {}
        models, skip = d.get("models") or {}, d.get("skip") or {}
        arc, memo, mail = d.get("archive") or {}, d.get("memo") or {}, d.get("mail") or {}
        noti, school = d.get("notify") or {}, d.get("school") or {}
        try:
            analyze = self._spec(models.get("analyze"))
            digest = self._spec(models.get("digest"), allow_empty=True) or analyze
            fallback = self._spec(models.get("fallback"), allow_empty=True)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        scope = str(noti.get("scope") or "")
        qs, qe = str(noti.get("quiet_start") or "").strip(), str(noti.get("quiet_end") or "").strip()
        # 방해금지를 끈 상태면 시각이 비어 있어도 저장되게 (tkinter 설정 창과 같은 규칙)
        err = validate(scope, qs, qe) if noti.get("quiet") else validate(scope, "22:00", "08:00")
        if err:
            return {"ok": False, "error": err}

        account = str(mail.get("account") or "").strip().lower()
        if account or mail.get("on"):
            try:
                from ..collectors.naver_mail import MailError, check_account
                account = check_account(account)
            except MailError as e:
                return {"ok": False, "error": str(e)}
        interval = str(mail.get("interval") or "2")
        if interval not in MAIL_INTERVALS:
            interval = "2"

        try:
            min_chars = max(0, min(200, int(skip.get("min_chars") or 0)))
        except (TypeError, ValueError):
            min_chars = 0
        reasoning = str(models.get("reasoning") or "default")
        fmt = str(arc.get("format") or "txt")
        memo_read_mode = str(memo.get("read_mode") or "ocr").strip().lower()
        updates = {
            "AI_ANALYZE": analyze, "AI_DIGEST": digest, "AI_FALLBACK": fallback,
            "AI_REASONING": reasoning if reasoning in REASONING_LEVELS else "default",
            "SKIP_AI_KEYWORDS": ",".join(k.strip() for k in _one_line(skip.get("keywords"), 1000).split(",") if k.strip()),
            "SKIP_AI_MIN_CHARS": str(min_chars),
            "ARCHIVE_LOCAL": _b(arc.get("local_on")), "ARCHIVE_LOCAL_DIR": _path_line(arc.get("local_dir")),
            "ARCHIVE_GDRIVE": _b(arc.get("gdrive_on")), "ARCHIVE_GDRIVE_DIR": _path_line(arc.get("gdrive_dir")),
            "ARCHIVE_GDRIVE_REDACT": _b(arc.get("gdrive_redact")),
            "ARCHIVE_FORMAT": fmt if fmt in ("txt", "md") else "txt",
            "ARCHIVE_SUMMARY": _b(arc.get("summary_on")),
            "MEMO_ENABLED": _b(memo.get("enabled", True)),
            "MEMO_READ_MODE": memo_read_mode if memo_read_mode in ("ocr", "image") else "ocr",
            "MAIL_ENABLED": _b(mail.get("on")), "MAIL_ACCOUNT": account, "MAIL_INTERVAL_MIN": interval,
            "NOTIFY_ENABLED": _b(noti.get("on")), "NOTIFY_SCOPE": scope,
            "NOTIFY_HIGH_ONLY": _b(noti.get("high_only")),
            "NOTIFY_SOURCES": ",".join(k for k in ("eztalk", "mail") if noti.get(k)),
            "NOTIFY_SOUND": _b(noti.get("sound")), "NOTIFY_PREVIEW": _b(noti.get("preview")),
            "NOTIFY_QUIET": _b(noti.get("quiet")),
            "SCHOOL_NAME": _one_line(school.get("name"), 200), "USER_ROLE": _one_line(school.get("role"), 200),
            "MY_NAMES": ",".join(n.strip() for n in _one_line(school.get("my_names"), 200).split(",") if n.strip()),
            "SCHOOL_DUTIES": _comma_lines(school.get("duties")), "SCHOOL_SENDERS": _comma_lines(school.get("senders")),
        }
        if noti.get("quiet") or (qs and qe):   # 끈 상태에서 비워 둔 시각으로 기존 값을 지우지 않음
            updates.update({"NOTIFY_QUIET_START": qs, "NOTIFY_QUIET_END": qe})
        try:
            save_env(updates)
        except OSError as e:
            return {"ok": False, "error": f"설정 파일을 저장하지 못했습니다: {e}"}
        restarted, restart_error = self._after_save()
        return {"ok": True, "error": "", "restarted": restarted, "restart_error": restart_error}

    def _after_save(self) -> tuple[bool, str]:
        """저장 뒤 감시 다시 시작. (다시 시작했는지, 실패 이유) — 실패하면 감시가 멈춘 상태이므로 이유를 화면에 알림"""
        self._models_cache = None
        if not self._on_saved:
            return False, ""
        try:
            r = self._on_saved() or {}
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]
        return bool(r.get("ok")), str(r.get("error") or "")[:300]

    # ---------- ChatGPT 구독 ----------
    def chatgpt_status(self) -> dict:
        out = {"logged_in": False, "email": "", "busy": False, "error": ""}
        with self._gpt_lock:
            out["busy"], out["error"] = self._gpt["busy"], self._gpt["error"]
        try:
            from ..llm.chatgpt_auth import ChatGPTSession
            sess = ChatGPTSession()
            out["logged_in"] = sess.is_logged_in()
            if out["logged_in"]:
                out["email"] = str((sess.load() or {}).get("email") or "")
        except Exception as e:  # noqa: BLE001
            out["error"] = out["error"] or f"로그인 상태를 확인하지 못했습니다: {e}"
        return out

    def chatgpt_login(self) -> dict:
        """브라우저 로그인을 시작하고 바로 돌아옴 (끝났는지는 chatgpt_status 의 busy 로 확인)."""
        with self._gpt_lock:
            if self._gpt["busy"]:
                return {"ok": False, "error": "이미 로그인 중입니다. 브라우저 창을 확인하세요."}
            self._gpt.update(busy=True, error="")

        def work():
            err = ""
            try:
                from ..llm.chatgpt_auth import ChatGPTSession
                ChatGPTSession().login()
            except Exception as e:  # noqa: BLE001  (취소·시간 초과 포함)
                err = str(e) or "로그인하지 못했습니다."
            with self._gpt_lock:
                self._gpt.update(busy=False, error=err)
            self._models_cache = None
            if not err:
                self._after_save()   # 로그인 전이라 꺼져 있던 감시를 다시 켬

        threading.Thread(target=work, name="chatgpt-login", daemon=True).start()
        return {"ok": True, "error": ""}

    def chatgpt_logout(self) -> dict:
        try:
            from ..llm.chatgpt_auth import ChatGPTSession
            ChatGPTSession().logout()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        self._models_cache = None
        with self._gpt_lock:
            self._gpt["error"] = ""
        return {"ok": True, "error": ""}

    def chatgpt_models(self, refresh: bool = False) -> dict:
        with self._models_lock:   # 화면이 여러 칸에서 한꺼번에 불러도 실제 조회는 한 번
            return self._chatgpt_models(refresh)

    def _chatgpt_models(self, refresh: bool) -> dict:
        cache = self._models_cache
        if cache and not refresh and time.time() - cache[0] < self._MODELS_TTL:
            return {"options": cache[1], "error": ""}
        try:
            from ..llm.chatgpt_auth import ChatGPTSession
            from ..llm.chatgpt_plan_provider import list_models
            sess = ChatGPTSession()
            if not sess.is_logged_in():
                return {"options": [], "error": "ChatGPT 로그인이 필요합니다."}
            options = [{"slug": str(m.get("slug", "")), "display_name": str(m.get("display_name", ""))}
                       for m in list_models(sess)]
        except Exception as e:  # noqa: BLE001
            return {"options": [], "error": f"모델 목록을 불러오지 못했습니다: {e}"}
        self._models_cache = (time.time(), options)
        return {"options": options, "error": ""}

    # ---------- Claude 구독 (공식 Claude Code) ----------
    def claude_status(self, refresh: bool = False) -> dict:
        cache = self._claude_cache
        if cache and not refresh and time.time() - cache[0] < self._CLAUDE_TTL:
            return dict(cache[1])
        from ..llm.claude_cli_provider import claude_status
        out = claude_status()
        self._claude_cache = (time.time(), dict(out))
        return out

    def claude_login(self) -> dict:
        from ..llm.base import LLMError
        from ..llm.claude_cli_provider import open_claude_login
        try:
            open_claude_login()
        except LLMError as e:
            return {"ok": False, "error": str(e)}
        self._claude_cache = None
        return {"ok": True, "error": ""}

    def claude_install(self) -> dict:
        from ..llm.base import LLMError
        from ..llm.claude_cli_provider import open_claude_install
        try:
            open_claude_install()
        except LLMError as e:
            return {"ok": False, "error": str(e)}
        self._claude_cache = None
        return {"ok": True, "error": ""}

    # ---------- 사용량 ----------
    def usage(self) -> dict:
        from ..llm.factory import PROVIDER_LABELS
        since = datetime.now().strftime("%Y-%m-01")
        purpose_labels = {"analyze": "분석", "digest": "브리핑", "memo": "메모"}
        rows = []
        try:
            raw = self._usage_fn(since) if self._usage_fn else []
        except Exception:  # noqa: BLE001
            raw = []
        for r in raw:
            purpose = str(r.get("purpose") or "")
            rows.append({"purpose": purpose_labels.get(purpose, purpose),
                         "provider": PROVIDER_LABELS.get(r.get("provider"), str(r.get("provider") or "")),
                         "model": str(r.get("model") or ""), "calls": int(r.get("calls") or 0),
                         "inp": int(r.get("inp") or 0), "outp": int(r.get("outp") or 0)})
        return {"since": since, "rows": rows}

    # ---------- 메일 (비밀번호는 keyring 에만) ----------
    def mail_connect(self, account: str = "", password: str = "") -> dict:
        from ..collectors.naver_mail import MailError, check_account, verify_login
        from ..config import save_env
        from ..secrets_store import SecretStoreError, save_password
        password = str(password or "").strip()
        try:
            acc = check_account(str(account or ""))
            if not password:
                return {"ok": False, "error": "애플리케이션 비밀번호를 입력하세요."}
            verify_login(acc, password)
            save_password(acc, password)
            save_env({"MAIL_ENABLED": "true", "MAIL_ACCOUNT": acc})
        except (MailError, SecretStoreError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"설정 파일을 저장하지 못했습니다: {e}"}
        restarted, restart_error = self._after_save()
        return {"ok": True, "error": "", "account": acc, "restarted": restarted, "restart_error": restart_error}

    def mail_disconnect(self, account: str = "") -> dict:
        from ..config import save_env
        from ..secrets_store import delete_password
        acc = str(account or "").strip().lower()
        try:
            if acc:
                delete_password(acc)
            save_env({"MAIL_ENABLED": "false"})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        self._after_save()
        return {"ok": True, "error": ""}

    # ---------- 그 밖 ----------
    def notify_test(self, sound: bool = False) -> dict:
        try:
            from ..notifier import notify
            notify("[테스트] 충북메신저 AI 비서", "알림이 이렇게 보입니다.", sound=bool(sound))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"알림을 보내지 못했습니다: {e}"}
        return {"ok": True, "error": ""}

    def pick_folder(self, current: str = "") -> dict:
        w = self._window
        if w is None:
            return {"path": ""}
        try:
            import webview
            start = str(current or "")
            got = w.create_file_dialog(webview.FOLDER_DIALOG, directory=start if Path(start).is_dir() else "")
        except Exception:  # noqa: BLE001
            return {"path": ""}
        return {"path": str(got[0]) if got else ""}

    def open_test_screen(self) -> dict:
        """예전 tkinter 테스트 화면(메시지 붙여 넣어 분석해 보기)"""
        import subprocess
        from ..paths import ROOT, self_command
        try:
            subprocess.Popen(self_command("gui"), cwd=str(ROOT))
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "error": ""}

    def close(self) -> dict:
        w = self._window
        if w is not None:
            threading.Timer(0.05, w.destroy).start()   # 응답을 돌려준 뒤 닫음
        return {"ok": True}

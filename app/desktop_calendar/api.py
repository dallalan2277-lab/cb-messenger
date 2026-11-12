"""바탕화면 캘린더의 데이터 API — 화면(HTML/JS)이 window.pywebview.api.* 로 부릅니다.

창(위젯) 조작은 host(widget.py)가 넣어 주고, 이 파일은 데이터만 다룹니다 (Windows 없이도 테스트 가능).
"""
from __future__ import annotations

import calendar as _cal
import base64
import binascii
import functools
import io
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import __version__
from .. import memo as memo_service
from .. import ocr
from ..config import get_settings
from ..llm.factory import create_provider
from ..paths import DATA_DIR, ROOT, self_command
from ..redact import redact
from ..storage import Storage

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
IMAGE_DATA_RE = re.compile(
    r"^data:image/(png|jpeg|jpg|webp|bmp|gif);base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
IMAGE_LIMIT = 10 * 1024 * 1024
IMAGE_PREVIEW_LIMIT = 5 * 1024 * 1024
MEMO_DISABLED_ERROR = "메모 기능이 꺼져 있습니다 (설정에서 켤 수 있음)."
IMAGE_EXTENSIONS = {
    "png": ("png", "image/png"),
    "jpeg": ("jpg", "image/jpeg"),
    "jpg": ("jpg", "image/jpeg"),
    "webp": ("webp", "image/webp"),
    "bmp": ("bmp", "image/bmp"),
    "gif": ("gif", "image/gif"),
}


def load_holidays() -> dict[str, str]:
    """{'2026-01-01': '신정', ...}"""
    try:
        raw = json.loads((HERE / "holidays_kr.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for y, days in raw.items():
        if y.isdigit() and isinstance(days, dict):
            for md, name in days.items():
                out[f"{y}-{md}"] = name
    return out


DEFAULT_PREFS = {
    "monitor": 0,            # 0 = 주 모니터, 1 = 두 번째 모니터
    "fraction": 0.66,        # 모니터 작업 영역 대비 크기 (처음 한 번)
    "anchor": "right",       # 처음 위치: right | left | center
    "x": None, "y": None, "w": None, "h": None,   # 사용자가 옮기거나 크기 바꾸면 여기에 기억
    "opacity": 0.92,
    "week_start": 0,         # 0 = 일요일, 1 = 월요일
    "show_suggested": True,
    "theme": "dark",         # dark | light
    "calendar_layout": "fit", # fit | expanded | custom(주 사이 경계를 끌어서 맞춤)
    "calendar_lines": 6,       # expanded 일 때 칸마다 보일 줄 수: 4 | 6 | 8
    "calendar_row_px": 0,      # custom 일 때 한 주(줄)의 높이(px, 40~600). 0 = 아직 안 정함
    "last_import": None,     # 마지막으로 파일에서 가져온 묶음 {batch, name, count, at} — 되돌리기용
}


def _locked(fn):
    @functools.wraps(fn)
    def wrap(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrap


class CalendarAPI:
    _UPDATE_TTL = 5 * 60 * 60   # 화면은 6시간마다 확인 → 캐시는 그보다 짧게 (매번 실제로 확인되도록)

    def __init__(self, storage: Storage, prefs_path: str | Path):
        self._storage = storage
        self._prefs_path = Path(prefs_path)
        self._holidays = load_holidays()
        self._host = None   # widget.Host (창 조작). 테스트 때는 None
        self._runner = None   # runner.Runner (쪽지·메일 감시). 없으면 None
        # pywebview 는 공개 속성을 화면에 노출하므로 내부 값은 모두 _ 로 시작 (노출 방지)
        self._lock = threading.RLock()   # 화면 호출이 여러 스레드에서 와도 DB 를 한 번에 하나씩
        self._update_cache = None        # (확인 시각, 화면 응답, UpdateInfo | None)
        self._update_info = None         # 설치할 최신 UpdateInfo
        self._update_install_lock = threading.Lock()
        self._update_installing = False
        self._memo_image_cache = None    # (시각, 분석 모델, 지원 여부, 이유)

    # ---------- 설정 ----------
    @_locked
    def get_prefs(self) -> dict:
        p = dict(DEFAULT_PREFS)
        try:
            p.update(json.loads(self._prefs_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return p

    @_locked
    def set_prefs(self, changes: dict) -> dict:
        p = self.get_prefs()
        changes = {k: v for k, v in (changes or {}).items() if k in DEFAULT_PREFS}
        if "calendar_layout" in changes and changes["calendar_layout"] not in ("fit", "expanded", "custom"):
            changes["calendar_layout"] = DEFAULT_PREFS["calendar_layout"]
        if ("calendar_lines" in changes
                and (not isinstance(changes["calendar_lines"], int)
                     or isinstance(changes["calendar_lines"], bool)
                     or changes["calendar_lines"] not in (4, 6, 8))):
            changes["calendar_lines"] = DEFAULT_PREFS["calendar_lines"]
        if "calendar_row_px" in changes:
            v = changes["calendar_row_px"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                changes["calendar_row_px"] = DEFAULT_PREFS["calendar_row_px"]
            else:
                changes["calendar_row_px"] = int(min(600, max(40, round(v))))
        p.update(changes)
        self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
        self._prefs_path.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    # ---------- 조회 ----------
    @_locked
    def month(self, year: int, month: int) -> dict:
        """달력 한 장(앞뒤 달 꼬리 포함 6주)의 날짜·일정·공휴일"""
        year, month = int(year), int(month)
        prefs = self.get_prefs()
        first_wd = 6 if prefs["week_start"] == 0 else 0       # calendar 모듈: 0=월 … 6=일
        weeks = _cal.Calendar(firstweekday=first_wd).monthdatescalendar(year, month)
        while len(weeks) < 6:
            last = weeks[-1][-1]
            weeks.append([last + timedelta(days=i) for i in range(1, 8)])
        start, end = weeks[0][0].isoformat(), weeks[-1][-1].isoformat()
        events = self._storage.calendar_events(start, end)
        if not prefs["show_suggested"]:
            events = [e for e in events if e["status"] != "suggested"]
        by_day: dict[str, list] = {}
        for e in events:
            by_day.setdefault(e["date"], []).append(_public(e))
        days = [[{"date": d.isoformat(), "day": d.day, "in_month": d.month == month,
                  "weekday": d.weekday(), "holiday": self._holidays.get(d.isoformat(), ""),
                  "events": by_day.get(d.isoformat(), [])} for d in w] for w in weeks]
        return {"year": year, "month": month, "today": date.today().isoformat(), "weeks": days,
                "suggested_count": sum(1 for e in events if e["status"] == "suggested")}

    @_locked
    def upcoming(self, days: int = 14) -> list[dict]:
        t = date.today()
        return [_public(e) for e in self._storage.calendar_events(t.isoformat(), (t + timedelta(days=days)).isoformat())]

    @_locked
    def suggestions(self) -> list[dict]:
        t = date.today() - timedelta(days=7)
        out = []
        for e in self._storage.calendar_events(t.isoformat(), "9999-12-31"):
            if e["status"] != "suggested":
                continue
            d = _public(e)
            if e.get("dup_of"):   # '기존 일정과 같은 일인가요?' 제안 → 비교할 기존 일정도 같이
                r = self._storage.conn.execute("SELECT * FROM calendar_events WHERE id=?", (e["dup_of"],)).fetchone()
                d["dup"] = _public(dict(r)) if r else None
                if not r:
                    d["dup_of"] = None
            out.append(d)
        return out

    @_locked
    def message(self, message_id: int) -> dict | None:
        m = self._storage.get_message(int(message_id))
        if not m:
            return None
        return {"sender": m["sender"], "body": m["body"], "received_at": m["received_at"], "source": m.get("source") or "",
                "summary": m.get("summary") or "", "importance": m.get("importance") or ""}

    # ---------- 받은 소식 (쪽지·메일 통합 목록) ----------
    @_locked
    def feed(self, filt: str = "open", source: str = "", limit: int = 80) -> list[dict]:
        """filt: open(처리 안 한 것, 중요도순) | school(학교 업무 전체·처리 완료 포함, 최신순) | all(전체, 최신순)
        — 모두 최근 30일. 처리 완료한 것은 화면에서 흐리게 보임."""
        since = (date.today() - timedelta(days=30)).isoformat()
        rows = self._storage.feed(source=source if source in ("mail", "eztalk") else "",
                                  only_open=filt == "open", school_only=filt == "school",
                                  limit=int(limit), since=since,
                                  order="importance" if filt == "open" else "recent")
        for r in rows:
            r["sender_short"] = re.sub(r"\s*<[^>]+>", "", r["sender"]).strip() or r["sender"]
        return rows

    @_locked
    def item(self, message_id: int) -> dict | None:
        """쪽지·메일 한 건: 원문 + 분석(요약·할 일·근거·학교 업무 판단)"""
        m = self._storage.get_message(int(message_id))
        if not m:
            return None
        keys = ("id", "sender", "subject", "body", "received_at", "source", "done", "summary", "importance", "category",
                "reason", "evidence", "deadline", "school_relevance", "school_reason", "school_evidence", "verified")
        out = {k: m.get(k) for k in keys}
        out["action_items"] = json.loads(m.get("action_items") or "[]")
        try:
            out["details"] = [str(x) for x in json.loads(m.get("details") or "[]")]
        except (TypeError, ValueError):
            out["details"] = []
        out["ai_model"] = f"{m.get('ai_provider') or ''}:{m.get('ai_model') or ''}".strip(":")
        out["done"] = bool(out["done"])
        out["events"] = [_public(e) for e in self._storage.calendar_events("0000-01-01", "9999-12-31", include_dismissed=False)
                         if e.get("message_id") == int(message_id)]
        return out

    def reanalyze(self, message_id: int) -> dict:
        """쪽지·메일 한 건을 지금 AI 설정으로 다시 분석 ('자세히' 가 없는 예전 분석 보강용).
        감시와 따로 새 연결(build_pipeline)로 돌려서, 감시 중인 AI 줄과 DB 연결을 같이 쓰지 않음."""
        from ..config import get_settings
        from ..pipeline import build_pipeline
        if not getattr(self, "_reanalyze_lock", None):
            self._reanalyze_lock = threading.Lock()
        if not self._reanalyze_lock.acquire(blocking=False):
            return {"ok": False, "error": "다른 분석이 진행 중입니다. 잠시 후 다시 눌러 주세요."}
        try:
            pipe = build_pipeline(get_settings(reload=True), notify_levels=())
            try:
                a = pipe.reanalyze(int(message_id))
            finally:
                pipe.storage.close()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001  (AI 한도·로그인 등)
            return {"ok": False, "error": f"다시 분석하지 못했습니다: {e}"[:400]}
        finally:
            self._reanalyze_lock.release()
        host = self._host
        if host is not None and hasattr(host, "refresh"):
            host.refresh()
        return {"ok": True, "model": f"{a.provider}:{a.model}"}

    @_locked
    def set_done(self, message_id: int, done: bool = True) -> dict:
        return {"ok": self._storage.set_done(int(message_id), bool(done))}

    def status(self) -> dict:
        r = self._runner
        st = dict(r.status) if r else {"messenger": "꺼짐", "mail": "꺼짐"}
        st["open_count"] = len(self.feed("open", limit=500))
        return st

    def open_settings(self) -> dict:
        """설정 창(AI 연결·모델·저장·메일·알림·학교 정보)을 띄움.
        캘린더 창 안에서 띄우면 저장하자마자 감시에 적용됨. 안 되면 따로 띄움(저장 뒤 '감시 다시 시작' 필요)."""
        if self._host is not None:
            try:
                from . import settings_window
                settings_window.open_window(self._prefs_path, on_saved=self._settings_saved,
                                            usage_fn=self._usage_summary, in_calendar=True)
                return {"ok": True, "inline": True}
            except Exception as e:  # noqa: BLE001
                log.warning("설정 창을 캘린더 안에서 띄우지 못해 따로 띄웁니다: %s", e)
        import subprocess
        subprocess.Popen(self_command("settings"), cwd=str(ROOT))
        return {"ok": True, "inline": False}

    def _settings_saved(self) -> dict:
        """설정 창에서 저장했을 때: 감시를 새 설정으로 다시 시작하고 화면을 다시 그림"""
        host = self._host
        if host is not None and hasattr(host, "settings_changed"):
            host.settings_changed()        # 메모 탭 보이기·기본 읽는 방식 등 화면 상태 다시 읽기
        r = self.restart_watch()
        host = self._host
        if host is not None and hasattr(host, "refresh"):
            host.refresh()
        return r

    @_locked
    def _usage_summary(self, since: str) -> list[dict]:
        return self._storage.usage_summary(since)

    def restart_watch(self) -> dict:
        """설정을 바꾼 뒤 쪽지·메일 감시를 새 설정으로 다시 시작"""
        from ..config import get_settings
        from ..runner import Runner
        old = self._runner
        on_change = old.on_change if old else getattr(self, "_watch_on_change", None)
        if not old and on_change is None:   # 감시 없이(--watch 없이) 켠 캘린더
            return {"ok": False}
        if old:
            old.stop()
        self._runner = None
        try:   # 처음 시작이 실패했던 경우(예: 로그인 전)에도 여기서 다시 켜짐
            self._runner = Runner(get_settings(reload=True), on_change=on_change).start()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def check_mail_now(self) -> dict:
        return {"ok": bool(self._runner and self._runner.check_mail_now())}

    # ---------- 앱 업데이트 ----------
    def _updater(self):
        """업데이터는 설치판에서만 필요하므로 필요할 때 불러온다."""
        from .. import updater
        return updater

    def update_check(self, force: bool = False) -> dict:
        """최신 설치판 정보를 확인한다. 네트워크 호출은 DB 잠금 밖에서 한다."""
        import time
        from ..config import get_settings

        settings = get_settings(reload=True)
        result = {
            "current": __version__,
            "available": False,
            "latest": __version__,
            "notes": "",
            "html_url": "",
            "error": "",
            "disabled": False,
        }
        if not settings.update_check and not force:
            result["disabled"] = True
            self._update_info = None
            return result

        cache = self._update_cache
        if cache and not force and time.time() - cache[0] < self._UPDATE_TTL:
            self._update_info = cache[2]
            return dict(cache[1])

        info = None
        updater = self._updater()
        try:
            info = updater.check_latest(settings.update_repo, __version__)
            if info is not None:
                result.update({
                    "available": True,
                    "latest": str(info.version),
                    "notes": str(info.notes or ""),
                    "html_url": str(info.html_url or ""),
                })
        except updater.UpdateError as e:
            result["error"] = str(e)

        self._update_info = info
        self._update_cache = (time.time(), dict(result), info)
        return result

    def update_install(self) -> dict:
        """최신 설치 파일을 받고 설치를 시작한 뒤 캘린더를 안전하게 종료한다."""
        with self._update_install_lock:
            if self._update_installing:
                return {"ok": False, "error": "이미 업데이트 중입니다."}
            self._update_installing = True

        started = False
        try:
            info = self._update_info
            if info is None:
                self.update_check(force=True)
                info = self._update_info
            if info is None:
                return {"ok": False, "error": "새 버전이 없습니다."}

            from .. import paths
            if not paths.FROZEN:
                return {"ok": False, "error": "개발 모드(파이썬으로 실행)에서는 자동 설치하지 않습니다. 설치판에서 사용하세요."}

            updater = self._updater()
            try:
                installer = updater.download(info)
                # 감시 없이(캘린더만) 켠 경우 업데이트 뒤에도 같은 모드로 다시 켜지게
                watching = self._runner is not None or getattr(self, "_watch_on_change", None) is not None
                updater.launch_installer(installer, watch=watching)
            except updater.UpdateError as e:
                return {"ok": False, "error": str(e)}

            def quit_host():
                host = self._host
                if host and hasattr(host, "quit"):
                    host.quit()

            timer = threading.Timer(1.5, quit_host)
            timer.daemon = True
            timer.start()
            started = True
            return {"ok": True, "version": info.version}
        finally:
            if not started:
                with self._update_install_lock:
                    self._update_installing = False

    # ---------- AI 모델 (메뉴에서 바로 고르기) ----------
    _MODELS_TTL = 600   # 모델 목록은 10분 동안 다시 묻지 않음

    def models(self, refresh: bool = False) -> dict:
        """현재 분석 모델 + ChatGPT 구독으로 쓸 수 있는 모델 목록.
        {current: 'chatgpt_plan:slug', provider, model, options: [{slug, display_name}], error}"""
        import time
        from ..config import get_settings
        from ..llm.factory import parse_spec
        s = get_settings(reload=True)
        provider, model = parse_spec(s.analyze_model)
        out = {"current": s.analyze_model, "provider": provider, "model": model, "options": [], "error": ""}
        cache = getattr(self, "_models_cache", None)
        if cache and not refresh and time.time() - cache[0] < self._MODELS_TTL:
            out["options"] = cache[1]
            return out
        try:
            from ..llm.chatgpt_auth import ChatGPTSession
            from ..llm.chatgpt_plan_provider import list_models
            sess = ChatGPTSession()
            if not sess.is_logged_in():
                out["error"] = "ChatGPT 로그인이 필요합니다 (2_ChatGPT로그인)."
                return out
            out["options"] = list_models(sess)
            self._models_cache = (time.time(), out["options"])
        except Exception as e:  # noqa: BLE001
            out["error"] = f"모델 목록을 불러오지 못했습니다: {e}"
        return out

    def set_model(self, slug: str = "") -> dict:
        """분석 모델을 ChatGPT 구독의 slug 로 바꿈 (빈 값 = 자동). .env 저장 후 감시를 새 설정으로 다시 시작."""
        from ..config import save_env
        slug = str(slug or "").strip()
        if slug and not re.fullmatch(r"[A-Za-z0-9._\-]{1,80}", slug):
            return {"ok": False, "error": "모델 이름이 올바르지 않습니다."}
        spec = f"chatgpt_plan:{slug}" if slug else "chatgpt_plan"
        save_env({"AI_ANALYZE": spec})
        r = self.restart_watch()
        return {"ok": True, "spec": spec, "restarted": bool(r.get("ok"))}

    # ---------- 메모 ----------
    @staticmethod
    def _memo_is_enabled(settings=None) -> bool:
        settings = settings or get_settings(reload=True)
        return bool(getattr(settings, "memo_enabled", True))

    def _memo_refresh(self) -> None:
        host = self._host
        if host is not None and hasattr(host, "refresh"):
            host.refresh()

    def _memo_record_usage(self, provider) -> None:
        """AI 응답 뒤 사용량을 남김. 기록 실패가 메모 정리를 망치지 않게 한다."""
        if provider is None:
            return
        usage = getattr(provider, "last_usage", None) or {}
        try:
            with self._lock:
                self._storage.add_usage(
                    "memo",
                    str(getattr(provider, "name", "") or ""),
                    str(getattr(provider, "model", "") or ""),
                    int(usage.get("input_tokens", 0) or 0),
                    int(usage.get("output_tokens", 0) or 0),
                )
        except Exception:  # noqa: BLE001  (사용량 기록은 부가 기능)
            log.warning("메모 AI 사용량을 기록하지 못했습니다.", exc_info=True)

    def memo_status(self) -> dict:
        """메모 기능과 이 PC/현재 AI의 이미지 읽기 가능 여부."""
        settings = get_settings(reload=True)
        mode = str(getattr(settings, "memo_read_mode", "ocr") or "ocr").lower()
        if mode not in ("ocr", "image"):
            mode = "ocr"
        try:
            can_ocr, ocr_reason = ocr.available()
        except Exception as e:  # noqa: BLE001
            can_ocr, ocr_reason = False, str(e)

        # 캘린더 첫 화면을 막지 않도록 AI 를 만들거나 인터넷에 묻지 않고, 설정의 제공자 이름만 봄
        # (로그인·한도 문제는 실제로 보낼 때 알려 줌)
        from ..llm.factory import parse_spec
        image_kinds = ("chatgpt_plan", "chatgpt", "claude_cli", "claude_code")
        names = [parse_spec(str(getattr(settings, key, "") or ""))[0] for key in ("analyze_model", "fallback_model")]
        image_ai = any(n in image_kinds for n in names if n)
        image_reason = "" if image_ai else "지금 분석 AI(ChatGPT 구독·Claude 구독이 아닌 연결)는 이미지를 받지 못합니다."
        return {
            "enabled": self._memo_is_enabled(settings),
            "default_mode": mode,
            "ocr_available": bool(can_ocr),
            "ocr_reason": str(ocr_reason or ""),
            "image_ai": bool(image_ai),
            "image_ai_reason": str(image_reason or ""),
            "kinds": list(memo_service.MEMO_KINDS),
        }

    def memo_ocr(self, image_data_url: str) -> dict:
        """이미지를 저장하지 않고 Windows 글자 인식만 실행."""
        if not self._memo_is_enabled():
            return {"ok": False, "text": "", "error": MEMO_DISABLED_ERROR}
        try:
            image_bytes, _mime, _ext = _decode_image_data_url(image_data_url)
            text = ocr.recognize_bytes(image_bytes)
        except (ValueError, ocr.OcrError) as e:
            return {"ok": False, "text": "", "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "text": "", "error": f"글자를 읽지 못했습니다: {e}"[:400]}
        return {"ok": True, "text": text, "error": ""}

    def memo_pick_image(self) -> dict:
        """이미지 파일 고르기 창을 열어 화면에 붙일 data URL을 돌려줌."""
        if not self._memo_is_enabled():
            return {"data_url": "", "error": MEMO_DISABLED_ERROR}
        host = self._host
        window = getattr(host, "window", None) if host else None
        if window is None:
            return {"data_url": "", "error": ""}
        try:
            import webview
            got = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("이미지 (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif)", "모든 파일 (*.*)"),
            )
            if not got:
                return {"data_url": "", "error": ""}
            path = Path(got[0])
            kind = path.suffix.lower().lstrip(".")
            if kind not in IMAGE_EXTENSIONS:
                raise ValueError("PNG, JPG, WEBP, BMP, GIF 이미지 파일만 고를 수 있습니다.")
            if path.stat().st_size > IMAGE_LIMIT:
                raise ValueError("이미지는 10MB 이하만 사용할 수 있습니다.")
            raw = path.read_bytes()
            if len(raw) > IMAGE_LIMIT:
                raise ValueError("이미지는 10MB 이하만 사용할 수 있습니다.")
            _ext, mime = IMAGE_EXTENSIONS[kind]
            data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            return {"data_url": data_url, "error": ""}
        except (OSError, ValueError) as e:
            return {"data_url": "", "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"data_url": "", "error": f"이미지 파일을 열지 못했습니다: {e}"[:400]}

    def memo_create(self, data: dict) -> dict:
        """글 또는 캡처를 메모로 저장하고 AI로 정리. AI 실패 때도 원본 메모는 남김."""
        settings = get_settings(reload=True)
        if not self._memo_is_enabled(settings):
            return {"ok": False, "error": MEMO_DISABLED_ERROR}
        data = data if isinstance(data, dict) else {}
        mode = str(data.get("mode") or getattr(settings, "memo_read_mode", "ocr") or "ocr").lower()
        if mode not in ("ocr", "image", "text"):
            return {"ok": False, "error": "읽는 방식을 다시 골라 주세요."}
        text = str(data.get("text") or "").strip()
        kind = str(data.get("kind") or "").strip()
        if kind and kind not in memo_service.MEMO_KINDS:
            return {"ok": False, "error": "메모 종류를 다시 골라 주세요."}

        image_bytes, mime, ext = None, "", ""
        image_data_url = str(data.get("image_data_url") or "")
        if image_data_url:
            try:
                image_bytes, mime, ext = _decode_image_data_url(image_data_url)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if mode == "image" and image_bytes is None:
            return {"ok": False, "error": "AI에 보낼 캡처 이미지를 넣어 주세요."}
        if mode != "image" and not text:
            return {"ok": False, "error": "정리할 글을 입력해 주세요."}

        now = datetime.now()
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        initial_title = (first_line[:30] if first_line else "새 메모")
        try:
            with self._lock:
                memo_id = self._storage.add_memo(
                    title=initial_title,
                    kind=kind or "기타",
                    kind_by_user=int(bool(kind)),
                    read_mode=mode,
                    source_text=text,
                    image_path="",
                    summary="",
                    details=[],
                    items=[],
                    action_items=[],
                    provider="",
                    model="",
                    status="ok",
                )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"메모를 저장하지 못했습니다: {e}"[:400]}

        image_path = ""
        if image_bytes is not None:
            try:
                base = Path(DATA_DIR).resolve()
                folder = base / "메모" / now.strftime("%Y-%m")
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"memo-{memo_id}-{now:%Y%m%d-%H%M%S}.{ext}"
                target.write_bytes(image_bytes)
                image_path = target.relative_to(base).as_posix()
                with self._lock:
                    self._storage.update_memo(memo_id, image_path=image_path)
            except Exception as e:  # noqa: BLE001
                try:
                    if image_path:
                        _safe_memo_path(image_path).unlink(missing_ok=True)
                    with self._lock:
                        self._storage.delete_memo(memo_id)
                except Exception:  # noqa: BLE001
                    pass
                return {"ok": False, "error": f"캡처 이미지를 저장하지 못했습니다: {e}"[:400]}

        provider = None
        try:
            provider = create_provider(settings, settings.analyze_model)
            redact_fn = (lambda value: redact(value, settings.redact_names)) if settings.redact_enabled else None
            result = memo_service.organize(
                provider,
                text=text,
                image=image_bytes if mode == "image" else None,
                mime=mime if mode == "image" else "",
                kind=kind,
                role=settings.user_role,
                now=now,
                redact_fn=redact_fn,
            )
        except Exception as e:  # noqa: BLE001  (AI 로그인·한도·응답 오류)
            reason = (str(e).strip() or "알 수 없는 오류")[:300]
            pname = str(getattr(provider, "name", "") or "")
            pmodel = str(getattr(provider, "model", "") or "")
            with self._lock:
                self._storage.update_memo(
                    memo_id,
                    summary=f"(AI 정리 실패: {reason})"[:1500],
                    provider=pname,
                    model=pmodel,
                    status="failed",
                )
            self._memo_record_usage(provider)
            self._memo_refresh()
            return {"ok": True, "error": "", "warning": reason, "memo": self.memo_get(memo_id)}

        self._memo_record_usage(provider)
        with self._lock:
            self._storage.update_memo(
                memo_id,
                title=result.title,
                kind=result.kind,
                summary=result.summary,
                details=result.details,
                items=result.items,
                action_items=result.action_items,
                provider=result.provider,
                model=result.model,
                status="ok",
            )
            for event in result.events:
                self._storage.suggest_event(
                    title=event.title,
                    date=event.date,
                    start_time=event.start_time,
                    end_time=event.end_time,
                    location=event.location,
                    note=event.note,
                    status="suggested",
                    origin="memo",
                    memo_id=memo_id,
                    source_text=text,
                    source_date=(self._storage.get_memo(memo_id).get("created_at") or "")[:10],
                )
        self._memo_refresh()
        return {"ok": True, "error": "", "memo": self.memo_get(memo_id)}

    def memo_list(self, limit: int = 100) -> list[dict]:
        try:
            limit = max(1, min(500, int(limit)))
        except (TypeError, ValueError):
            limit = 100
        with self._lock:
            rows = self._storage.list_memos(limit)
        return [{
            "id": r.get("id"),
            "title": r.get("title") or "",
            "kind": r.get("kind") or "기타",
            "summary": r.get("summary") or "",
            "created_at": r.get("created_at") or "",
            "has_image": bool(r.get("image_path")),
            "event_count": int(r.get("event_count") or 0),
            "status": r.get("status") or "ok",
        } for r in rows]

    def memo_get(self, memo_id: int) -> dict | None:
        try:
            memo_id = int(memo_id)
        except (TypeError, ValueError):
            return None
        with self._lock:
            row = self._storage.get_memo(memo_id)
            if not row:
                return None
            events = [
                _public(e)
                for e in self._storage.calendar_events("0000-01-01", "9999-12-31")
                if e.get("memo_id") == memo_id
            ]
        image_data_url = ""
        if row.get("image_path"):
            try:
                image_data_url = _memo_image_data_url(_safe_memo_path(row["image_path"]))
            except (OSError, ValueError):
                image_data_url = ""
        return {
            "id": row.get("id"),
            "title": row.get("title") or "",
            "kind": row.get("kind") or "기타",
            "kind_by_user": bool(row.get("kind_by_user")),
            "read_mode": row.get("read_mode") or "ocr",
            "source_text": row.get("source_text") or "",
            "image_data_url": image_data_url,
            "summary": row.get("summary") or "",
            "details": list(row.get("details") or []),
            "items": list(row.get("items") or []),
            "action_items": list(row.get("action_items") or []),
            "events": events,
            "provider": row.get("provider") or "",
            "model": row.get("model") or "",
            "status": row.get("status") or "ok",
            "created_at": row.get("created_at") or "",
        }

    def memo_update(self, memo_id: int, data: dict) -> dict:
        data = data if isinstance(data, dict) else {}
        changes = {}
        if "title" in data:
            title = str(data.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "제목을 입력해 주세요."}
            changes["title"] = title[:200]
        if "kind" in data:
            kind = str(data.get("kind") or "").strip()
            if kind not in memo_service.MEMO_KINDS:
                return {"ok": False, "error": "메모 종류를 다시 골라 주세요."}
            changes.update(kind=kind, kind_by_user=1)
        if "summary" in data:
            changes["summary"] = str(data.get("summary") or "").strip()[:1500]
        if "details" in data:
            if not isinstance(data.get("details"), list):
                return {"ok": False, "error": "자세한 내용의 형식이 올바르지 않습니다."}
            changes["details"] = [str(x).strip()[:300] for x in data["details"] if str(x).strip()][:8]
        if "source_text" in data:
            changes["source_text"] = str(data.get("source_text") or "")
        if not changes:
            return {"ok": False, "error": "바꿀 내용이 없습니다."}
        try:
            with self._lock:
                ok = self._storage.update_memo(int(memo_id), **changes)
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return {"ok": False, "error": "메모를 찾지 못했습니다."}
        self._memo_refresh()
        return {"ok": True, "error": "", "memo": self.memo_get(int(memo_id))}

    def memo_reanalyze(self, memo_id: int, kind: str = "") -> dict:
        settings = get_settings(reload=True)
        if not self._memo_is_enabled(settings):
            return {"ok": False, "error": MEMO_DISABLED_ERROR}
        kind = str(kind or "").strip()
        if kind and kind not in memo_service.MEMO_KINDS:
            return {"ok": False, "error": "메모 종류를 다시 골라 주세요."}
        try:
            memo_id = int(memo_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "메모를 찾지 못했습니다."}
        with self._lock:
            row = self._storage.get_memo(memo_id)
        if not row:
            return {"ok": False, "error": "메모를 찾지 못했습니다."}

        mode = row.get("read_mode") or "ocr"
        text = row.get("source_text") or ""
        image_bytes, mime = None, ""
        if mode == "image":
            try:
                path = _safe_memo_path(row.get("image_path") or "")
                image_bytes = path.read_bytes()
                mime = _mime_for_path(path)
            except (OSError, ValueError):
                return {"ok": False, "error": "저장된 캡처 이미지를 찾지 못했습니다."}
        elif not text.strip():
            return {"ok": False, "error": "다시 정리할 글이 없습니다."}

        forced_kind = kind or (row.get("kind") if row.get("kind_by_user") else "")
        provider = None
        try:
            provider = create_provider(settings, settings.analyze_model)
            redact_fn = (lambda value: redact(value, settings.redact_names)) if settings.redact_enabled else None
            result = memo_service.organize(
                provider,
                text=text,
                image=image_bytes,
                mime=mime,
                kind=forced_kind,
                role=settings.user_role,
                now=_memo_created_at(row),     # '내일' 같은 상대 날짜가 다시 정리할 때 바뀌지 않게
                redact_fn=redact_fn,
            )
        except Exception as e:  # noqa: BLE001
            self._memo_record_usage(provider)
            return {"ok": False, "error": f"다시 정리하지 못했습니다: {e}"[:400]}
        self._memo_record_usage(provider)

        changes = {
            "title": result.title,
            "kind": result.kind,
            "summary": result.summary,
            "details": result.details,
            "items": result.items,
            "action_items": result.action_items,
            "provider": result.provider,
            "model": result.model,
            "status": "ok",
        }
        if kind:
            changes["kind_by_user"] = 1
        with self._lock:
            self._storage.update_memo(memo_id, **changes)
            for event in result.events:
                self._storage.suggest_event(
                    title=event.title,
                    date=event.date,
                    start_time=event.start_time,
                    end_time=event.end_time,
                    location=event.location,
                    note=event.note,
                    status="suggested",
                    origin="memo",
                    memo_id=memo_id,
                    source_text=text,
                    source_date=(row.get("created_at") or "")[:10],
                )
        self._memo_refresh()
        return {"ok": True, "error": "", "memo": self.memo_get(memo_id)}

    def memo_to_event(self, memo_id: int, data: dict) -> dict:
        if not self._memo_is_enabled():
            return {"ok": False, "error": MEMO_DISABLED_ERROR}
        try:
            memo_id = int(memo_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "메모를 찾지 못했습니다."}
        with self._lock:
            row = self._storage.get_memo(memo_id)
        if not row:
            return {"ok": False, "error": "메모를 찾지 못했습니다."}
        data = data if isinstance(data, dict) else {}
        details = "\n".join(str(x) for x in (row.get("details") or []) if str(x).strip())
        note = "\n".join(x for x in (str(row.get("summary") or "").strip(), details) if x).strip()
        try:
            event = _clean({
                "title": data.get("title") or row.get("title") or "메모 일정",
                "date": data.get("date"),
                "start_time": data.get("start_time") or "",
                "end_time": data.get("end_time") or "",
                "location": "",
                "note": note,
            })
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        with self._lock:
            result = self._storage.add_event_from_user(event, memo_id=memo_id)
        self._memo_refresh()
        return {"ok": True, "error": "", **result}

    def memo_delete(self, memo_id: int) -> dict:
        try:
            memo_id = int(memo_id)
        except (TypeError, ValueError):
            return {"ok": False}
        with self._lock:
            row = self._storage.delete_memo(memo_id)
        if not row:
            return {"ok": False}
        image_path = row.get("image_path") or ""
        if image_path:
            try:
                _safe_memo_path(image_path).unlink(missing_ok=True)
            except (OSError, ValueError):
                log.warning("지운 메모의 이미지 파일을 삭제하지 못했습니다: %s", image_path)
        self._memo_refresh()
        return {"ok": True}

    # ---------- 변경 ----------
    @_locked
    def add(self, data: dict) -> dict:
        d = _clean(data)
        eid = self._storage.add_calendar_event(**d, status="confirmed", origin="manual")
        return {"ok": True, "id": eid}

    @_locked
    def update(self, event_id: int, data: dict) -> dict:
        d = _clean(data, partial=True)
        return {"ok": self._storage.update_calendar_event(int(event_id), **d)}

    @_locked
    def delete(self, event_id: int) -> dict:
        return {"ok": self._storage.delete_calendar_event(int(event_id))}

    @_locked
    def confirm(self, event_id: int) -> dict:
        event_id = int(event_id)
        row = self._storage.conn.execute("SELECT dup_of, dup_kind FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        if row and row["dup_kind"] == "cancel":
            return {"ok": False, "error": "취소 안내는 [일정 취소] 또는 [그대로 두기]로 골라 주세요."}
        if row and row["dup_of"] is not None:
            return self._storage.resolve_duplicate(event_id, "separate")
        return {"ok": self._storage.update_calendar_event(event_id, status="confirmed")}

    @_locked
    def resolve_duplicate(self, event_id: int, action: str) -> dict:
        return self._storage.resolve_duplicate(int(event_id), action)

    @_locked
    def restore_event(self, event_id: int, status: str = "confirmed") -> dict:
        return {"ok": self._storage.restore_event(int(event_id), str(status or ""))}

    @_locked
    def event_sources(self, event_id: int) -> list[dict]:
        return self._storage.event_sources(int(event_id))

    @_locked
    def dismiss(self, event_id: int) -> dict:
        return {"ok": self._storage.update_calendar_event(int(event_id), status="dismissed")}

    @_locked
    def confirm_all(self) -> dict:
        n = skipped = 0
        for e in self.suggestions():
            if e["dup_of"] is not None and self._storage.conn.execute(
                    "SELECT 1 FROM calendar_events WHERE id=?", (e["dup_of"],)).fetchone():
                skipped += 1
            elif self._storage.update_calendar_event(e["id"], status="confirmed"):
                n += 1
        return {"ok": True, "count": n, "skipped": skipped}

    # ---------- 다른 캘린더 파일에서 일정 가져오기 ----------
    _IMPORT_TYPES = ("일정 파일 (*.ics;*.xlsx;*.csv;*.tsv;*.txt)", "모든 파일 (*.*)")

    def import_pick(self) -> dict:
        """파일 고르기 창 → {path} (취소하면 빈 값)"""
        host = self._host
        window = getattr(host, "window", None) if host else None
        if window is None:
            return {"path": ""}
        try:
            import webview
            got = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=self._IMPORT_TYPES)
        except Exception as e:  # noqa: BLE001
            return {"path": "", "error": f"파일 고르기 창을 열지 못했습니다: {e}"}
        return {"path": str(got[0]) if got else ""}

    def import_preview(self, path: str, mapping: dict | None = None) -> dict:
        """파일을 읽어 넣을 일정 목록을 미리 보여 줌 (아직 저장 안 함).
        mapping: {칸 종류: 몇 번째 칸} — 표 파일에서 어느 칸이 날짜·제목인지 바꿀 때"""
        import secrets
        from ..extensions.calendar_import import FIELD_LABELS, ImportError_, parse_file
        try:
            clean = {k: int(v) for k, v in (mapping or {}).items() if k in FIELD_LABELS and v not in (None, "", -1)}
            res = parse_file(path, clean or None)
        except ImportError_ as e:
            return {"ok": False, "error": str(e)}
        except (OSError, ValueError, TypeError) as e:
            return {"ok": False, "error": f"파일을 읽지 못했습니다: {e}"}
        events = res["events"]
        have = set()
        if events:
            with self._lock:
                have = self._storage.existing_event_keys(min(e["date"] for e in events),
                                                         max(e["date"] for e in events))
        seen, rows = set(), []
        for i, e in enumerate(events):
            key = (e["date"], e["title"], e["start_time"])
            rows.append({**e, "idx": i, "dup": key in have or key in seen})
            seen.add(key)
        token = secrets.token_hex(8)
        self._import_cache = {"token": token, "events": events, "name": res["name"]}
        return {"ok": True, "token": token, "name": res["name"], "kind": res["kind"], "events": rows,
                "total": len(rows), "dup_count": sum(r["dup"] for r in rows), "warnings": res["warnings"],
                "columns": res["columns"], "mapping": res["mapping"], "field_labels": FIELD_LABELS}

    def import_apply(self, token: str, indexes: list | None = None) -> dict:
        """미리 본 일정 중 고른 것(indexes, 비우면 전부)을 확정 일정으로 넣음"""
        import secrets
        cache = getattr(self, "_import_cache", None)
        if not cache or cache.get("token") != token:
            return {"ok": False, "error": "미리보기가 지나갔습니다. 파일을 다시 골라 주세요."}
        events = cache["events"]
        if indexes is not None:
            pick = sorted({int(i) for i in indexes if str(i).isdigit() and int(i) < len(events)})
            events = [events[i] for i in pick]
        if not events:
            return {"ok": False, "error": "넣을 일정을 하나 이상 고르세요."}
        batch = "imp-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
        with self._lock:
            added, skipped = self._storage.import_calendar_events(events, batch)
        self._import_cache = None
        if added:
            self.set_prefs({"last_import": {"batch": batch, "name": cache["name"], "count": added,
                                            "at": datetime.now().strftime("%Y-%m-%d %H:%M")}})
        host = self._host
        if host is not None and hasattr(host, "refresh"):
            host.refresh()
        return {"ok": True, "added": added, "skipped": skipped, "batch": batch if added else ""}

    def import_undo(self, batch: str = "") -> dict:
        """가져온 일정 묶음을 지움 (비우면 마지막으로 가져온 것)"""
        last = self.get_prefs().get("last_import") or {}
        batch = str(batch or last.get("batch") or "")
        if not batch:
            return {"ok": False, "error": "되돌릴 가져오기가 없습니다."}
        with self._lock:
            removed = self._storage.delete_import_batch(batch)
        if last.get("batch") == batch:
            self.set_prefs({"last_import": None})
        host = self._host
        if host is not None and hasattr(host, "refresh"):
            host.refresh()
        return {"ok": True, "removed": removed}

    # ---------- 창 조작 (host 가 있을 때만) ----------
    def win(self, action: str, *args):
        """화면에서 부르는 창 조작: move_by(dx,dy) / resize_by(dw,dh) / save_geometry / snap(preset) /
        next_monitor / hide / quit"""
        if not self._host:
            return {"ok": False}
        fn = getattr(self._host, action, None)
        if not callable(fn) or action.startswith("_"):
            return {"ok": False}
        return {"ok": True, "result": fn(*args)}

    @_locked
    def export_ics(self) -> dict:
        from ..extensions.ics_export import export_ics
        from ..models import Event
        t = date.today() - timedelta(days=30)
        evs = [Event(title=e["title"], date=e["date"], start_time=e["start_time"], end_time=e["end_time"],
                     location=e["location"], note=e["note"])
               for e in self._storage.calendar_events(t.isoformat(), "9999-12-31") if e["status"] == "confirmed"]
        path = self._prefs_path.parent / f"바탕화면캘린더_{datetime.now():%Y%m%d_%H%M}.ics"
        export_ics(evs, path)
        return {"ok": True, "path": str(path), "count": len(evs)}


def _public(e: dict) -> dict:
    keys = ("id", "title", "date", "start_time", "end_time", "location", "note", "status", "origin", "message_id",
            "memo_id", "dup_of", "dup_kind")
    out = {k: e.get(k) for k in keys}
    out["sender"] = e.get("msg_sender") or ""
    return out


def _memo_created_at(row: dict) -> datetime:
    """메모를 만든 시각 (상대 날짜 기준). 읽을 수 없으면 지금."""
    try:
        return datetime.fromisoformat(str(row.get("created_at") or "").replace("T", " ")[:19])
    except ValueError:
        return datetime.now()


def _decode_image_data_url(value: str) -> tuple[bytes, str, str]:
    """허용한 이미지 data URL을 검사하고 (내용, MIME, 저장 확장자)로 바꿈."""
    if not isinstance(value, str):
        raise ValueError("이미지 형식이 올바르지 않습니다.")
    match = IMAGE_DATA_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("이미지 형식이 올바르지 않습니다. PNG, JPG, WEBP, BMP, GIF만 사용할 수 있습니다.")
    encoded = re.sub(r"\s+", "", match.group(2))
    # 큰 문자열을 먼저 전부 풀어 메모리를 쓰지 않도록 보수적으로 선검사한다.
    if len(encoded) > ((IMAGE_LIMIT + 2) // 3) * 4 + 4:
        raise ValueError("이미지는 10MB 이하만 사용할 수 있습니다.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("이미지 데이터가 올바르지 않습니다.") from e
    if not raw:
        raise ValueError("빈 이미지는 사용할 수 없습니다.")
    if len(raw) > IMAGE_LIMIT:
        raise ValueError("이미지는 10MB 이하만 사용할 수 있습니다.")
    ext, mime = IMAGE_EXTENSIONS[match.group(1).lower()]
    return raw, mime, ext


def _safe_memo_path(relative: str) -> Path:
    """DB의 상대 경로가 DATA_DIR 밖을 가리키지 못하게 확인."""
    if not relative:
        raise ValueError("이미지 경로가 비어 있습니다.")
    base = Path(DATA_DIR).resolve()
    path = (base / str(relative)).resolve()
    try:
        path.relative_to(base)
    except ValueError as e:
        raise ValueError("이미지 경로가 올바르지 않습니다.") from e
    return path


def _mime_for_path(path: Path) -> str:
    kind = path.suffix.lower().lstrip(".")
    return IMAGE_EXTENSIONS.get(kind, ("png", "image/png"))[1]


def _memo_image_data_url(path: Path) -> str:
    """저장 이미지를 data URL로 읽음. 5MB보다 크면 화면용 JPEG로 줄임."""
    raw = path.read_bytes()
    mime = _mime_for_path(path)
    if len(raw) > IMAGE_PREVIEW_LIMIT:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "L"):
                if "A" in image.getbands():
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            elif image.mode == "L":
                image = image.convert("RGB")
            quality = 88
            while True:
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=quality, optimize=True)
                raw = buf.getvalue()
                if len(raw) <= IMAGE_PREVIEW_LIMIT or quality <= 45:
                    break
                quality -= 10
            while len(raw) > IMAGE_PREVIEW_LIMIT and min(image.size) > 320:
                image.thumbnail((max(320, int(image.width * 0.8)), max(320, int(image.height * 0.8))),
                                Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=65, optimize=True)
                raw = buf.getvalue()
            mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _clean(data: dict, partial: bool = False) -> dict:
    data = data or {}
    out = {}
    if "title" in data or not partial:
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("제목을 입력하세요.")
        out["title"] = title[:200]
    if "date" in data or not partial:
        d = str(data.get("date", "")).strip()
        if not DATE_RE.match(d):
            raise ValueError("날짜 형식이 올바르지 않습니다 (YYYY-MM-DD).")
        datetime.strptime(d, "%Y-%m-%d")
        out["date"] = d
    for k in ("start_time", "end_time"):
        if k in data or not partial:
            t = str(data.get(k, "") or "").strip()
            if t and not TIME_RE.match(t):
                raise ValueError("시간 형식이 올바르지 않습니다 (HH:MM).")
            out[k] = t
    for k in ("location", "note"):
        if k in data or not partial:
            out[k] = str(data.get(k, "") or "").strip()[:2000]
    return out

"""바탕화면 캘린더의 데이터 API — 화면(HTML/JS)이 window.pywebview.api.* 로 부릅니다.

창(위젯) 조작은 host(widget.py)가 넣어 주고, 이 파일은 데이터만 다룹니다 (Windows 없이도 테스트 가능).
"""
from __future__ import annotations

import calendar as _cal
import functools
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import __version__
from ..paths import ROOT, self_command
from ..storage import Storage

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


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
        p.update({k: v for k, v in (changes or {}).items() if k in DEFAULT_PREFS})
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
        return [_public(e) for e in self._storage.calendar_events(t.isoformat(), "9999-12-31")
                if e["status"] == "suggested"]

    @_locked
    def message(self, message_id: int) -> dict | None:
        m = self._storage.get_message(int(message_id))
        if not m:
            return None
        return {"sender": m["sender"], "body": m["body"], "received_at": m["received_at"],
                "summary": m.get("summary") or "", "importance": m.get("importance") or ""}

    # ---------- 받은 소식 (쪽지·메일 통합 목록) ----------
    @_locked
    def feed(self, filt: str = "open", source: str = "", limit: int = 80) -> list[dict]:
        """filt: open(처리 안 한 것) | school(학교 업무, 처리 안 한 것) | all(처리 완료 포함, 최근 30일)"""
        since = (date.today() - timedelta(days=30)).isoformat()
        rows = self._storage.feed(source=source if source in ("mail", "eztalk") else "",
                                  only_open=filt != "all", school_only=filt == "school",
                                  limit=int(limit), since=since)
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
        out["done"] = bool(out["done"])
        out["events"] = [_public(e) for e in self._storage.calendar_events("0000-01-01", "9999-12-31", include_dismissed=False)
                         if e.get("message_id") == int(message_id)]
        return out

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
        return {"ok": self._storage.update_calendar_event(int(event_id), status="confirmed")}

    @_locked
    def dismiss(self, event_id: int) -> dict:
        return {"ok": self._storage.update_calendar_event(int(event_id), status="dismissed")}

    @_locked
    def confirm_all(self) -> dict:
        n = sum(1 for e in self.suggestions() if self._storage.update_calendar_event(e["id"], status="confirmed"))
        return {"ok": True, "count": n}

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
    keys = ("id", "title", "date", "start_time", "end_time", "location", "note", "status", "origin", "message_id")
    out = {k: e.get(k) for k in keys}
    out["sender"] = e.get("msg_sender") or ""
    return out


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

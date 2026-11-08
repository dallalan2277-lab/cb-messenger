"""한 프로그램 안에서 쪽지 감시 + 메일 확인 + AI 분석을 함께 돌리는 실행기.

수집기(쪽지·메일)는 각자 스레드에서 새 글을 모아 줄(queue)에 넣고,
AI 분석은 한 스레드가 차례로 처리합니다 (동시에 여러 번 부르지 않아 한도·순서 문제가 없음).
바탕화면 캘린더(widget.py)와 `python main.py watch` 가 이것을 씁니다.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Settings
from .notifier import notify
from .notify_policy import NotifyPrefs, in_quiet

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


def make_messenger_collector(s: Settings, on_unreadable=None):
    """설정(MESSENGER_COLLECTOR)에 맞는 쪽지 수집기. 기본은 소통메신저 창 읽기(eztalk)."""
    if s.messenger_collector == "notification":
        from .collectors.windows_notification import WindowsNotificationCollector
        return WindowsNotificationCollector(s.messenger_app_keywords)
    from .collectors.eztalk_collector import EztalkCollector
    return EztalkCollector(process=s.messenger_process, quiet_sec=s.messenger_quiet_sec,
                           on_unreadable=on_unreadable, unknown_log=str(ROOT / "data" / "messenger_unknown.log"))


def make_mail_collector(s: Settings, storage, on_error=None):
    """메일 설정이 켜져 있고 비밀번호가 저장돼 있으면 네이버 메일 수집기, 아니면 None (+이유)"""
    if not s.mail_enabled:
        return None, "메일 확인이 꺼져 있습니다."
    if not s.mail_account:
        return None, "메일 주소가 설정되지 않았습니다."
    from .secrets_store import load_password
    from .collectors.naver_mail import NaverMailCollector
    pw = load_password(s.mail_account)
    if not pw:
        return None, "저장된 앱 비밀번호가 없습니다. 메일 설정에서 다시 연결하세요."
    return NaverMailCollector(s.mail_account, pw, storage, interval_min=s.mail_interval_min, on_error=on_error), ""


class Runner:
    def __init__(self, settings: Settings, on_change: Callable[[], None] | None = None,
                 watch_messenger: bool = True, watch_mail: bool = True, pipeline=None):
        from .pipeline import build_pipeline
        self.s = settings
        self.pipe = pipeline or build_pipeline(settings)
        self.on_change = on_change or (lambda: None)
        self.q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.watch_messenger, self.watch_mail = watch_messenger, watch_mail
        self.mail = None
        self.prefs = NotifyPrefs.from_settings(settings)
        self.status = {"messenger": "꺼짐", "messenger_error": "", "mail": "꺼짐", "mail_error": "",
                       "mail_last_check": "", "ai_queue": 0, "ai_last_error": "", "processed": 0,
                       "ai_model": settings.analyze_model}
        self._warned: set[str] = set()

    # ---------- 알림 (오류·보안 쪽지) — 알림 규칙의 켜기/방해금지를 따름 ----------
    def _system_notice(self, title: str, body: str, once_key: str | None = None):
        if once_key:
            if once_key in self._warned:
                return
            self._warned.add(once_key)
        if self.prefs.enabled and not in_quiet(self.prefs, datetime.now()):
            notify(title, body, sound=False)

    def _unreadable(self, ev):
        who = ev.get("sender") or "보낸이 미상"
        self._system_notice(f"[확인 필요] 쪽지 · {who}" if self.prefs.preview else "[확인 필요] 쪽지",
                            "보안 쪽지이거나 내용을 읽지 못했습니다. 메신저에서 직접 확인하세요.")

    def _mail_error(self, msg: str):
        self.status["mail_error"] = msg
        self._system_notice("메일 확인 오류", msg, once_key=msg)

    # ---------- 스레드 ----------
    def start(self):
        if self.pipe.archiver:
            try:
                self.pipe.archiver.sync_pending()
            except Exception:
                pass
        threading.Thread(target=self._worker, name="ai-worker", daemon=True).start()
        if self.watch_messenger:
            threading.Thread(target=self._messenger_loop, name="messenger", daemon=True).start()
        if self.watch_mail:
            threading.Thread(target=self._mail_loop, name="mail", daemon=True).start()
        return self

    def stop(self):
        self.stop_flag.set()

    def _messenger_loop(self):
        try:
            import uiautomation as auto
            ctx = auto.UIAutomationInitializerInThread()
        except Exception as e:  # noqa: BLE001
            self.status.update(messenger="꺼짐", messenger_error=f"쪽지 감시를 켤 수 없습니다: {e}"[:200])
            return
        with ctx:
            try:
                col = make_messenger_collector(self.s, on_unreadable=self._unreadable)
            except Exception as e:  # noqa: BLE001
                self.status.update(messenger="꺼짐", messenger_error=str(e)[:200])
                return
            self.status["messenger"] = "감시 중"
            while not self.stop_flag.is_set():
                for m in col.collect_once():
                    self.q.put(m)
                time.sleep(self.s.poll_interval_sec)

    def _mail_loop(self):
        col, why = make_mail_collector(self.s, self.pipe.storage, on_error=self._mail_error)
        if not col:
            self.status.update(mail="꺼짐", mail_error=why if self.s.mail_enabled else "")
            return
        self.mail = col
        self.status["mail"] = f"{self.s.mail_interval_min:g}분마다 확인"
        while not self.stop_flag.is_set():
            try:
                got = list(col.collect_once())
            except Exception as e:  # noqa: BLE001  (수집기 안에서 대부분 처리됨)
                got = []
                self._mail_error(str(e)[:200])
            for m in got:
                self.q.put(m)
            if col.last_check:
                self.status["mail_last_check"] = datetime.fromtimestamp(col.last_check).strftime("%H:%M")
            if col.paused_reason:
                self.status["mail"] = "멈춤 (다시 연결 필요)"
            elif not col.last_error:
                self.status["mail_error"] = ""
            time.sleep(3)

    def _worker(self):
        last_retry = time.time()
        while not self.stop_flag.is_set():
            try:
                m = self.q.get(timeout=5)
            except queue.Empty:
                m = None
            self.status["ai_queue"] = self.q.qsize()
            if m is not None:
                try:
                    a = self.pipe.handle(m)
                    self.status["processed"] += 1
                    if a is None and m.id:
                        row = self.pipe.storage.get_message(m.id) or {}
                        if not row.get("summary"):
                            self.status["ai_last_error"] = "AI 분석 실패 (나중에 다시 시도)"
                    elif a is not None:
                        self.status["ai_last_error"] = ""
                    self.on_change()
                except Exception as e:  # noqa: BLE001
                    self.status["ai_last_error"] = str(e)[:200]
                    log.exception("처리 오류")
            if time.time() - last_retry > 600:     # 10분마다 분석 못 한 것 다시 시도 (네트워크가 돌아왔을 때)
                last_retry = time.time()
                try:
                    if self.pipe.retry_failed():
                        self.status["ai_last_error"] = ""
                        self.on_change()
                except Exception:
                    pass

    # ---------- 화면에서 ----------
    def check_mail_now(self) -> bool:
        if self.mail:
            self.mail.check_now()
            return True
        return False

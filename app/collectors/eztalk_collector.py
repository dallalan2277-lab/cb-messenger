"""충북소통메신저(eztalk.exe) 쪽지 수집기 — 창 글자를 읽기만 함 (클릭·입력·전송 없음).

Project Monday 의 messenger_watch.py Watcher 를 이 프로젝트의 Collector 형식으로 옮긴 것입니다.
- eztalk.exe 프로세스의 창만 봄 (다른 프로그램은 보지 않음)
- 새 쪽지 알림 창(TFrmAlarm)과 쪽지 보기 창(TFrmViewNote)이 뜨면 UI 자동화로 글자를 읽음
- 보안 쪽지·읽지 못한 쪽지는 내용을 쓰지 않고 '메신저에서 확인 필요' 알림만 띄움

준비: pip install uiautomation  (requirements.txt 에 포함, Windows 전용)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Callable, Iterable

from ..models import Message
from .base import Collector, OnMessage
from .eztalk_parse import Batch, is_reopened_old, parse_alarm, parse_view, tidy_list

log = logging.getLogger(__name__)


def available() -> tuple[bool, str]:
    """(사용 가능 여부, 안 되는 이유)"""
    if os.name != "nt":
        return False, "Windows 에서만 동작합니다."
    try:
        import uiautomation  # noqa: F401
    except Exception:
        return False, "uiautomation 라이브러리가 없습니다. 1_설치하기.bat 을 다시 실행하세요."
    return True, ""


def event_to_message(ev: dict) -> Message:
    body = ev["body"]
    if ev.get("title") and ev["title"] not in body:
        body = f"[제목] {ev['title']}\n{body}"
    return Message(
        sender=ev["sender"], body=body, room=ev.get("kind", "") or "쪽지",
        received_at=datetime.fromtimestamp(ev["ts"]).isoformat(timespec="seconds"),
        source="eztalk", external_id=ev["key"],
    )


class EztalkCollector(Collector):
    name = "eztalk"

    def __init__(self, process: str = "eztalk.exe", alarm_cls: str = "TFrmAlarm",
                 view_cls: str = "TFrmViewNote", quiet_sec: float = 5.0,
                 on_unreadable: Callable[[dict], None] | None = None,
                 unknown_log: str | None = None):
        ok, why = available()
        if not ok:
            raise RuntimeError(why)
        self.process = process.lower()
        self.alarm_cls, self.view_cls = alarm_cls, view_cls
        self.batch = Batch(quiet_sec=quiet_sec)
        self.on_unreadable = on_unreadable
        self.unknown_log = unknown_log
        self._unknown: set[str] = set()
        self._api()
        self.seen = set(self._messenger_windows())  # 시작할 때 이미 떠 있던 창은 새 쪽지가 아님
        self.vfail: dict[int, int] = {}
        self.vsig: dict[int, tuple] = {}   # 보기 창: 같은 창에서 다음 쪽지를 열면 내용만 바뀜 → 2초마다 확인
        self.sig: dict[int, tuple] = {}    # 알림 창: 떠 있는 동안 내용이 바뀌면 새 쪽지
        self.stats = {"polls": 0, "events": 0, "last_error": "", "messenger_seen_ts": 0.0, "classes": set(),
                      "old_skipped": 0}
        self.started = time.time()   # 이보다 먼저 보낸 쪽지를 보기 창으로 다시 열면 새 쪽지로 치지 않음

    # ---------- Windows API ----------
    def _api(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32, self.kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _str(self, fn, hwnd, n=256):
        buf = self.ct.create_unicode_buffer(n)
        fn(hwnd, buf, n)
        return buf.value

    def _process_name(self, hwnd):
        pid = self.wt.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        h = self.kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = self.ct.create_unicode_buffer(1024)
            size = self.wt.DWORD(1024)
            if self.kernel32.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            self.kernel32.CloseHandle(h)

    def _messenger_windows(self) -> dict[int, str]:
        found: dict[int, str] = {}

        def cb(hwnd, _):
            if self.user32.IsWindowVisible(hwnd):
                cls = self._str(self.user32.GetClassNameW, hwnd)
                if cls.startswith((self.alarm_cls, self.view_cls)):
                    if self._process_name(hwnd) == self.process:
                        found[int(hwnd)] = cls
                        self.stats["messenger_seen_ts"] = time.time()
                        self.stats["classes"].add(cls)
                elif cls.startswith("TFrm") and self._process_name(hwnd) == self.process:
                    self.stats["messenger_seen_ts"] = time.time()
                    self.stats["classes"].add(cls)
                    if cls not in self._unknown:
                        self._unknown.add(cls)
                        self._note_unknown(cls)
            return True

        self.user32.EnumWindows(self.PROC(cb), 0)
        return found

    def _note_unknown(self, cls):
        """처음 보는 메신저 창 종류의 이름만 기록 (내용은 기록하지 않음)"""
        if self.unknown_log:
            try:
                with open(self.unknown_log, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 새 창 종류: {cls}\n")
            except OSError:
                pass

    def read_texts(self, hwnd, limit=300) -> list[str]:
        """창 안의 글자를 문서 순서(깊이 우선)로. 본문이 담기는 Text/Custom/Edit 부품만"""
        import uiautomation as auto
        out, count = [], 0
        stack = [auto.ControlFromHandle(hwnd)]
        while stack and count < limit:
            ctl = stack.pop()
            count += 1
            try:
                kids = ctl.GetChildren()
                if ctl.ControlTypeName in ("TextControl", "CustomControl", "EditControl"):
                    name = ctl.Name or ""
                    if ctl.ControlTypeName == "EditControl" and not name:
                        try:
                            name = ctl.GetValuePattern().Value or ""
                        except Exception:
                            pass
                    if name:
                        out.append(name)
            except Exception:
                continue
            stack.extend(reversed(kids))
        return out

    # ---------- 한 번 살펴보기 ----------
    def _poll(self) -> list[dict]:
        """창을 한 번 훑어 새로 읽은 이벤트 목록을 돌려줌 (Monday 의 _loop 한 바퀴)"""
        events = []
        now = self._messenger_windows()
        for hwnd, cls in now.items():
            is_alarm = cls.startswith(self.alarm_cls)
            if hwnd in self.seen and not is_alarm:
                old = self.vsig.get(hwnd)
                if old is None:
                    self.vsig[hwnd] = ((), time.time())   # 시작 때부터 떠 있던 창은 기준만 잡음
                    continue
                if time.time() - old[1] < 2:
                    continue
                texts = self.read_texts(hwnd, 300)
                key = tuple(tidy_list(texts))
                self.vsig[hwnd] = (key if key else old[0], time.time())
                if not key or key == old[0] or not old[0]:
                    continue
                ev = parse_view(texts)
                if not ev["ok"] and not ev["secure"] and self.vfail.get(hwnd, 0) < 3:
                    self.vfail[hwnd] = self.vfail.get(hwnd, 0) + 1   # 내용이 덜 채워졌을 수 있어 다시 읽음
                    self.vsig[hwnd] = (old[0], 0)
                    continue
                self.vfail.pop(hwnd, None)
                events.append(ev)
                continue
            if hwnd in self.seen and not (is_alarm and hwnd in self.sig):
                continue
            if hwnd not in self.seen:
                time.sleep(0.4)   # 내용이 채워질 시간
            texts = self.read_texts(hwnd, 80 if is_alarm else 300)
            if is_alarm:
                key = tuple(tidy_list(texts))
                if not key or self.sig.get(hwnd) == key:
                    continue
                self.sig[hwnd] = key
                ev = parse_alarm(texts)
            else:
                ev = parse_view(texts)
                self.vsig[hwnd] = (tuple(tidy_list(texts)), time.time())
            events.append(ev)
        for d in (self.sig, self.vsig):
            for h in list(d):
                if h not in now:
                    del d[h]
        for hwnd, cls in now.items():   # 이미 떠 있던 알림 창도 내용 변화를 계속 살핌
            if cls.startswith(self.alarm_cls) and hwnd not in self.sig and hwnd in self.seen:
                self.sig[hwnd] = ()
        self.seen = set(now)
        return events

    def collect_once(self) -> Iterable[Message]:
        self.stats["polls"] += 1
        try:
            for ev in self._poll():
                self.stats["events"] += 1
                if is_reopened_old(ev, self.started):
                    self.stats["old_skipped"] += 1   # 감시 전에 받은 쪽지를 다시 열어 본 것 → 저장·분석 안 함
                    log.info("감시를 켜기 전에 받은 쪽지를 다시 열어 봐서 건너뜀")
                    continue
                self.batch.add(ev)
        except Exception as e:  # 한 번 실패해도 계속 감시
            self.stats["last_error"] = repr(e)[:200]
            log.warning("메신저 창 읽기 오류: %s", e)
        if not self.batch.ready():
            return
        for ev in self.batch.flush():
            if ev["ok"]:
                yield event_to_message(ev)
            elif self.on_unreadable:
                self.on_unreadable(ev)   # 보안 쪽지 등: 내용 없이 알림만

    def run_forever(self, on_message: OnMessage, interval_sec: float = 0.5) -> None:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():   # 스레드마다 COM 초기화 필요
            while True:
                for m in self.collect_once():
                    on_message(m)
                time.sleep(interval_sec)

    def snapshot(self) -> list[tuple[str, list[str], dict]]:
        """진단용: 지금 떠 있는 메신저 알림/보기 창의 (창 종류, 읽은 글, 파싱 결과)"""
        out = []
        for hwnd, cls in self._messenger_windows().items():
            texts = self.read_texts(hwnd, 300)
            ev = parse_alarm(texts) if cls.startswith(self.alarm_cls) else parse_view(texts)
            out.append((cls, tidy_list(texts), ev))
        return out

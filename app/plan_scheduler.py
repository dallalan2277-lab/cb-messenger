"""하루 2번(기본 08:30·12:40) '오늘 할 일 순서'를 정리하고 알림 — 캘린더 프로그램 안의 작은 스레드.

- 1분마다 확인: 아침 시각~점심 시각 전이면 아침 회차, 점심 시각~18시 전이면 점심 회차를 (아직 안 했으면) 한 번.
  PC 가 꺼져 있었으면 켤 때, 절전에서 깨어나면 그때 한 번 (오후에 켜면 점심 회차만).
- 같은 회차는 DB 의 실행권(Storage.claim_plan)으로 두 프로그램이 동시에 떠도 한 번만, 알림도 한 번만.
- 알림은 설정의 알림 끄기·방해금지를 따르고, 막힌 알림은 나중에 몰아서 보내지 않음.
- 쪽지·메일 감시(Runner)와 따로 돌아서, 감시를 꺼도 정리는 됨. DB 연결도 따로 씀.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Callable

from . import today_plan as tp

log = logging.getLogger(__name__)

DAY_END = "18:00"      # 점심 회차는 이 시각 전까지만 (저녁에 켜면 그날은 정리 안 함)
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def plan_times(settings) -> tuple[str, str]:
    times = [t.strip() for t in (getattr(settings, "plan_times", None) or []) if _TIME.fullmatch(t.strip())]
    morning = times[0] if times else "08:30"
    noon = times[1] if len(times) > 1 and times[1] > morning else "12:40"
    if noon <= morning:
        morning, noon = "08:30", "12:40"
    return morning, noon


def due_slot(settings, now: datetime, holidays: dict) -> str | None:
    """지금 해야 할 회차 (morning | noon) 또는 None"""
    if not getattr(settings, "plan_enabled", True):
        return None
    if getattr(settings, "plan_workdays_only", True) and not tp.is_workday(now.date(), holidays):
        return None
    morning, noon = plan_times(settings)
    t = now.strftime("%H:%M")
    if morning <= t < noon:
        return "morning"
    if noon <= t < max(DAY_END, noon):
        return "noon"
    return None


def item_allowed(x: dict, prefs) -> bool:
    """알림 범위 설정(받을 곳·학교 업무만·긴급·중요만·긴급만)을 할 일 하나에 적용.
    확정 일정·확정할 일정은 사용자가 넣거나 고를 일정이라 항상 포함."""
    if prefs is None or x.get("kind") != "todo":
        return True
    if x.get("source") and x.get("source") not in prefs.sources and x.get("source") != "manual":
        return False
    imp = x.get("importance") or ""
    if prefs.scope == "school":
        if x.get("school") != "학교 업무":
            return False
        return imp == "긴급" if prefs.high_only else True
    if prefs.scope == "important":
        return imp == "긴급" if prefs.high_only else imp in ("긴급", "중요")
    return imp == "긴급" if prefs.high_only else True


def toast_for(plan: dict, slot: str, preview: bool, prefs=None,
              seen_suggested: set | None = None) -> tuple[str, str] | None:
    """알림 문구. 알림 범위에 드는 할 일만 세고 보여 줌.
    점심 회차: 확정할 일정은 아침 정리에 없던 것(seen_suggested 밖)만 셈 (같은 제안을 다시 알리지 않음).
    남은 할 일도 새 제안도 없으면 None (알림 생략)."""
    all_items = plan.get("items") or []
    items = [x for x in all_items if item_allowed(x, prefs)]
    sugg = plan.get("suggested") or []
    if slot == "noon":
        seen = seen_suggested or set()
        sugg = [x for x in sugg if x.get("ref_id") not in seen]
        if not items and not sugg:
            return None
    if not items and not sugg:
        if all_items:
            return None      # 할 일은 있지만 모두 알림 범위 밖 → 알리지 않음 ('오늘' 탭에는 보임)
        return "오늘 할 일", "오늘 챙길 일이 없어요."
    title = "오늘 할 일 순서" if slot == "morning" else "오후 할 일 순서"
    if not items:   # 할 일은 없고 확정할 일정만 있음
        return title, f"확정할 일정 {len(sugg)}건이 있어요. 캘린더 '오늘' 탭에서 보세요."
    if not preview:
        return title, f"{len(items)}건을 정리했어요. 캘린더 '오늘' 탭에서 보세요. (알림 미리보기 꺼짐)"
    lines = [f"{x.get('no')}. {x.get('title', '')} — {x.get('reason', '')}" for x in items[:2]]
    more = len(items) - len(lines)
    body = "\n".join(lines) + (f"\n그 밖에 {more}건" if more > 0 else "")
    if sugg:
        body += f"\n확정할 일정 {len(sugg)}건"
    return title, body[:240]


class PlanScheduler:
    def __init__(self, db_path: str, settings_fn: Callable | None = None, on_change: Callable | None = None,
                 notify_fn: Callable | None = None, now_fn: Callable | None = None, holidays: dict | None = None,
                 interval: float = 60.0):
        from .config import get_settings
        self.db_path = db_path
        self.settings_fn = settings_fn or get_settings
        self.on_change = on_change or (lambda: None)
        self.notify_fn = notify_fn
        self.now_fn = now_fn or _seoul_now
        self.holidays = holidays
        self.interval = interval
        self._stop = threading.Event()
        self._storage = None
        self.last_error = ""

    def _store(self):
        if self._storage is None:
            from .storage import Storage
            self._storage = Storage(self.db_path)
        return self._storage

    def _holidays(self) -> dict:
        if self.holidays is None:
            try:
                from .desktop_calendar.api import load_holidays
                self.holidays = load_holidays()
            except Exception:  # noqa: BLE001
                # 실패를 기억하지 않음 → 다음 확인 때 다시 읽어 봄 (한 번 실패로 공휴일에도 알림이 나가지 않게)
                log.warning("공휴일 목록을 읽지 못했습니다. 다음 확인 때 다시 읽습니다.")
                return {}
        return self.holidays

    def tick(self) -> dict | None:
        """한 번 확인. 이번에 정리했으면 그 결과, 아니면 None."""
        s = self.settings_fn()
        now = self.now_fn()
        slot = due_slot(s, now, self._holidays())
        if not slot:
            return None
        st = self._store()
        plan_date = now.strftime("%Y-%m-%d")
        plan = tp.run_plan(st, now, slot)
        if plan is None:
            # 이미 끝난 회차인데 알림 처리 전에 프로그램이 꺼졌던 경우(notified=0) → 지금 한 번 알림
            # (방해금지·알림 끄기로 안 보낸 것은 notified=1 이라 다시 보내지 않음)
            saved = st.get_plan(plan_date, slot)
            if not saved or saved.get("notified"):
                return None
            plan = saved.get("data") or {}
        else:
            log.info("오늘 할 일 정리 (%s): %s", slot, plan.get("headline"))
        if st.mark_plan_notified(plan_date, slot):
            seen = None
            if slot == "noon":   # 아침 정리에서 이미 알린 확정할 일정
                morning = st.get_plan(plan_date, "morning") or {}
                seen = {x.get("ref_id") for x in ((morning.get("data") or {}).get("suggested") or [])}
            self._notify(s, now, plan, slot, seen)
        try:
            self.on_change()
        except Exception:  # noqa: BLE001
            pass
        return plan

    def _notify(self, s, now: datetime, plan: dict, slot: str, seen_suggested: set | None = None) -> bool:
        from .notify_policy import NotifyPrefs, in_quiet
        prefs = NotifyPrefs.from_settings(s)
        if not prefs.enabled or in_quiet(prefs, now):
            return False     # 정리는 했고, 알림만 안 보냄 (나중에 몰아서 보내지 않음)
        text = toast_for(plan, slot, prefs.preview, prefs, seen_suggested)
        if not text:
            return False
        send = self.notify_fn
        if send is None:
            from .notifier import notify as send
        send(text[0], text[1], sound=prefs.sound)
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = ""
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)[:200]
                log.exception("오늘 할 일 정리 실패")
            self._stop.wait(self.interval)

    def start(self) -> "PlanScheduler":
        threading.Thread(target=self._loop, name="plan-scheduler", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()


def _seoul_now() -> datetime:
    from .notify_policy import SEOUL
    return datetime.now(SEOUL).replace(tzinfo=None)

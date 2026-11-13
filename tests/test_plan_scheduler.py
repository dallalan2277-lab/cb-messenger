"""하루 2번 '오늘 할 일 순서' 정리·알림 — 가짜 시각·가짜 알림으로."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app import plan_scheduler as ps
from app.models import Analysis, Message
from app.plan_scheduler import PlanScheduler, due_slot, toast_for
from app.storage import Storage


def settings(**kw):
    base = dict(plan_enabled=True, plan_times=["08:30", "12:40"], plan_workdays_only=True,
                notify_enabled=True, notify_scope="school", notify_high_only=False, notify_sound=False,
                notify_quiet=True, notify_quiet_start="22:00", notify_quiet_end="08:00",
                notify_preview=False, notify_sources=["eztalk", "mail"])
    base.update(kw)
    return SimpleNamespace(**base)


THU = (2026, 11, 12)          # 목요일


@pytest.mark.parametrize("hm,slot", [((8, 0), None), ((8, 31), "morning"), ((10, 0), "morning"),
                                     ((12, 41), "noon"), ((15, 0), "noon"), ((19, 0), None)])
def test_due_slot_by_time(hm, slot):
    assert due_slot(settings(), datetime(*THU, *hm), {}) == slot


def test_due_slot_weekend_holiday_and_options():
    sat = datetime(2026, 11, 14, 9, 0)
    assert due_slot(settings(), sat, {}) is None
    assert due_slot(settings(plan_workdays_only=False), sat, {}) == "morning"
    assert due_slot(settings(), datetime(*THU, 9, 0), {"2026-11-12": "가상 공휴일"}) is None
    assert due_slot(settings(plan_enabled=False), datetime(*THU, 9, 0), {}) is None
    assert due_slot(settings(plan_times=["09:00", "15:30"]), datetime(*THU, 8, 50), {}) is None
    assert due_slot(settings(plan_times=["09:00", "15:30"]), datetime(*THU, 15, 30), {}) == "noon"
    assert due_slot(settings(plan_times=["이상한", "값"]), datetime(*THU, 8, 31), {}) == "morning"   # 기본값으로


class Clock:
    def __init__(self, *dt):
        self.now = datetime(*dt)

    def __call__(self):
        return self.now


def make(tmp_path, clock, **kw):
    sent = []
    sch = PlanScheduler(str(tmp_path / "p.db"), settings_fn=lambda: settings(**kw),
                        notify_fn=lambda t, b, sound=False: sent.append((t, b)), now_fn=clock, holidays={})
    return sch, sent


def add_todo(st, deadline="2026-11-12", subject="가상 제출 안내", school="학교 업무", source="eztalk",
             received_at="2026-11-11 10:00"):
    m = Message(sender="가상 보낸이", body="가상 본문", received_at=received_at, source=source,
                external_id=subject, subject=subject)
    st.add_message(m)
    st.add_analysis(Analysis(message_id=m.id, importance="중요", category="처리 필요", summary="가상",
                             deadline=deadline, provider="fake", school_relevance=school))
    return m.id


def test_morning_once_then_noon_once(tmp_path):
    clock = Clock(*THU, 8, 31)
    sch, sent = make(tmp_path, clock)
    add_todo(sch._store())
    assert sch.tick() is not None and len(sent) == 1
    clock.now = datetime(*THU, 9, 0)
    assert sch.tick() is None and len(sent) == 1            # 아침 회차는 한 번만
    clock.now = datetime(*THU, 12, 41)
    assert sch.tick() is not None and len(sent) == 2
    assert sent[0][0] == "오늘 할 일 순서" and "1건" in sent[0][1] and "미리보기 꺼짐" in sent[0][1]


def test_started_in_afternoon_runs_noon_only(tmp_path):
    clock = Clock(*THU, 15, 0)
    sch, sent = make(tmp_path, clock)
    add_todo(sch._store())
    sch.tick()
    st = sch._store()
    assert st.get_plan("2026-11-12", "noon") and st.get_plan("2026-11-12", "morning") is None


def test_quiet_hours_make_plan_but_no_toast_and_no_later_burst(tmp_path):
    clock = Clock(*THU, 8, 31)
    sch, sent = make(tmp_path, clock, notify_quiet_start="08:00", notify_quiet_end="09:00")
    add_todo(sch._store())
    assert sch.tick() is not None and sent == []
    clock.now = datetime(*THU, 9, 30)                        # 방해금지가 끝나도 몰아서 보내지 않음
    sch.tick()
    assert sent == []


def test_notify_off_still_makes_plan(tmp_path):
    sch, sent = make(tmp_path, Clock(*THU, 8, 31), notify_enabled=False)
    add_todo(sch._store())
    assert sch.tick() is not None and sent == []


def test_noon_without_remaining_todo_skips_toast(tmp_path):
    clock = Clock(*THU, 8, 31)
    sch, sent = make(tmp_path, clock)
    m = add_todo(sch._store())
    sch.tick()
    sch._store().set_done(m)                                  # 아침에 처리함
    clock.now = datetime(*THU, 12, 41)
    assert sch.tick() is not None and len(sent) == 1         # 점심 정리는 했지만 남은 할 일이 없어 알림 없음


def test_two_schedulers_same_db_notify_once(tmp_path):
    clock = Clock(*THU, 8, 31)
    a, sent_a = make(tmp_path, clock)
    b, sent_b = make(tmp_path, clock)
    add_todo(a._store())
    a.tick()
    b.tick()
    assert len(sent_a) + len(sent_b) == 1


def test_toast_preview_shows_titles_and_reasons():
    plan = {"items": [{"no": 1, "title": "가상 설문", "reason": "마감 오늘 16:00"},
                      {"no": 2, "title": "가상 회의", "reason": "오늘 14:00"},
                      {"no": 3, "title": "가상 연수", "reason": "내일 09:00"}],
            "suggested": [{"title": "가상 제안"}]}
    title, body = toast_for(plan, "morning", True)
    assert title == "오늘 할 일 순서"
    assert body.splitlines() == ["1. 가상 설문 — 마감 오늘 16:00", "2. 가상 회의 — 오늘 14:00", "그 밖에 1건", "확정할 일정 1건"]
    assert toast_for({"items": []}, "noon", True) is None


def test_tick_error_is_kept_and_loop_continues(tmp_path, monkeypatch):
    sch, _ = make(tmp_path, Clock(*THU, 8, 31))
    monkeypatch.setattr(ps.tp, "run_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("가상 오류")))
    sch.interval = 0.01
    sch.start()
    import time
    for _ in range(100):
        if sch.last_error:
            break
        time.sleep(0.02)
    sch.stop()
    assert "가상 오류" in sch.last_error


# ---- Codex 검토(2026-11-13)로 고친 것 ----

def test_crash_between_save_and_notify_sends_once_on_restart(tmp_path):
    clock = Clock(*THU, 8, 31)
    sch, sent = make(tmp_path, clock)
    add_todo(sch._store())
    ps.tp.run_plan(sch._store(), clock(), "morning")          # 저장까지만 하고 꺼진 것처럼
    clock.now = datetime(*THU, 8, 40)
    assert sch.tick() is not None and len(sent) == 1
    assert sch.tick() is None and len(sent) == 1


def test_quiet_skip_is_not_resent_after_restart(tmp_path):
    clock = Clock(*THU, 8, 31)
    sch, sent = make(tmp_path, clock, notify_quiet_start="08:00", notify_quiet_end="09:00")
    add_todo(sch._store())
    sch.tick()
    clock.now = datetime(*THU, 9, 30)
    sch2, sent2 = make(tmp_path, clock)
    sch2.tick()
    assert sent == [] and sent2 == []


def test_toast_follows_notify_scope(tmp_path):
    clock = Clock(*THU, 8, 31)
    sch, sent = make(tmp_path, clock, notify_preview=True)
    add_todo(sch._store(), subject="가상 일반 메일", school="관련 없음", source="mail")
    sch.tick()
    assert sent == []                                          # 학교 업무만 → 일반 메일은 알리지 않음
    plan = sch._store().get_plan("2026-11-12", "morning")["data"]
    from app.notify_policy import NotifyPrefs
    assert toast_for(plan, "morning", True, NotifyPrefs(scope="all")) is not None
    assert toast_for(plan, "morning", True, NotifyPrefs(scope="all", sources=("eztalk",))) is None

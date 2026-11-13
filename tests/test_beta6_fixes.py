"""beta.6 수정 (PR #4 Gemini 1차 리뷰 반영) — 가상 자료만, 네트워크·키 없이."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.event_match import classify
from app.plan_scheduler import PlanScheduler, toast_for
from app.storage import Storage

DAY = "2026-11-20"


# ---- 1. 첫 업그레이드 때 두 프로그램이 동시에 칸을 추가해도 멈추지 않음 ----
class _StaleTableInfo:
    """PRAGMA table_info 가 옛 결과(칸 없음)를 주는 상황 = 다른 프로그램이 방금 칸을 추가한 경우"""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA table_info"):
            return []
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_migrate_ignores_column_added_by_other_process(tmp_path):
    st = Storage(str(tmp_path / "m.db"))
    real = st.conn
    st.conn = _StaleTableInfo(real)
    try:
        st._migrate()          # 모든 ALTER 가 'duplicate column' → 무시해야 함
    finally:
        st.conn = real


def test_connection_waits_when_locked(tmp_path):
    st = Storage(str(tmp_path / "w.db"))
    assert st.conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 30000


# ---- 2. '다시 정리' 가 실패해도 직전 수동 정리는 남음 ----
def test_failed_manual_replan_keeps_previous_result(tmp_path):
    st = Storage(str(tmp_path / "p.db"))
    assert st.claim_plan(DAY, "manual", "2026-11-20 09:00:00")
    assert st.finish_plan(DAY, "manual", {"headline": "이전 정리"}, "rule", "2026-11-20 09:00:05",
                          claimed_at="2026-11-20 09:00:00")
    assert st.claim_plan(DAY, "manual", "2026-11-20 10:00:00")      # 다시 정리 시작
    assert st.get_plan(DAY, "manual") is None                        # 정리 중
    st.conn.execute("BEGIN")                                          # 정리 중 오류로 트랜잭션이 남은 상황
    st.release_plan(DAY, "manual", "2026-11-20 10:00:00")
    plan = st.get_plan(DAY, "manual")
    assert plan and plan["data"]["headline"] == "이전 정리"
    assert st.claim_plan(DAY, "manual", "2026-11-20 10:05:00")      # 다시 맡을 수 있음


def test_failed_scheduled_run_can_be_claimed_again(tmp_path):
    st = Storage(str(tmp_path / "q.db"))
    assert st.claim_plan(DAY, "morning", "2026-11-20 08:31:00")
    st.release_plan(DAY, "morning", "2026-11-20 08:31:00")
    assert st.get_plan(DAY, "morning") is None                       # 결과 없던 회차는 그대로 미완료
    assert st.claim_plan(DAY, "morning", "2026-11-20 08:32:00")


# ---- 3. 공휴일 목록을 한 번 못 읽어도 다음에 다시 읽음 ----
def test_holidays_failure_is_not_cached(tmp_path, monkeypatch):
    import app.desktop_calendar.api as api
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("읽기 실패")
        return {"2026-11-20": "가상 휴일"}

    monkeypatch.setattr(api, "load_holidays", flaky)
    sch = PlanScheduler(str(tmp_path / "h.db"), settings_fn=lambda: None, now_fn=datetime.now)
    assert sch._holidays() == {}
    assert sch._holidays() == {"2026-11-20": "가상 휴일"}


# ---- 4. 메모로 확정할 때 기존 제안에 없던 시각·장소를 채움 ----
def test_memo_confirm_fills_missing_time_and_place(tmp_path):
    st = Storage(str(tmp_path / "e.db"))
    old = st.add_calendar_event(title="가상 학년 협의회", date=DAY, status="suggested", origin="ai")
    memo_id = st.add_memo(title="가상 메모", kind="기타", source_text="가상")
    r = st.add_event_from_user({"title": "가상 학년 협의회", "date": DAY, "start_time": "15:00",
                                "location": "가상 회의실"}, memo_id=memo_id)
    assert r["id"] == old and r["existed"]
    row = st.conn.execute("SELECT * FROM calendar_events WHERE id=?", (old,)).fetchone()
    assert (row["status"], row["start_time"], row["location"]) == ("confirmed", "15:00", "가상 회의실")


def test_memo_confirm_keeps_existing_time(tmp_path):
    st = Storage(str(tmp_path / "f.db"))
    old = st.add_calendar_event(title="가상 학년 협의회", date=DAY, start_time="15:00", location="가상 교실",
                                status="suggested", origin="ai")
    memo_id = st.add_memo(title="가상 메모", kind="기타", source_text="가상")
    st.add_event_from_user({"title": "가상 학년 협의회", "date": DAY, "start_time": "",
                            "location": ""}, memo_id=memo_id)
    row = st.conn.execute("SELECT * FROM calendar_events WHERE id=?", (old,)).fetchone()
    assert (row["start_time"], row["location"]) == ("15:00", "가상 교실")


# ---- 6. 짧은 제목은 시각이 양쪽 다 있고 같을 때만 자동으로 같은 일 ----
def test_short_title_needs_matching_times():
    def ev(title, t=""):
        return {"title": title, "date": DAY, "start_time": t}
    assert classify(ev("회의"), ev("회의", "15:00"))[0] == "maybe"
    assert classify(ev("출장"), ev("출장"))[0] == "maybe"
    assert classify(ev("회의", "15:00"), ev("회의", "15:00"))[0] == "same"
    assert classify(ev("회의", "9:00"), ev("회의", "09:00"))[0] == "same"
    # 3글자 이상은 예전처럼 (재전송 쪽지를 또 묻지 않음)
    assert classify(ev("학부모 상담 안내"), ev("학부모 상담", "09:00"))[0] == "same"


# ---- 점심 알림: 아침에 없던 확정할 일정은 알림, 이미 알린 것은 다시 안 알림 ----
def test_noon_toast_for_new_suggestions_only():
    plan = {"items": [], "suggested": [{"ref_id": 1, "title": "가상 제안 A"}, {"ref_id": 2, "title": "가상 제안 B"}]}
    assert toast_for(plan, "noon", True, seen_suggested={1, 2}) is None
    title, body = toast_for(plan, "noon", True, seen_suggested={1})
    assert title == "오후 할 일 순서" and "1건" in body
    assert toast_for({"items": []}, "noon", True) is None


# ---- 5. '감사합니다' 를 한 번만 지움 ----
def test_migrate_skip_thanks_once(tmp_path):
    env = tmp_path / ".env"
    marker = tmp_path / "data" / ".marker"
    env.write_text("# 설명\nSKIP_AI_KEYWORDS=식단표,급식,감사합니다,생일 축하\nUSER_ROLE=가상\n", encoding="utf-8")
    assert config.migrate_skip_thanks(env, marker) is True
    text = env.read_text(encoding="utf-8")
    assert "SKIP_AI_KEYWORDS=식단표,급식,생일 축하" in text and "USER_ROLE=가상" in text and "# 설명" in text
    # 사용자가 나중에 일부러 다시 넣으면 그대로 둠
    env.write_text("SKIP_AI_KEYWORDS=감사합니다\n", encoding="utf-8")
    assert config.migrate_skip_thanks(env, marker) is False
    assert env.read_text(encoding="utf-8") == "SKIP_AI_KEYWORDS=감사합니다\n"


def test_migrate_skip_thanks_without_keyword(tmp_path):
    env = tmp_path / ".env"
    marker = tmp_path / ".marker"
    env.write_text("SKIP_AI_KEYWORDS=급식\n", encoding="utf-8")
    assert config.migrate_skip_thanks(env, marker) is False and marker.exists()
    assert env.read_text(encoding="utf-8") == "SKIP_AI_KEYWORDS=급식\n"

"""오늘 할 일 순서 (규칙 단계, AI 없음) — 가상 예시만."""
from datetime import datetime

import pytest

from app import today_plan as tp
from app.models import Analysis, Message
from app.storage import Storage

NOW = datetime(2026, 11, 12, 9, 0)        # 목요일 09:00
TODAY, TOMORROW = "2026-11-12", "2026-11-13"


@pytest.fixture
def db():
    st = Storage(":memory:")
    yield st
    st.close()


_n = [0]


def msg(db, *, deadline="", importance="중요", category="처리 필요", received="2026-11-11 15:00",
        subject="", summary="가상 안내", actions=None, provider="fake", done=False):
    _n[0] += 1
    m = Message(sender=f"가상 보낸이{_n[0]}", body="가상 본문", received_at=received, source="eztalk",
                external_id=f"x{_n[0]}", subject=subject)
    db.add_message(m)
    db.add_analysis(Analysis(message_id=m.id, importance=importance, category=category, summary=summary,
                             action_items=actions or [], deadline=deadline, provider=provider))
    if done:
        db.set_done(m.id)
    return m.id


def event(db, title, day=TODAY, time="", status="confirmed", end=""):
    return db.add_calendar_event(title=title, date=day, start_time=time, end_time=end, status=status)


def kinds(items):
    return [(it.kind, it.ref_id) for it in items]


def test_only_today_tomorrow_confirmed_events_and_suggestions_apart(db):
    a = event(db, "가상 회의", time="14:00")
    b = event(db, "가상 연수", day=TOMORROW)
    event(db, "모레 일정", day="2026-11-14")
    s = event(db, "가상 제안", time="10:00", status="suggested")
    items, sugg, overflow = tp.collect(db, NOW)
    assert kinds(items) == [("event", a), ("event", b)]
    assert [x.ref_id for x in sugg] == [s] and sugg[0].reason == "오늘 10:00 · 확정 필요"
    assert overflow == 0


def test_deadline_window_and_unreadable_deadline(db):
    past = msg(db, deadline="2026-11-09")              # 3일 전 → 마감 지남
    too_old = msg(db, deadline="2026-11-08")           # 4일 전 → 빠짐
    soon = msg(db, deadline="2026-11-19")              # 7일 뒤 → 들어감
    far = msg(db, deadline="2026-11-20")               # 8일 뒤 → 빠짐
    bad = msg(db, deadline="다음 주 화요일")              # 못 읽음 → 빠짐 (지난 정리 이후 새 소식이 아니면)
    items, _, _ = tp.collect(db, NOW, last_plan_at="2026-11-12 08:00:00")
    ids = {it.ref_id for it in items}
    assert past in ids and soon in ids
    assert not ({too_old, far, bad} & ids)
    assert next(it for it in items if it.ref_id == past).overdue


def test_new_messages_since_last_plan_skip_fyi_and_rule(db):
    new = msg(db, received="2026-11-12 08:30")
    msg(db, received="2026-11-12 08:31", category="참고", importance="참고")
    msg(db, received="2026-11-12 08:32", category="광고·소식")
    msg(db, received="2026-11-12 08:33", provider="rule")            # AI 생략
    msg(db, received="2026-11-12 08:34", done=True)                  # 처리 완료
    old = msg(db, received="2026-11-11 10:00")                       # 지난 정리 전
    items, _, _ = tp.collect(db, NOW, last_plan_at="2026-11-12 08:00:00")
    assert [it.ref_id for it in items] == [new]
    assert old not in [it.ref_id for it in items]


def test_done_and_snoozed(db):
    e = event(db, "가상 회의", time="14:00")
    m1 = msg(db, deadline=TODAY)
    m2 = msg(db, deadline=TOMORROW)
    db.set_todo("event", e, "done")
    db.set_todo("message", m1, "snoozed", until=TOMORROW)
    db.set_todo("message", m2, "snoozed", until=TODAY)          # 오늘까지 미룸 → 오늘부터 다시 보임
    items, _, _ = tp.collect(db, NOW)
    assert [it.ref_id for it in items] == [m2]
    db.clear_todo("message", m1)
    assert m1 in [it.ref_id for it in tp.collect(db, NOW)[0]]


def test_message_with_confirmed_event_is_one_item(db):
    m = msg(db, deadline=TODAY, received="2026-11-12 08:30")
    db.suggest_event(title="가상 협의회", date=TODAY, start_time="15:00", status="confirmed", message_id=m)
    items, _, _ = tp.collect(db, NOW, last_plan_at="2026-11-12 08:00:00")
    assert [it.kind for it in items] == ["event"]


def test_rule_order_and_reasons(db):
    later = msg(db, deadline="2026-11-14", summary="가상 보고서 제출 안내", actions=["보고서 제출"])
    overdue = msg(db, deadline="2026-11-11 17:00")
    today_t = msg(db, deadline="2026-11-12 16:00", actions=["설문 회신"])
    today_n = msg(db, deadline=TODAY)
    ev = event(db, "가상 회의", time="14:00")
    ev_t = event(db, "가상 연수", day=TOMORROW, time="09:00")
    fresh = msg(db, received="2026-11-12 08:50", importance="긴급")
    items, _, _ = tp.collect(db, NOW, last_plan_at="2026-11-12 08:00:00")
    T, E = "todo", "event"
    assert kinds(items) == [(T, overdue), (T, today_t), (E, ev), (T, today_n), (T, later), (E, ev_t), (T, fresh)]
    assert [it.no for it in items] == list(range(1, 8))
    r = {(it.kind, it.ref_id): it.reason for it in items}
    assert r[(T, overdue)] == "마감 지남 11/11(수) 17:00"
    assert r[(T, today_t)] == "마감 오늘 16:00 · 회신·제출 필요"
    assert r[(E, ev)] == "오늘 14:00" and r[(E, ev_t)] == "내일 09:00"
    assert r[(T, later)] == "마감 11/14(토) · 회신·제출 필요"
    assert r[(T, fresh)] == "긴급"


def test_overflow_cap(db):
    for i in range(tp.MAX_ITEMS + 5):
        msg(db, deadline=TOMORROW)
    items, _, overflow = tp.collect(db, NOW)
    assert len(items) == tp.MAX_ITEMS and overflow == 5
    plan = tp.build_plan(db, NOW, "morning")
    assert "그 밖에 5건" in plan["headline"]


def test_warnings_overlap_and_overdue(db):
    event(db, "가상 회의", time="14:00")                  # 끝 시각 없음 → 1시간
    event(db, "가상 상담", time="14:30", end="15:00")
    event(db, "가상 점검", time="16:00")
    msg(db, deadline="2026-11-10")
    plan = tp.build_plan(db, NOW, "morning")
    kinds_ = sorted(w["kind"] for w in plan["warnings"])
    assert kinds_ == ["overdue", "overlap"]
    overlap = next(w for w in plan["warnings"] if w["kind"] == "overlap")
    titles = {x["no"]: x["title"] for x in plan["items"]}
    assert {titles[n] for n in overlap["nos"]} == {"가상 회의", "가상 상담"}


def test_plan_has_no_ai_text_and_db_values_only(db):
    msg(db, deadline=TODAY, summary="가상 요약", subject="가상 제목")
    plan = tp.build_plan(db, NOW, "morning")
    assert plan["source"] == "rule"
    assert plan["items"][0]["title"] == "가상 제목"
    assert "action" not in plan["items"][0]


def test_claim_once_and_stale_and_manual(db):
    assert db.claim_plan(TODAY, "morning", "2026-11-12 08:30:00")
    assert not db.claim_plan(TODAY, "morning", "2026-11-12 08:31:00")      # 다른 쪽이 정리 중
    assert db.claim_plan(TODAY, "morning", "2026-11-12 08:46:00")          # 15분 넘게 running → 다시
    db.finish_plan(TODAY, "morning", {"items": []}, "rule", "2026-11-12 08:47:00")
    assert not db.claim_plan(TODAY, "morning", "2026-11-12 09:30:00")      # 끝난 회차는 다시 안 함
    assert db.claim_plan(TODAY, "manual", "2026-11-12 10:00:00")
    db.finish_plan(TODAY, "manual", {"items": []}, "rule", "2026-11-12 10:00:01")
    assert db.claim_plan(TODAY, "manual", "2026-11-12 10:05:00")           # 수동은 다시 가능


def test_two_connections_claim_only_one(tmp_path):
    path = str(tmp_path / "p.db")
    a, b = Storage(path), Storage(path)
    try:
        results = [a.claim_plan(TODAY, "noon", "2026-11-12 12:40:00"),
                   b.claim_plan(TODAY, "noon", "2026-11-12 12:40:01")]
        assert results.count(True) == 1
    finally:
        a.close()
        b.close()


def test_run_plan_saves_and_notified_once(db):
    msg(db, deadline=TODAY)
    data = tp.run_plan(db, NOW, "morning")
    assert data and data["items"]
    assert tp.run_plan(db, NOW, "morning") is None
    saved = db.get_plan(TODAY, "morning")
    assert saved["source"] == "rule" and saved["data"]["headline"] == data["headline"]
    assert db.mark_plan_notified(TODAY, "morning")
    assert not db.mark_plan_notified(TODAY, "morning")


def test_noon_plan_uses_morning_finish_as_since(db):
    tp.run_plan(db, NOW, "morning")                             # 09:00 에 끝남
    early = msg(db, received="2026-11-12 08:00")                # 아침 정리 전에 받은 것
    late = msg(db, received="2026-11-12 11:00")                 # 아침 정리 뒤에 받은 것
    noon = datetime(2026, 11, 12, 12, 40)
    data = tp.run_plan(db, noon, "noon")
    ids = [x["ref_id"] for x in data["items"]]
    assert late in ids and early not in ids


def test_live_view_hides_done_and_snoozed_after_plan(db):
    m1 = msg(db, deadline=TODAY)
    m2 = msg(db, deadline=TODAY)
    e = event(db, "가상 회의", time="14:00")
    tp.run_plan(db, NOW, "morning")
    db.set_done(m1)
    db.set_todo("message", m2, "snoozed", until=TOMORROW)
    view = tp.live_view(db, NOW)
    assert [x["ref_id"] for x in view["items"]] == [e] and view["saved"]
    db.delete_calendar_event(e)                                  # 지워진 일정도 빠짐
    assert tp.live_view(db, NOW)["items"] == []


def test_live_view_without_saved_plan_is_preview(db):
    msg(db, deadline=TODAY)
    view = tp.live_view(db, NOW)
    assert view["saved"] is False and len(view["items"]) == 1
    assert db.get_plan(TODAY) is None


def test_old_version_sql_still_works_with_new_tables(db):
    db.conn.execute("INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, "
                    "message_id, memo_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("가상", TODAY, "", "", "", "", "confirmed", "manual", None, None))
    db.conn.commit()
    assert len(db.calendar_events(TODAY, TODAY)) == 1

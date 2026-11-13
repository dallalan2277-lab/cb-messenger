"""받은 소식 목록: '전체'·'학교 업무'는 최신순, '학교 업무'는 처리 완료 포함 (가상 예시)."""
from app.desktop_calendar.api import CalendarAPI
from app.models import Analysis, Message
from app.storage import Storage
from datetime import date, timedelta


def _add(s, when, importance, school, done=False):
    m = Message(sender="가상교사", body=f"가상 쪽지 {when} {importance}", source="eztalk",
                external_id=f"{when}{importance}", received_at=when)
    s.add_message(m)
    s.add_analysis(Analysis(message_id=m.id, importance=importance, category="기타", summary="요약",
                            school_relevance=school))
    if done:
        s.set_done(m.id, True)
    return m.id


def test_feed_orders_and_school_includes_done(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    d = date.today()
    old_urgent = _add(s, f"{d - timedelta(days=3)}T09:00:00", "긴급", "학교 업무", done=True)
    new_ref = _add(s, f"{d}T09:30:00", "참고", "학교 업무", done=True)
    mid_imp = _add(s, f"{d - timedelta(days=1)}T10:00:00", "중요", "일반")
    open_ref = _add(s, f"{d}T08:00:00", "참고", "판단 보류")
    api = CalendarAPI(s, tmp_path / "p.json")
    assert [r["id"] for r in api.feed("all")] == [new_ref, open_ref, mid_imp, old_urgent]       # 최신순
    assert [r["id"] for r in api.feed("school")] == [new_ref, old_urgent]                       # 처리 완료 포함
    assert [r["id"] for r in api.feed("open")] == [mid_imp, open_ref]                            # 처리 안 한 것, 중요도순

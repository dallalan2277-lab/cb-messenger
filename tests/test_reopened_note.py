"""예전에 받은 쪽지를 메신저에서 다시 열어 봤을 때 새 쪽지로 또 분석하지 않는지 — 가상 예시만 사용."""
from app.config import Settings
from app.models import Message
from app.pipeline import build_pipeline
from app.storage import Storage

BODY = "다음 주 수요일 오후 3시에 2층 회의실에서 학년 협의회가 있습니다. 자료를 미리 확인해 주세요."


def _pipe(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.analyze_model = s.digest_model = "fake"
    s.fallback_model, s.skip_ai_keywords, s.skip_ai_min_chars = "", [], 0
    s.include_senders, s.exclude_senders, s.redact_names = [], [], []
    return build_pipeline(s, notify_levels=())


def _note(body, sender="교무부장", key="k1", source="eztalk"):
    return Message(sender=sender, body=body, source=source, external_id=key)


def _count(pipe):
    return pipe.storage.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def test_reopened_note_is_not_analyzed_again(tmp_path):
    pipe = _pipe(tmp_path)
    assert pipe.handle(_note(BODY, key="first")) is not None
    # 다시 열면 줄바꿈·공백이 조금 달라져 이름표(external_id)도 달라짐
    again = BODY.replace(" 자료를", "\n자료를")
    assert pipe.handle(_note(again, key="second")) is None
    assert _count(pipe) == 1


def test_alarm_body_was_cut_then_full_body_fills_in_without_new_analysis(tmp_path):
    pipe = _pipe(tmp_path)
    assert pipe.handle(_note(BODY[:30] + "…", key="alarm")) is not None
    assert pipe.handle(_note("[제목] 협의회 안내\n" + BODY, key="view")) is None
    assert _count(pipe) == 1
    stored = pipe.storage.conn.execute("SELECT body FROM messages").fetchone()[0]
    assert stored.endswith("확인해 주세요.")          # 더 긴 본문으로 보충됨


def test_short_body_from_alarm_is_not_shortened_by_reopen(tmp_path):
    pipe = _pipe(tmp_path)
    pipe.handle(_note(BODY, key="a"))
    assert pipe.handle(_note(BODY[:30] + "…", key="b")) is None
    assert pipe.storage.conn.execute("SELECT body FROM messages").fetchone()[0] == BODY


def test_different_note_from_same_sender_is_still_new(tmp_path):
    pipe = _pipe(tmp_path)
    pipe.handle(_note(BODY, key="a"))
    other = "내일 오전 9시까지 학년 협의회 자료를 제출해 주세요. 양식은 메신저로 보냈습니다."
    assert pipe.handle(_note(other, key="b")) is not None
    assert _count(pipe) == 2


def test_same_text_from_another_sender_is_still_new(tmp_path):
    pipe = _pipe(tmp_path)
    pipe.handle(_note(BODY, key="a"))
    assert pipe.handle(_note(BODY, sender="행정실", key="b")) is not None


def test_short_replies_can_really_come_again(tmp_path):
    """'넵', '감사합니다' 같은 짧은 말은 진짜로 또 올 수 있어 기간 제한 없이 막지 않음"""
    pipe = _pipe(tmp_path)
    pipe.handle(_note("확인했습니다", key="a"))
    assert pipe.handle(_note("확인했습니다 ", key="b")) is not None
    assert _count(pipe) == 2


def test_mail_is_not_affected(tmp_path):
    pipe = _pipe(tmp_path)
    pipe.handle(_note(BODY, key="<a@x>", source="mail"))
    assert pipe.handle(_note(BODY, key="<b@x>", source="mail")) is not None   # 메일은 Message-ID 로 따로 구별


def test_body_key_ignores_title_spaces_and_ellipsis():
    assert Storage.body_key("[제목] 안내\n가 나  다…") == "가나다"
    assert Storage.body_key("") == ""


# ---------- 감시를 켜기 전에 받은 쪽지를 보기 창으로 다시 연 경우 ----------
from datetime import datetime  # noqa: E402

from app.collectors.eztalk_parse import is_reopened_old, sent_time  # noqa: E402

NOW = datetime(2026, 11, 10, 9, 30)


def test_sent_time_formats():
    assert sent_time("2026.11.09 15:25", NOW) == datetime(2026, 11, 9, 15, 25)
    assert sent_time("2026-11-09 오후 3:25", NOW) == datetime(2026, 11, 9, 15, 25)
    assert sent_time("11/09 (월) 오전 12:05", NOW) == datetime(2026, 11, 9, 0, 5)
    assert sent_time("11월 9일 15시 25분", NOW) == datetime(2026, 11, 9, 15, 25)
    assert sent_time("오전 8:10", NOW) == datetime(2026, 11, 10, 8, 10)          # 날짜 없으면 오늘
    assert sent_time("오후 11:50", NOW) == datetime(2026, 11, 9, 23, 50)         # 오늘로 치면 미래 → 어제
    assert sent_time("12.30 10:00", datetime(2027, 1, 2, 9, 0)) == datetime(2026, 12, 30, 10, 0)   # 해 넘김
    assert sent_time("", NOW) is None and sent_time("시각 없음", NOW) is None
    assert sent_time("2026.13.40 25:99", NOW) is None


def test_reopened_old_view_window_is_skipped_but_alarms_never():
    started = datetime(2026, 11, 10, 8, 30).timestamp()
    old_view = {"source": "view", "time": "2026.11.09 15:25"}
    assert is_reopened_old(old_view, started, now=NOW) is True
    assert is_reopened_old({"source": "alarm", "time": "2026.11.09 15:25"}, started, now=NOW) is False
    assert is_reopened_old({"source": "view", "time": "2026.11.10 08:25"}, started, now=NOW) is False   # 켜기 직전 10분 안
    assert is_reopened_old({"source": "view", "time": "2026.11.10 09:20"}, started, now=NOW) is False   # 켠 뒤에 온 쪽지
    assert is_reopened_old({"source": "view", "time": ""}, started, now=NOW) is False                   # 시각을 못 읽으면 새 쪽지로


# ---------- 내가 보낸 쪽지 ----------
def _pipe_me(tmp_path, names):
    pipe = _pipe(tmp_path)
    pipe.s.my_names = names
    return pipe


def test_my_own_note_is_skipped(tmp_path):
    pipe = _pipe_me(tmp_path, ["홍길동"])
    assert pipe.handle(_note(BODY, sender="홍길동")) is None
    assert pipe.handle(_note(BODY.replace("자료", "서류"), sender="홍길동 선생님", key="k2")) is None
    assert _count(pipe) == 0


def test_similar_names_and_others_still_come_in(tmp_path):
    pipe = _pipe_me(tmp_path, ["홍길동"])
    assert pipe.handle(_note(BODY, sender="홍길동수", key="a")) is not None      # 이름 일부만 같은 다른 사람
    assert pipe.handle(_note(BODY, sender="김철수", key="b")) is not None
    assert _count(pipe) == 2


def test_no_my_name_set_means_nothing_skipped_and_mail_unaffected(tmp_path):
    pipe = _pipe_me(tmp_path, [])
    assert pipe.handle(_note(BODY, sender="홍길동")) is not None
    pipe2 = _pipe_me(tmp_path / "b", ["홍길동"])
    (tmp_path / "b").mkdir(exist_ok=True)
    assert pipe2.handle(_note(BODY, sender="홍길동", source="mail", key="<m@x>")) is not None

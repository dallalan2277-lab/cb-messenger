"""소통메신저 창 글자 파싱 + 원문·요약 보관(로컬/드라이브) 테스트. 메시지는 모두 가상 예시입니다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors.eztalk_collector import event_to_message
from app.collectors.eztalk_parse import Batch, parse_alarm, parse_view, sender_name
from app.extensions.archive import Archiver, PENDING_DIRNAME
from app.models import Analysis, Event, Message
from app.redact import redact

ALARM = ["​쪽지", "오후 3:25", "홍길동", "내일 15시 회의가 있습니다…"]
VIEW = ["받은쪽지", "[공지] 회의 안내", "보낸사람 :홍길동 - (123) 교무부", "2026.11.09 15:25",
        "내일 15시 회의가 있습니다. 자료를 준비해 주세요.", "내일 15시 회의가 있습니다.", "자료를 준비해 주세요.",
        "※ 쪽지는 30일 후 삭제됩니다"]
SECURE = ["받은쪽지", "보안쪽지 - 비밀번호를 입력해야 볼 수 있습니다", "보낸사람 :홍길동", "(내용 숨김)"]


def test_sender_name():
    assert sender_name("원진아 - (223) 도서관/정보") == "원진아"
    assert sender_name("홍길동") == "홍길동"


def test_parse_alarm():
    ev = parse_alarm(ALARM)
    assert (ev["kind"], ev["time"], ev["sender"]) == ("쪽지", "오후 3:25", "홍길동")
    assert ev["body"].startswith("내일 15시") and ev["ok"]


def test_parse_view():
    ev = parse_view(VIEW)
    assert ev["sender"] == "홍길동" and ev["title"] == "[공지] 회의 안내"
    assert ev["body"] == "내일 15시 회의가 있습니다. 자료를 준비해 주세요."
    assert ev["time"] == "2026.11.09 15:25" and ev["ok"]


def test_secure_note_not_readable():
    ev = parse_view(SECURE)
    assert ev["secure"] and not ev["ok"]


def test_body_word_bojan_is_not_secure():
    v = ["받은쪽지", "보낸사람 :홍길동", "정보 보안 점검 안내입니다. 금요일까지 PC 점검 부탁드립니다.",
         "정보 보안 점검 안내입니다.", "금요일까지 PC 점검 부탁드립니다."]
    ev = parse_view(v)
    assert ev["ok"] and not ev["secure"]


def test_batch_merges_alarm_then_view():
    b = Batch(quiet_sec=5)
    assert b.add(parse_alarm(ALARM), now=100) == "new"
    assert b.add(parse_view(VIEW), now=101) == "upgrade"      # 같은 쪽지, 더 긴 본문
    assert not b.ready(now=103) and b.ready(now=107)
    out = b.flush(now=107)
    assert len(out) == 1 and out[0]["body"].endswith("준비해 주세요.")
    assert b.add(parse_view(VIEW), now=200) == "dup"         # 30분 안에 다시 열어도 중복
    m = event_to_message(out[0])
    assert m.source == "eztalk" and m.sender == "홍길동" and m.body.startswith("[제목] [공지] 회의 안내")


def _msg():
    return Message(sender="홍길동", body="김민수 보호자 010-1234-5678 상담 요청", received_at="2026-11-09T15:25:00",
                   room="쪽지", source="eztalk", external_id="k1")


def _analysis():
    return Analysis(message_id=1, importance="중요", category="회의", summary="상담 요청",
                    action_items=["보호자 연락"], deadline="2026-11-10",
                    events=[Event(title="상담", date="2026-11-10", start_time="16:00")])


def test_archive_local_and_drive_separately(tmp_path):
    local, drive = tmp_path / "local", tmp_path / "drive"
    a = Archiver(local_enabled=True, local_dir=str(local), gdrive_enabled=True, gdrive_dir=str(drive),
                 fmt="txt", gdrive_redact=True, redact_fn=lambda t: redact(t, ["김민수"]))
    a.save_message(_msg())
    a.save_analysis(_msg(), _analysis())
    lt = (local / "쪽지원문_2026-11.txt").read_text(encoding="utf-8")
    dt = (drive / "쪽지원문_2026-11.txt").read_text(encoding="utf-8")
    assert "010-1234-5678" in lt and "김민수" in lt                 # 이 PC: 원문 그대로
    assert "010-1234-5678" not in dt and "김민수" not in dt         # 드라이브: 가려서
    assert "[2026-11-09 15:25:00] 보낸이: 홍길동 (쪽지)" in dt
    st = (drive / "쪽지요약_2026-11.txt").read_text(encoding="utf-8")
    assert "[중요/회의] 홍길동: 상담 요청" in st and "마감: 2026-11-10" in st and "일정: 2026-11-10 16:00 상담" in st


def test_archive_only_local_or_only_drive(tmp_path):
    a = Archiver(local_enabled=False, local_dir=str(tmp_path / "l"), gdrive_enabled=True,
                 gdrive_dir=str(tmp_path / "d"), fmt="md")
    a.save_message(_msg())
    assert (tmp_path / "d" / "쪽지원문_2026-11.md").exists() and not (tmp_path / "l").exists()
    b = Archiver(local_enabled=True, local_dir=str(tmp_path / "l2"), gdrive_enabled=False, gdrive_dir="")
    b.save_message(_msg())
    assert (tmp_path / "l2" / "쪽지원문_2026-11.txt").exists()
    c = Archiver(local_enabled=False, local_dir=str(tmp_path / "l3"), gdrive_enabled=False, gdrive_dir="")
    assert not c.enabled


def test_drive_unavailable_goes_to_pending_then_syncs(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")                       # 파일 아래에 폴더를 못 만들게 해서 '드라이브 실패' 흉내
    a = Archiver(local_enabled=False, local_dir=str(tmp_path / "l"), gdrive_enabled=True,
                 gdrive_dir=str(blocker / "drive"), fmt="txt", gdrive_redact=False)
    a.save_message(_msg())
    pend = tmp_path / "l" / PENDING_DIRNAME / "쪽지원문_2026-11.txt"
    assert pend.exists() and "드라이브" in a.status["last_error"]
    a.gdrive_dir = str(tmp_path / "drive_ok")      # 드라이브가 다시 연결됨
    assert a.sync_pending() == 1 and not pend.exists()
    txt = (tmp_path / "drive_ok" / "쪽지원문_2026-11.txt").read_text(encoding="utf-8")
    assert txt.count("쪽지 원문 2026-11") == 1 and "상담 요청" in txt


def test_pipeline_archives_only_selected_sources(tmp_path):
    from app.collectors import message_from_text
    from app.config import Settings
    from app.pipeline import build_pipeline
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.analyze_model = s.digest_model = "fake"
    s.fallback_model, s.skip_ai_keywords, s.skip_ai_min_chars = "", [], 0
    s.include_senders, s.exclude_senders, s.redact_names = [], [], []
    s.archive_local_enabled, s.archive_local_dir = True, str(tmp_path / "arch")
    s.archive_gdrive_enabled, s.archive_sources = False, ["eztalk"]
    pipe = build_pipeline(s, notify_levels=())
    pipe.handle(message_from_text("테스트 입력은 보관 안 함"))
    assert not (tmp_path / "arch").exists()
    pipe.handle(_msg())
    files = sorted(p.name for p in (tmp_path / "arch").iterdir())
    assert files == ["쪽지요약_2026-11.txt", "쪽지원문_2026-11.txt"]

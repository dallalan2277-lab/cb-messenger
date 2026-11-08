"""실행기(쪽지·메일 → 한 줄 AI 처리)와 캘린더 '받은 소식' API 테스트"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.runner as runner_mod
from app.config import Settings
from app.desktop_calendar.api import CalendarAPI
from app.models import Message
from app.pipeline import build_pipeline
from app.runner import Runner
from app.storage import Storage


def _settings(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.analyze_model = s.digest_model = "fake"
    s.fallback_model, s.skip_ai_keywords, s.skip_ai_min_chars = "", [], 0
    s.include_senders, s.exclude_senders, s.redact_names = [], [], []
    s.archive_local_enabled = s.archive_gdrive_enabled = False
    s.notify_enabled = False
    s.mail_enabled, s.mail_account, s.mail_interval_min = True, "teacher@naver.com", 1
    return s


def mail(i, subj, body):
    now = datetime.now().replace(microsecond=0)
    return Message(sender=f"교무부 <office{i}@school.kr>", body=f"[제목] {subj}\n\n{body}", room="메일", source="mail",
                   external_id=f"m{i}", subject=subj, received_at=(now - timedelta(minutes=i)).isoformat())


class FakeMailCol:
    def __init__(self, batches):
        self.batches, self.last_check, self.paused_reason, self.last_error, self.checked = list(batches), 0.0, "", "", 0
    def collect_once(self):
        self.last_check = time.time()
        return self.batches.pop(0) if self.batches else []
    def check_now(self):
        self.checked += 1


def test_runner_processes_mail_queue(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    col = FakeMailCol([[mail(1, "회의 안내", "학년 회의가 내일 있습니다"), mail(2, "광고", "할인 이벤트")]])
    monkeypatch.setattr(runner_mod, "make_mail_collector", lambda s_, st, on_error=None: (col, ""))
    changes = []
    r = Runner(s, on_change=lambda: changes.append(1), watch_messenger=False).start()
    for _ in range(50):
        if r.status["processed"] >= 2:
            break
        time.sleep(0.1)
    r.stop()
    assert r.status["processed"] == 2 and len(changes) >= 2
    assert r.status["mail"].startswith("1분마다") and r.status["mail_last_check"]
    assert r.check_mail_now() and col.checked == 1
    rows = r.pipe.storage.feed(source="mail")
    assert {x["subject"] for x in rows} == {"회의 안내", "광고"}


def test_runner_mail_off_reason(tmp_path):
    s = _settings(tmp_path)
    s.mail_account = ""
    r = Runner(s, watch_messenger=False).start()
    time.sleep(0.3)
    r.stop()
    assert r.status["mail"] == "꺼짐" and "메일 주소" in r.status["mail_error"]


def test_feed_item_done_api(tmp_path):
    s = _settings(tmp_path)
    pipe = build_pipeline(s, notify_levels=())
    pipe.handle(mail(1, "교육청 공문 제출 요청", "학교 교육과정 점검표를 금요일까지 제출 바랍니다"))
    pipe.handle(Message(sender="교감", body="긴급 오늘까지 학급 명단 확인 부탁드립니다", source="eztalk", external_id="e1"))
    api = CalendarAPI(pipe.storage, tmp_path / "p.json")
    feed = api.feed("open")
    assert [x["source"] for x in feed] == ["eztalk", "mail"]            # 긴급(쪽지)이 먼저
    assert feed[1]["sender_short"] == "교무부" and feed[1]["subject"] == "교육청 공문 제출 요청"
    assert {x["source"] for x in api.feed("open", "mail")} == {"mail"}
    it = api.item(feed[1]["id"])
    assert it["subject"] == "교육청 공문 제출 요청" and it["school_relevance"] == "학교 업무" and it["done"] is False
    api.set_done(feed[1]["id"], True)
    assert len(api.feed("open")) == 1 and len(api.feed("all")) == 2
    assert api.status()["open_count"] == 1


def test_old_database_is_upgraded(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL, room TEXT DEFAULT '',
        body TEXT NOT NULL, received_at TEXT NOT NULL, source TEXT DEFAULT 'manual', external_id TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now', 'localtime')));
        CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL, importance TEXT NOT NULL,
        category TEXT, summary TEXT, action_items TEXT, deadline TEXT, events TEXT, provider TEXT, model TEXT,
        created_at TEXT DEFAULT (datetime('now', 'localtime')));
        INSERT INTO messages(sender, body, received_at) VALUES ('교감', '예전 쪽지', '2026-11-01T09:00:00');
        INSERT INTO analyses(message_id, importance, category, summary, action_items, events) VALUES (1, '중요', '회의', '예전 요약', '[]', '[]');""")
    con.commit(); con.close()
    st = Storage(str(db))
    (m, a), = st.recent()
    assert a.summary == "예전 요약" and a.school_relevance == "판단 보류" and m.subject == ""
    assert st.feed(since="2026-01-01")[0]["done"] is False

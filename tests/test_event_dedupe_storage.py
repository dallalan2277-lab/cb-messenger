import sqlite3

import pytest

from app.storage import Storage


DATE = "2026-12-12"


@pytest.fixture
def db():
    storage = Storage(":memory:")
    yield storage
    storage.close()


def message(db, number):
    cur = db.conn.execute(
        "INSERT INTO messages(sender, body, received_at, subject) VALUES (?, ?, ?, ?)",
        ("가상 교사", "가상 쪽지", "2026-12-01", str(number)))
    db.conn.commit()
    return cur.lastrowid


def suggest(db, source=None, **kwargs):
    values = {"title": "교직원 회의", "date": DATE, "message_id": source}
    values.update(kwargs)
    return db.suggest_event(**values)


def row(db, event_id):
    result = db.conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
    return dict(result) if result else None


def test_same_sources_and_reanalysis(db):
    m1, m2 = message(db, 1), message(db, 2)
    first = suggest(db, m1)
    second = suggest(db, m2, title="교직원 회의 개최")
    assert second == {"action": "linked", "event_id": first["event_id"]}
    assert suggest(db, m1)["action"] == "skipped"
    sources = db.event_sources(first["event_id"])
    assert [(s["ref_id"], s["relation"]) for s in sources] == [(m1, "origin"), (m2, "same")]
    assert sources[0]["sender"] == "가상 교사" and sources[0]["subject"] == "1"
    assert db.events_for_source("message", m2) == [first["event_id"]]
    assert db.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 1


def test_dismissed_and_empty_fields(db):
    first = suggest(db, status="dismissed")
    assert suggest(db, message(db, 1), start_time="09:00")["action"] == "linked"
    assert row(db, first["event_id"])["status"] == "dismissed"
    assert row(db, first["event_id"])["start_time"] == ""

    other = suggest(db, title="학년 협의회")
    assert suggest(db, title="학년 협의회", start_time="09:00", end_time="10:00",
                   location="가상 회의실")["action"] == "linked"
    filled = row(db, other["event_id"])
    assert (filled["start_time"], filled["end_time"], filled["location"]) == (
        "09:00", "10:00", "가상 회의실")


def test_confirmed_empty_and_changed_time_are_asked(db):
    first = suggest(db, status="confirmed")
    result = suggest(db, message(db, 1), start_time="09:00", status="confirmed")
    assert result["action"] == "asked" and result["dup_of"] == first["event_id"]
    assert row(db, result["event_id"])["status"] == "suggested"
    assert row(db, first["event_id"])["start_time"] == ""
    db.resolve_duplicate(result["event_id"], "separate")
    result = suggest(db, message(db, 2), start_time="10:00", status="confirmed")
    assert result["action"] == "asked"
    assert row(db, result["event_id"])["status"] == "suggested"


def test_multiple_candidates_and_third_source(db):
    first = db.add_calendar_event(title="교직원 회의", date=DATE, start_time="09:00")
    second = db.add_calendar_event(title="교직원 회의", date=DATE, start_time="15:00")
    proposal = suggest(db, message(db, 1))
    assert proposal["action"] == "asked"
    assert set(proposal["candidates"]) == {first, second}
    assert suggest(db, message(db, 2)) == {"action": "linked", "event_id": proposal["event_id"]}
    assert db.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 3


@pytest.mark.parametrize("action", ["replace", "separate", "ignore"])
def test_resolve_duplicate(db, action):
    first = suggest(db, message(db, 1), start_time="09:00", location="가상 교실")
    proposal = suggest(db, message(db, 2), start_time="10:00")
    old_id, new_id = first["event_id"], proposal["event_id"]
    result = db.resolve_duplicate(new_id, action)
    assert result == {"ok": True, "event_id": old_id if action == "replace" else new_id}
    if action == "replace":
        assert row(db, new_id) is None
        assert row(db, old_id)["start_time"] == "10:00"
        assert row(db, old_id)["location"] == "가상 교실"
        assert [s["relation"] for s in db.event_sources(old_id)] == ["origin", "change"]
    elif action == "separate":
        assert row(db, new_id)["dup_of"] is None and row(db, new_id)["status"] == "confirmed"
    else:
        assert row(db, new_id)["dup_of"] == old_id and row(db, new_id)["status"] == "dismissed"
        assert [s["relation"] for s in db.event_sources(old_id)] == ["origin", "ignored"]
    assert not db.resolve_duplicate(99999, action)["ok"]
    assert not db.resolve_duplicate(old_id, action)["ok"]


def test_replace_rolls_back_on_failure(db, monkeypatch):
    first = suggest(db, message(db, 1), start_time="09:00")
    proposal = suggest(db, message(db, 2), start_time="10:00")
    conn = db.conn

    class FailingConnection:
        in_transaction = False

        def __enter__(self):
            conn.__enter__()
            return self

        def __exit__(self, *args):
            return conn.__exit__(*args)

        def execute(self, sql, *args):
            if sql.startswith("DELETE FROM calendar_events"):
                raise RuntimeError("가상 실패")
            return conn.execute(sql, *args)

    monkeypatch.setattr(db, "conn", FailingConnection())
    with pytest.raises(RuntimeError):
        db.resolve_duplicate(proposal["event_id"], "replace")
    monkeypatch.setattr(db, "conn", conn)
    assert row(db, first["event_id"])["start_time"] == "09:00"
    assert row(db, proposal["event_id"])["dup_of"] == first["event_id"]
    assert [s["relation"] for s in db.event_sources(first["event_id"])] == ["origin"]


def test_old_sql_and_foreign_keys(db):
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    cur = db.conn.execute(
        "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, "
        "message_id, memo_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("교직원 회의", DATE, "09:00", "", "", "", "confirmed", "manual", None, None))
    db.conn.commit()
    proposal = suggest(db, start_time="10:00")
    assert proposal["dup_of"] == cur.lastrowid
    assert db.conn.execute("SELECT * FROM calendar_events WHERE id=?", (cur.lastrowid,)).fetchone()
    db.conn.execute("DELETE FROM calendar_events WHERE id=?", (cur.lastrowid,))
    db.conn.commit()
    assert row(db, proposal["event_id"])["dup_of"] is None


def test_migrate_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT NOT NULL, room TEXT DEFAULT '',
          body TEXT NOT NULL, received_at TEXT NOT NULL, source TEXT DEFAULT 'manual',
          external_id TEXT DEFAULT '', created_at TEXT);
        CREATE TABLE calendar_events (id INTEGER PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL,
          start_time TEXT DEFAULT '', end_time TEXT DEFAULT '', location TEXT DEFAULT '', note TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'confirmed', origin TEXT DEFAULT 'manual',
          message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL, created_at TEXT, updated_at TEXT);
        INSERT INTO messages(id, sender, body, received_at) VALUES (1, '가상 교사', '가상 쪽지', '2026-12-01');
        INSERT INTO calendar_events(id, title, date, message_id) VALUES (1, '교직원 회의', '2026-12-12', 1);
    """)
    conn.close()
    db = Storage(str(path))
    try:
        assert row(db, 1)["dup_of"] is None
        assert [(s["kind"], s["ref_id"], s["relation"]) for s in db.event_sources(1)] == [
            ("message", 1, "origin")]
    finally:
        db.close()


def test_delete_memo_and_missing_sources(db):
    memo_id = db.add_memo(title="가상 메모")
    event_id = suggest(db, memo_id=memo_id)["event_id"]
    assert db.events_for_source("memo", memo_id) == [event_id]
    db.delete_memo(memo_id)
    assert db.events_for_source("memo", memo_id) == []
    msg = message(db, 1)
    suggest(db, msg)
    db.conn.execute("DELETE FROM messages WHERE id=?", (msg,))
    db.conn.commit()
    assert db.event_sources(event_id) == []


def test_events_added_by_old_version_get_origin_link_on_reopen(tmp_path):
    """시험판 → 0.9.0 으로 되돌린 동안 생긴 일정도 다시 시험판을 열면 원문 연결이 채워짐"""
    path = str(tmp_path / "t.db")
    first = Storage(path)
    m = message(first, 1)
    first.conn.execute(  # 0.9.0 의 INSERT 그대로
        "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, "
        "message_id, memo_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("교직원 회의", DATE, "", "", "", "", "suggested", "ai", m, None))
    first.conn.commit()
    first.close()
    again = Storage(path)
    eid = again.conn.execute("SELECT id FROM calendar_events").fetchone()[0]
    assert again.events_for_source("message", m) == [eid]
    assert suggest(again, message(again, 2))["action"] == "linked"
    again.close()


def test_replace_revives_dismissed_original(db):
    old = suggest(db, message(db, 1), start_time="14:00")["event_id"]
    db.conn.execute("UPDATE calendar_events SET status='dismissed' WHERE id=?", (old,))
    db.conn.commit()
    asked = suggest(db, message(db, 2), start_time="15:00")
    assert asked["action"] == "asked"
    assert db.resolve_duplicate(asked["event_id"], "replace")["ok"]
    assert row(db, old)["status"] == "confirmed" and row(db, old)["start_time"] == "15:00"


def test_one_message_with_morning_and_afternoon_meetings(db):
    m = message(db, 1)
    assert suggest(db, m, start_time="09:00")["action"] == "created"
    second = suggest(db, m, start_time="15:00")
    assert second["action"] == "asked"
    assert suggest(db, m, start_time="09:00")["action"] == "skipped"


def test_pending_question_gets_new_time(db):
    old = suggest(db, message(db, 1), start_time="09:00", location="본관")["event_id"]
    asked = suggest(db, message(db, 2), location="별관")
    assert asked["action"] == "asked"
    again = suggest(db, message(db, 3), start_time="10:00", location="별관")
    assert again == {"action": "linked", "event_id": asked["event_id"]}
    assert row(db, asked["event_id"])["start_time"] == "10:00"
    assert db.resolve_duplicate(asked["event_id"], "replace")["ok"]
    assert row(db, old)["start_time"] == "10:00" and row(db, old)["location"] == "별관"


def test_failed_duplicate_message_does_not_break_next_suggestion(db):
    from app.models import Message
    m = Message(sender="가상 교사", body="가상 쪽지 본문", received_at="2026-12-01", source="eztalk", external_id="x1")
    assert db.add_message(m)
    m2 = Message(sender="가상 교사", body="가상 쪽지 본문", received_at="2026-12-01", source="eztalk", external_id="x1")
    assert db.add_message(m2) is None
    assert not db.conn.in_transaction
    assert suggest(db, m.id)["action"] == "created"

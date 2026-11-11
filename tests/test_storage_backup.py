"""시험판(0.10)이 예전 DB 를 처음 열 때 한 번만 백업하는지 (가상 자료만)."""
import sqlite3

from app.models import Message
from app.storage import Storage


def _old_db(path):
    """0.9.0 처럼 user_version 0 인 DB 에 가상 쪽지 하나"""
    st = Storage(str(path))
    st.add_message(Message(sender="가상 보낸이", body="가상 본문입니다. 시험용.", received_at="2026-11-01 09:00"))
    st.conn.execute("PRAGMA user_version = 0")
    st.conn.commit()
    st.close()


def test_new_db_has_no_backup_and_gets_version(tmp_path):
    db = tmp_path / "messages.db"
    st = Storage(str(db))
    assert st.conn.execute("PRAGMA user_version").fetchone()[0] == Storage.SCHEMA_VERSION
    st.close()
    assert not Storage.backup_path(str(db)).exists()


def test_old_db_backed_up_once(tmp_path):
    db = tmp_path / "messages.db"
    _old_db(db)
    Storage(str(db)).close()
    bak = Storage.backup_path(str(db))
    assert bak.exists()
    con = sqlite3.connect(str(bak))
    assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()
    first = bak.stat().st_mtime_ns
    # 0.9.0 으로 되돌렸다가(user_version 그대로) 다시 시험판 → 다시 백업하지 않음
    Storage(str(db)).close()
    assert bak.stat().st_mtime_ns == first
    assert not list(bak.parent.glob("*.tmp"))


def test_existing_backup_is_not_overwritten(tmp_path):
    db = tmp_path / "messages.db"
    _old_db(db)
    bak = Storage.backup_path(str(db))
    bak.parent.mkdir(parents=True)
    bak.write_bytes(b"earlier")
    Storage(str(db)).close()
    assert bak.read_bytes() == b"earlier"


def test_backup_failure_does_not_block_open(tmp_path, monkeypatch):
    db = tmp_path / "messages.db"
    _old_db(db)

    def boom(_db_path):
        raise OSError("가상 오류")

    monkeypatch.setattr(Storage, "backup_path", staticmethod(boom))
    st = Storage(str(db))
    assert st.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    # 실패했으니 구조 번호를 남기지 않음 → 다음에 다시 시도
    assert st.conn.execute("PRAGMA user_version").fetchone()[0] == 0
    st.close()
    monkeypatch.undo()
    Storage(str(db)).close()
    assert Storage.backup_path(str(db)).exists()


def test_backup_made_by_other_process_is_kept(tmp_path, monkeypatch):
    """두 프로그램이 동시에 처음 열 때: 먼저 생긴 백업을 덮어쓰지 않고 내 임시 파일만 지움"""
    db = tmp_path / "messages.db"
    _old_db(db)
    bak = Storage.backup_path(str(db))
    real_backup = sqlite3.Connection.backup

    class Conn(sqlite3.Connection):
        def backup(self, target, *a, **k):
            real_backup(self, target, *a, **k)
            bak.write_bytes(b"other process")   # 그 사이 다른 프로그램이 백업을 만듦

    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, **{**k, "factory": Conn}))
    bak.parent.mkdir(parents=True)
    Storage(str(db)).close()
    assert bak.read_bytes() == b"other process"
    assert not list(bak.parent.glob("*.tmp"))

"""SQLite 저장소. 모든 데이터는 내 PC에만 저장됩니다."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .models import Analysis, Event, Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    room TEXT DEFAULT '',
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    external_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_ext
    ON messages(source, external_id) WHERE external_id != '';

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    importance TEXT NOT NULL,
    category TEXT,
    summary TEXT,
    action_items TEXT,      -- JSON 배열
    deadline TEXT,
    events TEXT,            -- JSON 배열
    provider TEXT,
    model TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_analyses_msg ON analyses(message_id);

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    start_time TEXT DEFAULT '',     -- HH:MM (빈 값 = 종일)
    end_time TEXT DEFAULT '',
    location TEXT DEFAULT '',
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',   -- suggested(AI 제안) | confirmed | dismissed
    origin TEXT DEFAULT 'manual',               -- manual | ai
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_cal_date ON calendar_events(date);

CREATE TABLE IF NOT EXISTS mail_cursor (
    account TEXT PRIMARY KEY,
    uidvalidity INTEGER NOT NULL,
    last_uid INTEGER NOT NULL,
    since_date TEXT NOT NULL,          -- 이 날짜(한국 시각 0시) 이후 도착 메일만 수집
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL,          -- analyze | digest
    provider TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


class Storage:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    # 예전 버전 DB 에 새 칸 추가 (데이터는 그대로)
    _NEW_COLUMNS = {
        "messages": [("done", "INTEGER DEFAULT 0"), ("subject", "TEXT DEFAULT ''")],
        "analyses": [("reason", "TEXT DEFAULT ''"), ("evidence", "TEXT DEFAULT ''"),
                     ("school_relevance", "TEXT DEFAULT '판단 보류'"), ("school_reason", "TEXT DEFAULT ''"),
                     ("school_evidence", "TEXT DEFAULT ''"), ("verified", "INTEGER DEFAULT 1"),
                     ("notified", "INTEGER DEFAULT 0")],
        "calendar_events": [("import_batch", "TEXT DEFAULT ''")],   # 파일에서 가져온 일정 묶음 (되돌리기용)
    }

    def _migrate(self):
        for table, cols in self._NEW_COLUMNS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- messages ----
    def add_message(self, m: Message) -> int | None:
        """저장 후 id 반환. 이미 있는 메시지(external_id 중복)면 None."""
        try:
            cur = self.conn.execute(
                "INSERT INTO messages(sender, room, body, received_at, source, external_id, subject) "
                "VALUES (?,?,?,?,?,?,?)",
                (m.sender, m.room, m.body, m.received_at, m.source, m.external_id, m.subject),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            return None
        m.id = cur.lastrowid
        return m.id

    # ---- 다시 열어 본 옛 쪽지 알아보기 ----
    SIMILAR_MIN_CHARS = 15     # 이보다 짧은 본문("넵", "감사합니다")은 진짜로 또 올 수 있어 기간 제한 없이 비교하지 않음

    @staticmethod
    def body_key(body: str) -> str:
        """비교용 본문: [제목] 줄·공백·끝의 말줄임표를 없앰 (알림 창은 본문이 잘려서 '…' 로 끝남)"""
        lines = [ln for ln in (body or "").splitlines() if not ln.startswith("[제목] ")]
        return re.sub(r"\s+", "", "".join(lines)).rstrip("….·")

    def find_similar_message(self, source: str, sender: str, body: str, limit: int = 300) -> dict | None:
        """같은 곳·같은 보낸 사람의 저장된 메시지 중 본문이 같거나 한쪽이 다른 쪽의 앞부분인 것 (최근 것부터).
        예전 쪽지를 메신저에서 다시 열었을 때 새 쪽지로 또 분석하지 않으려고 씀."""
        key = self.body_key(body)
        if len(key) < self.SIMILAR_MIN_CHARS:
            return None
        rows = self.conn.execute(
            "SELECT id, body FROM messages WHERE source = ? AND sender = ? ORDER BY id DESC LIMIT ?",
            (source, sender, limit)).fetchall()
        for r in rows:
            old = self.body_key(r["body"])
            if len(old) >= self.SIMILAR_MIN_CHARS and (old.startswith(key) or key.startswith(old)):
                return {"id": r["id"], "body": r["body"]}
        return None

    def update_message_body(self, message_id: int, body: str) -> bool:
        cur = self.conn.execute("UPDATE messages SET body = ? WHERE id = ?", (body, int(message_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def unanalyzed_messages(self, limit: int = 50) -> list[Message]:
        rows = self.conn.execute(
            "SELECT m.* FROM messages m LEFT JOIN analyses a ON a.message_id = m.id "
            "WHERE a.id IS NULL ORDER BY m.id LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    # ---- analyses ----
    def add_analysis(self, a: Analysis) -> int:
        cur = self.conn.execute(
            "INSERT INTO analyses(message_id, importance, category, summary, action_items, "
            "deadline, events, provider, model, reason, evidence, school_relevance, school_reason, "
            "school_evidence, verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.message_id, a.importance, a.category, a.summary,
             json.dumps(a.action_items, ensure_ascii=False), a.deadline,
             json.dumps([e.__dict__ for e in a.events], ensure_ascii=False),
             a.provider, a.model, a.reason, a.evidence, a.school_relevance, a.school_reason,
             a.school_evidence, int(a.verified)),
        )
        self.conn.commit()
        return cur.lastrowid

    def recent(self, since: str | None = None, limit: int = 100) -> list[tuple[Message, Analysis]]:
        """최근 분석 결과. since 는 'YYYY-MM-DD' 또는 'YYYY-MM-DDTHH:MM:SS'."""
        sql = ("SELECT m.*, a.importance, a.category, a.summary, a.action_items, a.deadline, "
               "a.events, a.provider, a.model, a.reason, a.evidence, a.school_relevance, a.school_reason, "
               "a.school_evidence, a.verified FROM messages m JOIN analyses a ON a.message_id = m.id ")
        params: list = []
        if since:
            sql += "WHERE m.received_at >= ? "
            params.append(since)
        sql += ("ORDER BY CASE a.importance WHEN '긴급' THEN 0 WHEN '중요' THEN 1 ELSE 2 END, "
                "m.received_at DESC LIMIT ?")
        params.append(limit)
        out = []
        for r in self.conn.execute(sql, params).fetchall():
            m = self._row_to_msg(r)
            a = Analysis(
                message_id=m.id, importance=r["importance"], category=r["category"] or "",
                summary=r["summary"] or "", action_items=json.loads(r["action_items"] or "[]"),
                deadline=r["deadline"] or "",
                events=[Event(**e) for e in json.loads(r["events"] or "[]")],
                provider=r["provider"] or "", model=r["model"] or "",
                reason=r["reason"] or "", evidence=r["evidence"] or "",
                school_relevance=r["school_relevance"] or "판단 보류", school_reason=r["school_reason"] or "",
                school_evidence=r["school_evidence"] or "", verified=bool(r["verified"]),
            )
            out.append((m, a))
        return out

    # ---- calendar ----
    CAL_FIELDS = ("title", "date", "start_time", "end_time", "location", "note", "status")

    def add_calendar_event(self, *, title: str, date: str, start_time: str = "", end_time: str = "",
                           location: str = "", note: str = "", status: str = "confirmed",
                           origin: str = "manual", message_id: int | None = None) -> int | None:
        """일정 추가. AI 제안은 같은 날·같은 제목·같은 시작 시각이 이미 있으면 건너뜀(None)."""
        if origin == "ai":
            dup = self.conn.execute(
                "SELECT id FROM calendar_events WHERE date=? AND title=? AND start_time=?",
                (date, title, start_time)).fetchone()
            if dup:
                return None
        cur = self.conn.execute(
            "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, message_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (title, date, start_time, end_time, location, note, status, origin, message_id))
        self.conn.commit()
        return cur.lastrowid

    # ---- 파일에서 일정 가져오기 ----
    def import_calendar_events(self, events: list[dict], batch: str) -> tuple[int, int]:
        """확정 일정으로 한꺼번에 넣음. 같은 날·같은 제목·같은 시작 시각이 이미 있으면 건너뜀.
        (넣은 수, 건너뛴 수). 한 번에 처리해서 중간에 실패하면 하나도 안 들어감."""
        added = skipped = 0
        with self.conn:
            for e in events:
                key = (e["date"], e["title"], e.get("start_time", ""))
                if self.conn.execute("SELECT 1 FROM calendar_events WHERE date=? AND title=? AND start_time=? "
                                     "AND status != 'dismissed'", key).fetchone():
                    skipped += 1
                    continue
                self.conn.execute(
                    "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, "
                    "import_batch) VALUES (?,?,?,?,?,?,'confirmed','import',?)",
                    (e["title"], e["date"], e.get("start_time", ""), e.get("end_time", ""),
                     e.get("location", ""), e.get("note", ""), batch))
                added += 1
        return added, skipped

    def existing_event_keys(self, start: str, end: str) -> set:
        rows = self.conn.execute("SELECT date, title, start_time FROM calendar_events WHERE date BETWEEN ? AND ? "
                                 "AND status != 'dismissed'", (start, end)).fetchall()
        return {(r["date"], r["title"], r["start_time"] or "") for r in rows}

    def delete_import_batch(self, batch: str) -> int:
        if not batch:
            return 0
        cur = self.conn.execute("DELETE FROM calendar_events WHERE import_batch = ? AND origin = 'import'", (batch,))
        self.conn.commit()
        return cur.rowcount

    def update_calendar_event(self, event_id: int, **fields) -> bool:
        sets = {k: v for k, v in fields.items() if k in self.CAL_FIELDS}
        if not sets:
            return False
        sql = ", ".join(f"{k}=?" for k in sets) + ", updated_at=datetime('now','localtime')"
        cur = self.conn.execute(f"UPDATE calendar_events SET {sql} WHERE id=?", (*sets.values(), event_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_calendar_event(self, event_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def calendar_events(self, start: str, end: str, include_dismissed: bool = False) -> list[dict]:
        """start~end(YYYY-MM-DD, 포함) 일정. 메시지에서 온 일정은 보낸 사람도 같이."""
        sql = ("SELECT e.*, m.sender AS msg_sender FROM calendar_events e "
               "LEFT JOIN messages m ON m.id = e.message_id WHERE e.date BETWEEN ? AND ? ")
        if not include_dismissed:
            sql += "AND e.status != 'dismissed' "
        sql += "ORDER BY e.date, CASE WHEN e.start_time='' THEN 0 ELSE 1 END, e.start_time, e.id"
        return [dict(r) for r in self.conn.execute(sql, (start, end)).fetchall()]

    def get_message(self, message_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT m.*, a.summary, a.importance, a.category, a.reason, a.evidence, a.action_items, a.deadline, "
            "a.school_relevance, a.school_reason, a.school_evidence, a.verified "
            "FROM messages m LEFT JOIN analyses a ON a.message_id=m.id "
            "WHERE m.id=? ORDER BY a.id DESC LIMIT 1", (message_id,)).fetchone()
        return dict(r) if r else None

    # ---- mail cursor (마지막으로 본 메일 위치) ----
    def get_mail_cursor(self, account: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM mail_cursor WHERE account=?", (account.lower(),)).fetchone()
        return dict(r) if r else None

    def set_mail_cursor(self, account: str, uidvalidity: int, last_uid: int, since_date: str) -> None:
        self.conn.execute(
            "INSERT INTO mail_cursor(account, uidvalidity, last_uid, since_date) VALUES (?,?,?,?) "
            "ON CONFLICT(account) DO UPDATE SET uidvalidity=excluded.uidvalidity, last_uid=excluded.last_uid, "
            "since_date=excluded.since_date, updated_at=datetime('now','localtime')",
            (account.lower(), int(uidvalidity), int(last_uid), since_date))
        self.conn.commit()

    def reset_mail_cursor(self, account: str) -> None:
        self.conn.execute("DELETE FROM mail_cursor WHERE account=?", (account.lower(),))
        self.conn.commit()

    # ---- usage ----
    def add_usage(self, purpose: str, provider: str, model: str,
                  input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO usage(purpose, provider, model, input_tokens, output_tokens) VALUES (?,?,?,?,?)",
            (purpose, provider, model, int(input_tokens or 0), int(output_tokens or 0)))
        self.conn.commit()

    def usage_summary(self, since: str) -> list[dict]:
        """since('YYYY-MM-DD') 이후 모델별 호출 수·토큰 합계."""
        rows = self.conn.execute(
            "SELECT provider, model, purpose, COUNT(*) AS calls, SUM(input_tokens) AS inp, "
            "SUM(output_tokens) AS outp FROM usage WHERE created_at >= ? "
            "GROUP BY provider, model, purpose ORDER BY calls DESC", (since,)).fetchall()
        return [dict(r) for r in rows]

    # ---- 받은 소식 (캘린더 옆 통합 목록) ----
    def feed(self, *, source: str = "", only_open: bool = True, school_only: bool = False,
             limit: int = 100, since: str = "") -> list[dict]:
        sql = ("SELECT m.id, m.sender, m.room, m.received_at, m.source, m.subject, m.done, "
               "a.importance, a.category, a.summary, a.action_items, a.deadline, a.school_relevance "
               "FROM messages m JOIN analyses a ON a.id = (SELECT MAX(id) FROM analyses WHERE message_id = m.id) WHERE 1=1 ")
        params: list = []
        if source:
            sql += "AND m.source = ? "
            params.append(source)
        if only_open:
            sql += "AND m.done = 0 "
        if school_only:
            sql += "AND a.school_relevance = '학교 업무' "
        if since:
            sql += "AND m.received_at >= ? "
            params.append(since)
        sql += ("ORDER BY m.done, CASE a.importance WHEN '긴급' THEN 0 WHEN '중요' THEN 1 ELSE 2 END, "
                "m.received_at DESC LIMIT ?")
        params.append(limit)
        out = []
        for r in self.conn.execute(sql, params).fetchall():
            d = dict(r)
            d["action_items"] = json.loads(d["action_items"] or "[]")
            d["done"] = bool(d["done"])
            out.append(d)
        return out

    def set_done(self, message_id: int, done: bool = True) -> bool:
        cur = self.conn.execute("UPDATE messages SET done=? WHERE id=?", (int(done), message_id))
        self.conn.commit()
        return cur.rowcount > 0

    def is_done(self, message_id: int) -> bool:
        r = self.conn.execute("SELECT done FROM messages WHERE id=?", (message_id,)).fetchone()
        return bool(r and r["done"])

    @staticmethod
    def _row_to_msg(r: sqlite3.Row) -> Message:
        keys = r.keys()
        return Message(id=r["id"], sender=r["sender"], room=r["room"], body=r["body"],
                       received_at=r["received_at"], source=r["source"], external_id=r["external_id"],
                       subject=(r["subject"] if "subject" in keys else "") or "")

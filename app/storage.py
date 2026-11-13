"""SQLite 저장소. 모든 데이터는 내 PC에만 저장됩니다."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path

from .models import Analysis, Event, Message
from .event_match import (best_matches, cancel_positions, classify, dates_in_text, has_any, moved_dates, title_key,
                          CHANGE_WORDS, CANCEL_WORDS, _compact, _text)

log = logging.getLogger(__name__)

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

CREATE TABLE IF NOT EXISTS event_sources (
    event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    relation TEXT NOT NULL DEFAULT 'same',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(event_id, kind, ref_id)
);
CREATE INDEX IF NOT EXISTS ix_event_sources_ref ON event_sources(kind, ref_id);

CREATE TABLE IF NOT EXISTS memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '기타',
    kind_by_user INTEGER DEFAULT 0,
    read_mode TEXT NOT NULL DEFAULT 'ocr',
    source_text TEXT DEFAULT '',
    image_path TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    details TEXT DEFAULT '[]',
    items TEXT DEFAULT '[]',
    action_items TEXT DEFAULT '[]',
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    status TEXT DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS mail_cursor (
    account TEXT PRIMARY KEY,
    uidvalidity INTEGER NOT NULL,
    last_uid INTEGER NOT NULL,
    since_date TEXT NOT NULL,          -- 이 날짜(한국 시각 0시) 이후 도착 메일만 수집
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 오늘 할 일 순서 (하루 2번 정리). slot: morning | noon | manual
CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    slot TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'running',     -- running | done
    claimed_at TEXT,
    finished_at TEXT,
    source TEXT DEFAULT '',                    -- rule | ai
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    data TEXT DEFAULT '{}',
    notified INTEGER DEFAULT 0,
    UNIQUE(plan_date, slot)
);

-- 할 일 상태: kind message|event, state done|snoozed(until 날짜까지 숨김). 소식의 완료는 messages.done 을 씀
CREATE TABLE IF NOT EXISTS todo_state (
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    until TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY(kind, ref_id)
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL,          -- analyze | digest | memo
    provider TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


class Storage:
    # DB 구조 번호 (PRAGMA user_version). 0.9.0 이하는 0. 0.10 시험판부터 10.
    # 0.9.0 은 이 값을 건드리지 않으므로, 시험판 → 0.9.0 → 시험판으로 오가도 백업은 처음 한 번만.
    SCHEMA_VERSION = 10

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        existed = db_path != ":memory:" and os.path.isfile(db_path) and os.path.getsize(db_path) > 0
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        backup_ok = True
        if existed:
            backup_ok = self._backup_before_upgrade(db_path)   # 구조를 바꾸기(새 표 포함) 전에
        self.conn.executescript(SCHEMA)
        self._migrate()
        # 백업이 실패했으면 구조 번호를 남기지 않음 → 다음에 켤 때 다시 백업을 시도
        # (새 칸·새 표는 추가만 하므로, 그때 백업되는 DB 도 0.9.0 에서 그대로 열림)
        if backup_ok and self.conn.execute("PRAGMA user_version").fetchone()[0] < self.SCHEMA_VERSION:
            self.conn.execute(f"PRAGMA user_version = {int(self.SCHEMA_VERSION)}")
            self.conn.commit()

    @staticmethod
    def backup_path(db_path: str) -> Path:
        p = Path(db_path)
        return p.parent / "backup" / f"{p.stem}-before-0.10{p.suffix or '.db'}"

    def _backup_before_upgrade(self, db_path: str) -> bool:
        """예전(0.9.0 이하) DB 를 시험판 구조로 바꾸기 직전에 한 번만 통째로 백업.
        SQLite 백업 기능으로 임시 파일에 쓴 뒤 이름을 바꿔서, 도중에 꺼져도 반쪽 백업이 남지 않게 함.
        두 프로그램이 동시에 처음 열어도 먼저 만든 백업을 덮어쓰지 않음 (임시 파일 이름을 프로세스마다 다르게,
        이미 있으면 버림). 실패해도 프로그램은 그대로 열림 (로그만). 백업이 있거나 필요 없으면 True."""
        tmp = None
        try:
            if self.conn.execute("PRAGMA user_version").fetchone()[0] >= self.SCHEMA_VERSION:
                return True
            dest = self.backup_path(db_path)
            if dest.exists():
                return True   # 처음 백업을 덮어쓰지 않음
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
            if tmp.exists():
                tmp.unlink()
            out = sqlite3.connect(str(tmp))
            try:
                self.conn.backup(out)
            finally:
                out.close()
            try:
                os.link(tmp, dest)          # 이미 있으면 실패 (덮어쓰지 않음), 윈도·리눅스 모두
            except FileExistsError:
                pass                        # 다른 프로그램이 먼저 만듦 → 그것을 둠
            except OSError:
                if dest.exists():
                    raise
                os.rename(tmp, dest)        # 하드 링크를 못 쓰는 드라이브
                tmp = None
            if tmp is not None:
                tmp.unlink()
            log.info("DB 구조를 바꾸기 전에 백업했습니다: %s", dest)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("DB 백업을 만들지 못했습니다 (프로그램은 계속): %s", e)
            try:
                if tmp is not None and tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return False

    # 예전 버전 DB 에 새 칸 추가 (데이터는 그대로)
    _NEW_COLUMNS = {
        "messages": [("done", "INTEGER DEFAULT 0"), ("subject", "TEXT DEFAULT ''")],
        "analyses": [("reason", "TEXT DEFAULT ''"), ("evidence", "TEXT DEFAULT ''"),
                     ("school_relevance", "TEXT DEFAULT '판단 보류'"), ("school_reason", "TEXT DEFAULT ''"),
                     ("school_evidence", "TEXT DEFAULT ''"), ("verified", "INTEGER DEFAULT 1"),
                     ("notified", "INTEGER DEFAULT 0"), ("details", "TEXT DEFAULT '[]'")],
        "calendar_events": [("import_batch", "TEXT DEFAULT ''"),   # 파일에서 가져온 일정 묶음 (되돌리기용)
                            ("memo_id", "INTEGER REFERENCES memos(id) ON DELETE SET NULL"),
                            ("dup_of", "INTEGER REFERENCES calendar_events(id) ON DELETE SET NULL"),
                            ("dup_kind", "TEXT DEFAULT ''")],
    }

    def _migrate(self):
        for table, cols in self._NEW_COLUMNS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        # 처음 원문(message_id·memo_id)을 원문 연결 표에도 채움. 매번 실행해서
        # 0.9.0 으로 되돌렸던 동안 생긴 일정도 다시 시험판을 열 때 채워짐 (이미 있으면 그대로)
        for kind, col in (("message", "message_id"), ("memo", "memo_id")):
            self.conn.execute(
                "INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                f"SELECT id, '{kind}', {col}, 'origin' FROM calendar_events WHERE {col} IS NOT NULL")
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
            self.conn.rollback()   # 실패한 INSERT 의 트랜잭션을 닫아 둠 (다음 BEGIN IMMEDIATE 가 막히지 않게)
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
            "school_evidence, verified, details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.message_id, a.importance, a.category, a.summary,
             json.dumps(a.action_items, ensure_ascii=False), a.deadline,
             json.dumps([e.__dict__ for e in a.events], ensure_ascii=False),
             a.provider, a.model, a.reason, a.evidence, a.school_relevance, a.school_reason,
             a.school_evidence, int(a.verified), json.dumps(a.details or [], ensure_ascii=False)),
        )
        self.conn.commit()
        return cur.lastrowid

    def delete_older_analyses(self, message_id: int, keep_id: int) -> int:
        """다시 분석한 뒤 예전 분석 결과를 지움 (새 결과 keep_id 만 남김)"""
        cur = self.conn.execute("DELETE FROM analyses WHERE message_id = ? AND id != ?", (message_id, keep_id))
        self.conn.commit()
        return cur.rowcount

    def message_obj(self, message_id: int):
        r = self.conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)).fetchone()
        return self._row_to_msg(r) if r else None

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
                           origin: str = "manual", message_id: int | None = None,
                           memo_id: int | None = None) -> int | None:
        """Schedule an event; generated suggestions with the same key are skipped."""
        if origin in ("ai", "memo"):
            dup = self.conn.execute(
                "SELECT id FROM calendar_events WHERE date=? AND title=? AND start_time=?",
                (date, title, start_time)).fetchone()
            if dup:
                return None
        cur = self.conn.execute(
            "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, "
            "message_id, memo_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, date, start_time, end_time, location, note, status, origin, message_id, memo_id))
        self.conn.commit()
        return cur.lastrowid

    def suggest_event(self, *, title: str, date: str, start_time: str = "", end_time: str = "",
                      location: str = "", note: str = "", status: str = "suggested",
                      origin: str = "ai", message_id: int | None = None,
                      memo_id: int | None = None, source_text: str = "",
                      source_date: str = "") -> dict:
        new = dict(title=title, date=date, start_time=start_time, end_time=end_time, location=location)
        source = (("message", message_id) if message_id is not None else
                  ("memo", memo_id) if memo_id is not None else None)

        def link(event_id, relation):
            if source is not None:
                self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                                  "VALUES (?, ?, ?, ?)", (event_id, *source, relation))

        if self.conn.in_transaction:
            self.conn.commit()   # 이 연결에 남은 작업이 있으면 먼저 확정 (중첩 BEGIN 오류·남의 작업 되돌리기 방지)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if source is not None:
                rows = self.conn.execute(
                    "SELECT e.* FROM calendar_events e JOIN event_sources s ON s.event_id=e.id "
                    "WHERE s.kind=? AND s.ref_id=? AND e.date=? ORDER BY e.id", (*source, date))
                for row in rows:
                    # 같은 원문의 '같은 일'만 건너뜀 (한 쪽지에 09시·15시 회의가 둘 다 있으면 둘째는 묻기로)
                    if classify(new, dict(row))[0] == "same":
                        return {"action": "skipped", "event_id": row["id"]}

            rows = [dict(row) for row in self.conn.execute(
                "SELECT * FROM calendar_events WHERE date=? ORDER BY id", (date,))]
            matches = best_matches(new, [row for row in rows if row["dup_of"] is None])
            for row in rows:
                if row["dup_of"] is not None and classify(new, row)[0] == "same":
                    # 묻는 중인 제안에 붙일 때도 새로 알게 된 시각·장소는 채움 (나중에 [바꾸기]에 반영되게)
                    fill = {key: new[key] for key in ("start_time", "end_time", "location")
                            if not row[key] and new[key]}
                    if fill and row["status"] == "suggested":
                        self.conn.execute("UPDATE calendar_events SET " + ", ".join(k + "=?" for k in fill) +
                                          ", updated_at=datetime('now','localtime') WHERE id=?",
                                          (*fill.values(), row["id"]))
                    link(row["id"], "same")
                    return {"action": "linked", "event_id": row["id"]}

            if len(matches) == 1 and matches[0][1] == "same":
                old = matches[0][0]
                fill = {key: new[key] for key in ("start_time", "end_time", "location")
                        if not old[key] and new[key]}
                if old["status"] != "confirmed" or not fill:
                    if old["status"] == "suggested" and fill:
                        assignments = ", ".join(key + "=?" for key in fill)
                        self.conn.execute("UPDATE calendar_events SET " + assignments +
                                          ", updated_at=datetime('now','localtime') WHERE id=?",
                                          (*fill.values(), old["id"]))
                    link(old["id"], "same")
                    return {"action": "linked", "event_id": old["id"]}

            dup_of = matches[0][0]["id"] if matches else None
            dup_kind = ""
            if not matches and source_text and has_any(source_text, CHANGE_WORDS):
                # '19일에서 20일로' 처럼 옛 날짜 → 새 날짜로 잇는 표현이 있을 때만 (날짜 둘이 따로 나온 것은 아님)
                pairs = moved_dates(source_text, source_date or date)
                candidates = []
                for old_date in sorted({a for a, b in pairs if b == date}):
                    for old_row in self.conn.execute(
                            "SELECT * FROM calendar_events WHERE date=? AND status IN ('suggested','confirmed') "
                            "AND dup_of IS NULL ORDER BY id", (old_date,)):
                        old = dict(old_row)
                        adjusted = {**new, "date": old_date, "start_time": "", "location": ""}
                        if classify(adjusted, {**old, "start_time": "", "location": ""})[0] in ("same", "maybe"):
                            candidates.append(old["id"])
                if len(candidates) == 1:
                    dup_of, dup_kind = candidates[0], "change"
            cur = self.conn.execute(
                "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, "
                "message_id, memo_id, dup_of, dup_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (title, date, start_time, end_time, location, note,
                 "suggested" if dup_of is not None else status, origin, message_id, memo_id, dup_of, dup_kind))
            event_id = cur.lastrowid
            link(event_id, "origin")
            if dup_of is not None:
                return {"action": "asked", "event_id": event_id, "dup_of": dup_of,
                        "candidates": [match[0]["id"] for match in matches] if matches else [dup_of]}
            return {"action": "created", "event_id": event_id}

    def restore_event(self, event_id: int, status: str) -> bool:
        """[일정 취소]를 되돌림: 무시(dismissed)로 돌렸던 일정을 이전 상태(제안·확정)로"""
        if status not in ("suggested", "confirmed"):
            return False
        cur = self.conn.execute("UPDATE calendar_events SET status=?, updated_at=datetime('now','localtime') "
                                "WHERE id=? AND status='dismissed'", (status, int(event_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def get_calendar_event(self, event_id) -> dict | None:
        if event_id is None:
            return None
        r = self.conn.execute("SELECT * FROM calendar_events WHERE id=?", (int(event_id),)).fetchone()
        return dict(r) if r else None

    def detect_cancellation(self, *, message_id: int, text: str, ref_date: str) -> dict | None:
        if not has_any(text, CANCEL_WORDS):
            return None
        dates = dates_in_text(text, ref_date)
        if not dates:
            return None
        body = _compact(_text(text))
        cancels = cancel_positions(body)   # '신청 취소'·'취소하지 않습니다' 등은 빠져 있음
        if not cancels:
            return None

        def near_cancel(key):
            # 일정 이름 바로 뒤(또는 앞) 30글자 안에 취소 표현이 있어야 그 일정의 취소로 봄
            start = body.find(key)
            while start >= 0:
                end = start + len(key)
                if any(end - 5 <= c <= end + 30 or start - 30 <= c < start for c in cancels):
                    return True
                start = body.find(key, start + 1)
            return False

        if self.conn.in_transaction:
            self.conn.commit()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            rows = self.conn.execute(
                "SELECT * FROM calendar_events e WHERE status IN ('suggested','confirmed') "
                "AND dup_of IS NULL AND NOT EXISTS (SELECT 1 FROM event_sources s "
                "WHERE s.event_id=e.id AND s.kind='message' AND s.ref_id=?)", (message_id,))
            candidates = [row for row in rows if row["date"] in dates
                          and len(title_key(row["title"])) >= 3 and near_cancel(title_key(row["title"]))]
            if len(candidates) != 1:
                return None
            old = candidates[0]
            existing = self.conn.execute(
                "SELECT id FROM calendar_events WHERE dup_of=? AND dup_kind='cancel' ORDER BY id LIMIT 1",
                (old["id"],)).fetchone()
            if existing:
                event_id = existing["id"]
                action = "linked"
            else:
                cur = self.conn.execute(
                    "INSERT INTO calendar_events(title,date,start_time,status,origin,message_id,dup_of,dup_kind,note) "
                    "VALUES (?,?,?,'suggested','ai',?,?,'cancel',?)",
                    (f"(취소?) {old['title']}", old["date"], old["start_time"], message_id, old["id"],
                     "취소 안내로 보여요"))
                event_id = cur.lastrowid
                action = "asked"
            self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id,kind,ref_id,relation) "
                              "VALUES (?,'message',?,'origin')", (event_id, message_id))
            return {"action": action, "event_id": event_id, "dup_of": old["id"]}

    def add_event_from_user(self, event: dict, *, memo_id: int) -> dict:
        """메모에서 직접 넣은 일정과 관련 제안을 한 트랜잭션으로 처리한다."""
        if self.conn.in_transaction:
            self.conn.commit()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            rows = [dict(row) for row in self.conn.execute(
                "SELECT * FROM calendar_events WHERE date=? AND dup_of IS NULL ORDER BY id",
                (event["date"],))]
            matches = [(row, classify(event, row)[0]) for row in rows]
            same = [row for row, kind in matches if kind == "same"]
            if len(same) == 1:
                old = same[0]
                self.conn.execute(
                    "UPDATE calendar_events SET status='confirmed', end_time=?, note=?, memo_id=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (event.get("end_time") or old["end_time"],
                     old["note"] if (old["note"] or "").strip() else event.get("note", ""),
                     old["memo_id"] or memo_id, old["id"]))
                self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                                  "VALUES (?, 'memo', ?, 'same')", (old["id"], memo_id))
                return {"id": old["id"], "existed": True}

            cur = self.conn.execute(
                "INSERT INTO calendar_events(title, date, start_time, end_time, location, note, status, origin, memo_id) "
                "VALUES (?, ?, ?, ?, ?, ?, 'confirmed', 'memo', ?)",
                (event["title"], event["date"], event.get("start_time", ""), event.get("end_time", ""),
                 event.get("location", ""), event.get("note", ""), memo_id))
            event_id = cur.lastrowid
            self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                              "VALUES (?, 'memo', ?, 'origin')", (event_id, memo_id))
            targeted = {r[0] for r in self.conn.execute(
                "SELECT DISTINCT dup_of FROM calendar_events WHERE dup_of IS NOT NULL")}
            # 이미 다른 묻기 제안이 가리키는 제안은 건드리지 않음 (dup_of 는 한 단계만)
            related = [row["id"] for row, kind in matches if kind in ("same", "maybe")
                       and row["status"] == "suggested" and row["origin"] in ("ai", "memo")
                       and row["id"] not in targeted]
            for related_id in related:
                self.conn.execute("UPDATE calendar_events SET dup_of=?, updated_at=datetime('now','localtime') "
                                  "WHERE id=?", (event_id, related_id))
            return {"id": event_id, "existed": False, "related": related}

    def resolve_duplicate(self, event_id: int, action: str) -> dict:
        if self.conn.in_transaction:
            self.conn.commit()   # 이 연결에 남은 작업이 있으면 먼저 확정 (중첩 BEGIN 오류·남의 작업 되돌리기 방지)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            if not row or row["dup_of"] is None:
                return {"ok": False, "error": "확인할 중복 제안을 찾을 수 없어요."}
            original = row["dup_of"]
            if not self.conn.execute("SELECT 1 FROM calendar_events WHERE id=?", (original,)).fetchone():
                return {"ok": False, "error": "기존 일정을 찾을 수 없어요."}
            if row["dup_kind"] == "cancel":
                if action == "cancel_event":
                    prev = self.conn.execute("SELECT status FROM calendar_events WHERE id=?", (original,)).fetchone()[0]
                    self.conn.execute("UPDATE calendar_events SET status='dismissed', "
                                      "updated_at=datetime('now','localtime') WHERE id=?", (original,))
                    self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                                      "SELECT ?, kind, ref_id, 'change' FROM event_sources WHERE event_id=?",
                                      (original, event_id))
                    self.conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
                    return {"ok": True, "event_id": original, "prev_status": prev}   # 화면의 [되돌리기]용
                if action == "keep":
                    self.conn.execute("UPDATE calendar_events SET status='dismissed', "
                                      "updated_at=datetime('now','localtime') WHERE id=?", (event_id,))
                    return {"ok": True, "event_id": event_id}
                return {"ok": False, "error": "취소 안내는 일정 취소 또는 그대로 두기를 골라 주세요."}
            if action == "replace":
                fields = {key: row[key] for key in ("title", "date", "start_time", "end_time", "location")
                          if row[key]}
                old_status = self.conn.execute("SELECT status FROM calendar_events WHERE id=?",
                                               (original,)).fetchone()[0]
                if old_status == "dismissed":
                    fields["status"] = "confirmed"   # 무시했던 일정이라도 사람이 [바꾸기]를 눌렀으면 살림
                assignments = ", ".join(key + "=?" for key in fields) or "id=id"
                self.conn.execute("UPDATE calendar_events SET " + assignments +
                                  ", updated_at=datetime('now','localtime') WHERE id=?",
                                  (*fields.values(), original))
                self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                                  "SELECT ?, kind, ref_id, 'change' FROM event_sources WHERE event_id=?",
                                  (original, event_id))
                self.conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
                return {"ok": True, "event_id": original}
            if action == "separate":
                self.conn.execute("UPDATE calendar_events SET dup_of=NULL, status='confirmed', "
                                  "updated_at=datetime('now','localtime') WHERE id=?", (event_id,))
            elif action == "ignore":
                self.conn.execute("UPDATE calendar_events SET status='dismissed', "
                                  "updated_at=datetime('now','localtime') WHERE id=?", (event_id,))
                self.conn.execute("INSERT OR IGNORE INTO event_sources(event_id, kind, ref_id, relation) "
                                  "SELECT ?, kind, ref_id, 'ignored' FROM event_sources WHERE event_id=?",
                                  (original, event_id))
            else:
                return {"ok": False, "error": "처리 방법을 확인해 주세요."}
            return {"ok": True, "event_id": event_id}

    def event_sources(self, event_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.kind, s.ref_id, s.relation, s.created_at, m.sender, m.received_at, m.subject "
            "FROM event_sources s LEFT JOIN messages m ON s.kind='message' AND m.id=s.ref_id "
            "LEFT JOIN memos n ON s.kind='memo' AND n.id=s.ref_id "
            "WHERE s.event_id=? AND (m.id IS NOT NULL OR n.id IS NOT NULL) ORDER BY s.rowid", (event_id,))
        return [dict(row) for row in rows]

    def events_for_source(self, kind: str, ref_id: int) -> list[int]:
        return [row[0] for row in self.conn.execute(
            "SELECT event_id FROM event_sources WHERE kind=? AND ref_id=? ORDER BY event_id", (kind, ref_id))]

    # ---- memos ----
    MEMO_FIELDS = ("title", "kind", "kind_by_user", "read_mode", "source_text", "image_path",
                   "summary", "details", "items", "action_items", "provider", "model", "status")
    MEMO_JSON_FIELDS = frozenset(("details", "items", "action_items"))

    @classmethod
    def _memo_values(cls, fields: dict) -> dict:
        values = {key: value for key, value in fields.items() if key in cls.MEMO_FIELDS}
        for key in cls.MEMO_JSON_FIELDS & values.keys():
            value = values[key]
            if not isinstance(value, str):
                values[key] = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
        if "kind_by_user" in values:
            values["kind_by_user"] = int(bool(values["kind_by_user"]))
        return values

    @classmethod
    def _memo_row(cls, row: sqlite3.Row) -> dict:
        memo = dict(row)
        for key in cls.MEMO_JSON_FIELDS:
            try:
                value = json.loads(memo.get(key) or "[]")
            except (TypeError, ValueError):
                value = []
            memo[key] = value if isinstance(value, list) else []
        memo["kind_by_user"] = bool(memo.get("kind_by_user"))
        return memo

    def add_memo(self, **fields) -> int:
        values = self._memo_values(fields)
        if values:
            columns = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            cur = self.conn.execute(
                f"INSERT INTO memos({columns}) VALUES ({marks})", tuple(values.values()))
        else:
            cur = self.conn.execute("INSERT INTO memos DEFAULT VALUES")
        self.conn.commit()
        return cur.lastrowid

    def update_memo(self, memo_id: int, **fields) -> bool:
        values = self._memo_values(fields)
        if not values:
            return False
        assignments = ", ".join(f"{key}=?" for key in values)
        cur = self.conn.execute(
            f"UPDATE memos SET {assignments}, updated_at=datetime('now','localtime') WHERE id=?",
            (*values.values(), int(memo_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def get_memo(self, memo_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM memos WHERE id=?", (int(memo_id),)).fetchone()
        return self._memo_row(row) if row else None

    def list_memos(self, limit: int = 100) -> list[dict]:
        safe_limit = max(0, min(int(limit), 1000))
        rows = self.conn.execute(
            "SELECT m.*, COUNT(e.id) AS event_count FROM memos m "
            "LEFT JOIN calendar_events e ON e.memo_id=m.id "
            "GROUP BY m.id ORDER BY m.id DESC LIMIT ?", (safe_limit,)).fetchall()
        return [self._memo_row(row) for row in rows]

    def delete_memo(self, memo_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM memos WHERE id=?", (int(memo_id),)).fetchone()
        if not row:
            return None
        with self.conn:
            self.conn.execute("UPDATE calendar_events SET memo_id=NULL WHERE memo_id=?", (int(memo_id),))
            self.conn.execute("DELETE FROM event_sources WHERE kind='memo' AND ref_id=?", (int(memo_id),))
            self.conn.execute("DELETE FROM memos WHERE id=?", (int(memo_id),))
        return self._memo_row(row)

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

    def find_calendar_event(self, date: str, title: str, start_time: str = "") -> dict | None:
        """같은 날·제목·시작 시각 일정 (무시한 제안 포함, 가장 최근 것)"""
        r = self.conn.execute("SELECT * FROM calendar_events WHERE date=? AND title=? AND start_time=? "
                              "ORDER BY id DESC LIMIT 1", (date, title, start_time or "")).fetchone()
        return dict(r) if r else None

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
            "a.school_relevance, a.school_reason, a.school_evidence, a.verified, a.details, "
            "a.provider AS ai_provider, a.model AS ai_model "
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
    # ---- 오늘 할 일 순서 ----
    def _begin(self):
        if self.conn.in_transaction:
            self.conn.commit()

    def claim_plan(self, plan_date: str, slot: str, now_iso: str, stale_minutes: int = 15) -> bool:
        """이 (날짜, 회차) 정리를 내가 맡음. 두 프로그램이 동시에 떠도 한쪽만 True.
        이미 끝난 회차는 False, 정리 중에 꺼져 오래 'running' 으로 남은 것은 다시 맡을 수 있음.
        manual(사용자가 누른 '다시 정리')은 끝난 것도 다시 맡음."""
        self._begin()
        stale = f"-{int(stale_minutes)} minutes"
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO daily_plans(plan_date, slot, state, claimed_at) VALUES (?,?,'running',?)",
                (plan_date, slot, now_iso))
            if cur.rowcount:
                return True
            if slot == "manual":
                cur = self.conn.execute(
                    "UPDATE daily_plans SET state='running', claimed_at=? WHERE plan_date=? AND slot='manual' "
                    "AND (state='done' OR claimed_at IS NULL OR claimed_at <= datetime(?, ?))",
                    (now_iso, plan_date, now_iso, stale))
            else:
                cur = self.conn.execute(
                    "UPDATE daily_plans SET claimed_at=? WHERE plan_date=? AND slot=? AND state='running' "
                    "AND (claimed_at IS NULL OR claimed_at <= datetime(?, ?))",
                    (now_iso, plan_date, slot, now_iso, stale))
            return cur.rowcount > 0

    def finish_plan(self, plan_date: str, slot: str, data: dict, source: str, now_iso: str,
                    provider: str = "", model: str = "", claimed_at: str | None = None) -> bool:
        """정리 결과 저장. claimed_at 을 주면 그 시각에 맡은 실행만 저장할 수 있음
        (멈췄던 실행이 15분 뒤 다른 실행에 넘어간 뒤 다시 깨어나 덮어쓰고 알림을 또 내지 않게)."""
        self._begin()
        sql = ("UPDATE daily_plans SET state='done', finished_at=?, source=?, provider=?, model=?, data=?, notified=0 "
               "WHERE plan_date=? AND slot=?")
        args = [now_iso, source, provider, model, json.dumps(data, ensure_ascii=False), plan_date, slot]
        if claimed_at is not None:
            sql += " AND state='running' AND claimed_at=?"
            args.append(claimed_at)
        with self.conn:
            cur = self.conn.execute(sql, args)
            return cur.rowcount > 0

    def release_plan(self, plan_date: str, slot: str, claimed_at: str) -> None:
        """정리하다 실패: 내가 맡은 실행이면 다음 확인 때 다시 맡을 수 있게 놓아 줌"""
        self.conn.execute("UPDATE daily_plans SET claimed_at=NULL WHERE plan_date=? AND slot=? "
                          "AND state='running' AND claimed_at=?", (plan_date, slot, claimed_at))
        self.conn.commit()

    def get_plan(self, plan_date: str, slot: str | None = None) -> dict | None:
        """그 날의 (회차) 정리. 회차를 안 주면 가장 최근에 끝난 것."""
        if slot:
            r = self.conn.execute("SELECT * FROM daily_plans WHERE plan_date=? AND slot=? AND state='done'",
                                  (plan_date, slot)).fetchone()
        else:
            r = self.conn.execute("SELECT * FROM daily_plans WHERE plan_date=? AND state='done' "
                                  "ORDER BY finished_at DESC, id DESC LIMIT 1", (plan_date,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["data"] = json.loads(d.get("data") or "{}")
        except ValueError:
            d["data"] = {}
        return d

    def last_plan_before(self, now_iso: str) -> dict | None:
        """now_iso 전에 끝난 가장 최근 정리 (날짜·회차 상관없이). 지난번 할 일을 이어 받을 때 씀."""
        r = self.conn.execute("SELECT * FROM daily_plans WHERE state='done' AND finished_at < ? "
                              "ORDER BY finished_at DESC, id DESC LIMIT 1", (now_iso,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["data"] = json.loads(d.get("data") or "{}")
        except ValueError:
            d["data"] = {}
        return d

    def calendar_events_by_ids(self, ids) -> list[dict]:
        """id 로 일정 몇 개 (지운 것 제외). '내일로' 미뤘다가 돌아온 일정처럼 날짜 범위 밖인 것을 가져올 때."""
        ids = [int(i) for i in ids]
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return [dict(r) for r in self.conn.execute(
            "SELECT e.*, m.sender AS msg_sender FROM calendar_events e LEFT JOIN messages m ON m.id = e.message_id "
            f"WHERE e.id IN ({q}) AND e.status != 'dismissed' ORDER BY e.date, e.start_time, e.id", ids)]

    def last_plan_finished_before(self, now_iso: str) -> str | None:
        r = self.conn.execute("SELECT MAX(finished_at) FROM daily_plans WHERE state='done' AND finished_at < ?",
                              (now_iso,)).fetchone()
        return r[0] if r and r[0] else None

    def mark_plan_notified(self, plan_date: str, slot: str) -> bool:
        """알림은 이걸 0→1 로 바꾼 쪽만 보냄 (두 번 안 나가게)"""
        self._begin()
        with self.conn:
            cur = self.conn.execute("UPDATE daily_plans SET notified=1 WHERE plan_date=? AND slot=? AND notified=0",
                                    (plan_date, slot))
            return cur.rowcount > 0

    def set_todo(self, kind: str, ref_id: int, state: str, until: str = "") -> None:
        if kind not in ("message", "event") or state not in ("done", "snoozed"):
            raise ValueError("할 일 상태를 확인해 주세요.")
        self.conn.execute(
            "INSERT INTO todo_state(kind, ref_id, state, until) VALUES (?,?,?,?) "
            "ON CONFLICT(kind, ref_id) DO UPDATE SET state=excluded.state, until=excluded.until, "
            "updated_at=datetime('now','localtime')", (kind, int(ref_id), state, until or ""))
        self.conn.commit()

    def clear_todo(self, kind: str, ref_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM todo_state WHERE kind=? AND ref_id=?", (kind, int(ref_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def todo_states(self) -> dict:
        return {(r["kind"], r["ref_id"]): {"state": r["state"], "until": r["until"] or ""}
                for r in self.conn.execute("SELECT kind, ref_id, state, until FROM todo_state")}

    def plan_messages(self, since_iso: str, deadline_from: str, deadline_to: str, include_ids=()) -> list[dict]:
        """오늘 할 일 후보 소식: 처리 안 했고(done=0), 가장 최근 분석이 AI 분석(규칙 생략 아님)이며,
        마감이 deadline_from~deadline_to(YYYY-MM-DD) 이거나 since_iso 이후에 받은 것,
        또는 include_ids (지난 정리에 있던 할 일·미뤘다가 오늘 돌아온 것)."""
        ids = [int(i) for i in include_ids]
        extra = f" OR m.id IN ({','.join('?' * len(ids))})" if ids else ""
        sql = ("SELECT m.id, m.sender, m.subject, m.source, m.received_at, "
               "a.importance, a.category, a.summary, a.action_items, a.deadline, a.provider, a.school_relevance "
               "FROM messages m JOIN analyses a ON a.id = (SELECT MAX(id) FROM analyses WHERE message_id = m.id) "
               "WHERE m.done = 0 AND a.provider != 'rule' AND ("
               "(a.deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' AND substr(a.deadline, 1, 10) BETWEEN ? AND ?)"
               # 받은 시각은 수집기마다 '2026-11-12T10:00:00'(T) 모양 → 비교 전에 'YYYY-MM-DD HH:MM:SS' 로 맞춤
               " OR (? != '' AND replace(substr(m.received_at, 1, 19), 'T', ' ') >= ?)" + extra +
               ") ORDER BY m.received_at DESC")
        out = []
        for r in self.conn.execute(sql, (deadline_from, deadline_to, since_iso or "", since_iso or "", *ids)):
            d = dict(r)
            try:
                d["action_items"] = json.loads(d["action_items"] or "[]")
            except ValueError:
                d["action_items"] = []
            out.append(d)
        return out

    def messages_linked_to_events(self, event_ids) -> set:
        """이 일정들에 원문으로 연결된 메시지 id (같은 일을 할 일과 일정으로 두 번 보이지 않게)"""
        ids = [int(i) for i in event_ids]
        if not ids:
            return set()
        q = ",".join("?" * len(ids))
        linked = {r[0] for r in self.conn.execute(
            f"SELECT ref_id FROM event_sources WHERE kind='message' AND event_id IN ({q})", ids)}
        linked |= {r[0] for r in self.conn.execute(
            f"SELECT message_id FROM calendar_events WHERE message_id IS NOT NULL AND id IN ({q})", ids)}
        return linked

    def feed(self, *, source: str = "", only_open: bool = True, school_only: bool = False,
             limit: int = 100, since: str = "", order: str = "importance") -> list[dict]:
        """order: importance(처리 안 한 것 먼저·긴급→중요→참고·최신) | recent(받은 시각 최신순)"""
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
        if order == "recent":
            sql += "ORDER BY m.received_at DESC, m.id DESC LIMIT ?"
        else:
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

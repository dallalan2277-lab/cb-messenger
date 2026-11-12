"""오늘 할 일 순서 (능동 비서) — 하루 2번(아침·점심) 오늘 관련 일정·소식을 모아 무엇부터 할지 정함.

이 단계는 AI 없이 규칙만 씀 (마감 지남 → 오늘 마감 → 오늘 일정 시각순 → 마감 빠른 순 → 내일 일정 → 중요도 → 받은 시각).
화면에 보이는 날짜·시각·제목·이유는 모두 DB 값으로 만들고, AI 가 쓴 글은 넣지 않음.
나중에 AI 가 순서를 다듬을 때도(번호·이유 코드만 받음) 이 규칙 순서가 바탕이자 실패할 때의 대체가 됨.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

MAX_ITEMS = 40
DEADLINE_PAST_DAYS = 3      # 마감이 지난 것은 3일 전까지만
DEADLINE_AHEAD_DAYS = 7     # 앞으로 7일 안 마감
SKIP_CATEGORIES = ("참고", "광고·소식")
_IMPORTANCE_RANK = {"긴급": 0, "중요": 1, "참고": 2}
_WEEKDAYS = "월화수목금토일"
_REPLY_WORDS = ("회신", "답변", "답장", "제출", "입력", "작성", "신청", "응답")
_ATTEND_WORDS = ("참석", "출석", "참여")

REASON_LABELS = {
    "overdue": "마감 지남", "deadline_today": "오늘 마감", "deadline_soon": "마감 임박",
    "today_event": "오늘 일정", "tomorrow_event": "내일 일정", "reply_needed": "회신·제출 필요",
    "attend": "참석", "fyi_only": "참고", "other": "",
}


@dataclass
class PlanItem:
    kind: str                 # event(확정 일정) | todo(처리 안 한 소식) | suggested(확정할 일정)
    ref_id: int
    title: str
    date: str = ""            # 일정 날짜 (YYYY-MM-DD)
    time: str = ""            # 일정 시작 시각 (HH:MM)
    end_time: str = ""
    deadline: str = ""        # 소식의 마감 (YYYY-MM-DD[ HH:MM])
    importance: str = ""
    category: str = ""
    sender: str = ""
    source: str = ""
    received_at: str = ""
    action: str = ""          # 첫 번째 할 일 (이유 판단용, 화면엔 안 보냄)
    overdue: bool = False
    no: int = 0
    reason_code: str = "other"
    reason: str = ""
    extra: dict = field(default_factory=dict)


def now_iso(now: datetime) -> str:
    """DB 비교용 시각 문자열 (SQLite datetime 과 같은 모양)"""
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _md(d: str) -> str:
    try:
        x = date.fromisoformat(d[:10])
    except ValueError:
        return d
    return f"{x.month}/{x.day}({_WEEKDAYS[x.weekday()]})"


def _deadline_parts(deadline: str) -> tuple[str, str]:
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?", deadline or "")
    if not m:
        return "", ""
    t = m.group(2) or ""
    if t and len(t) == 4:
        t = "0" + t
    return m.group(1), t


def _is_overdue(item: PlanItem, now: datetime) -> bool:
    d, t = _deadline_parts(item.deadline)
    if not d:
        return False
    today = now.strftime("%Y-%m-%d")
    if d < today:
        return True
    return d == today and bool(t) and t < now.strftime("%H:%M")


def collect(storage, now: datetime, last_plan_at: str | None = None) -> tuple[list, list, int]:
    """(할 일 목록, 확정할 일정 목록, 넘쳐서 뺀 수). 할 일 목록은 규칙 순서로 번호가 매겨져 있음."""
    today = now.date()
    t0, t1 = today.isoformat(), (today + timedelta(days=1)).isoformat()
    states = storage.todo_states()

    def hidden(kind, ref_id) -> bool:
        st = states.get((kind, ref_id))
        if not st:
            return False
        if st["state"] == "done":
            return True
        return st["state"] == "snoozed" and (st["until"] or "") > t0

    items: list[PlanItem] = []
    suggested: list[PlanItem] = []
    event_ids = []
    for e in storage.calendar_events(t0, t1):
        if e["status"] == "confirmed":
            if hidden("event", e["id"]):
                continue
            items.append(PlanItem(kind="event", ref_id=e["id"], title=e["title"], date=e["date"],
                                  time=e.get("start_time") or "", end_time=e.get("end_time") or "",
                                  sender=e.get("msg_sender") or ""))
            event_ids.append(e["id"])
        elif e["status"] == "suggested":
            suggested.append(PlanItem(kind="suggested", ref_id=e["id"], title=e["title"], date=e["date"],
                                      time=e.get("start_time") or "", end_time=e.get("end_time") or "",
                                      sender=e.get("msg_sender") or "",
                                      extra={"dup_of": e.get("dup_of"), "dup_kind": e.get("dup_kind") or ""}))
    linked = storage.messages_linked_to_events(event_ids)

    since = last_plan_at or ""
    d_from = (today - timedelta(days=DEADLINE_PAST_DAYS)).isoformat()
    d_to = (today + timedelta(days=DEADLINE_AHEAD_DAYS)).isoformat()
    for m in storage.plan_messages(since, d_from, d_to):
        if m["id"] in linked or hidden("message", m["id"]):
            continue
        d, _ = _deadline_parts(m.get("deadline") or "")
        in_window = bool(d) and d_from <= d <= d_to
        if not in_window and (m.get("category") in SKIP_CATEGORIES):
            continue      # 마감 없는 새 소식 중 참고·광고는 할 일이 아님
        summary_lines = (m.get("summary") or "").strip().splitlines()
        title = (m.get("subject") or "").strip() or (summary_lines[0] if summary_lines else "")
        actions = m.get("action_items") or []
        items.append(PlanItem(kind="todo", ref_id=m["id"], title=str(title)[:80] or "(제목 없음)",
                              deadline=(m.get("deadline") or "") if in_window else "",
                              importance=m.get("importance") or "", category=m.get("category") or "",
                              sender=m.get("sender") or "", source=m.get("source") or "",
                              received_at=m.get("received_at") or "", action=str(actions[0]) if actions else ""))
    for it in items:
        it.overdue = _is_overdue(it, now)
    ordered = rule_order(items, now)
    overflow = max(0, len(ordered) - MAX_ITEMS)
    ordered = ordered[:MAX_ITEMS]
    for it in ordered + suggested:
        it.reason_code = rule_reason(it, now)
        it.reason = reason_text(it, now)
    suggested.sort(key=lambda x: (x.date, x.time or "99:99", x.ref_id))
    return ordered, suggested, overflow


def _sort_key(it: PlanItem, now: datetime):
    today = now.strftime("%Y-%m-%d")
    d, t = _deadline_parts(it.deadline)
    imp = _IMPORTANCE_RANK.get(it.importance, 3)
    recv = it.received_at or ""
    # 큰 묶음: 0 마감 지남 / 1 오늘 마감(시각 있음) / 2 오늘 일정 / 3 오늘 마감(시각 없음) / 4 앞으로 마감 / 5 내일 일정 / 6 그 밖 새 소식
    if it.kind == "event":
        if it.date == today:
            return (2, it.time or "99:99", "", imp, "")
        return (5, it.time or "99:99", "", imp, "")
    if it.overdue:
        return (0, d, t or "99:99", imp, recv)
    if d == today:
        return (1, t, "", imp, recv) if t else (3, "", "", imp, recv)
    if d:
        return (4, d, t or "99:99", imp, recv)
    return (6, "", "", imp, _neg(recv))


def _neg(s: str) -> str:
    """받은 시각 최신 순으로 정렬하려고 글자를 뒤집은 값"""
    return "".join(chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in s)


def rule_order(items: list, now: datetime) -> list:
    out = sorted(items, key=lambda it: _sort_key(it, now))
    for i, it in enumerate(out, 1):
        it.no = i
    return out


def rule_reason(it: PlanItem, now: datetime) -> str:
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    if it.kind in ("event", "suggested"):
        if it.date == today:
            return "today_event"
        return "tomorrow_event" if it.date == tomorrow else "other"
    d, _ = _deadline_parts(it.deadline)
    if it.overdue:
        return "overdue"
    if d == today:
        return "deadline_today"
    if d:
        return "deadline_soon"
    text = f"{it.action} {it.title}"
    if any(w in text for w in _REPLY_WORDS):
        return "reply_needed"
    if any(w in text for w in _ATTEND_WORDS):
        return "attend"
    if it.importance == "참고":
        return "fyi_only"
    return "other"


def reason_text(it: PlanItem, now: datetime) -> str:
    """화면용 '이유 한 줄' — DB 값(일정 날짜·시각, 마감)과 정해진 말로만 만듦"""
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    parts = []
    if it.kind in ("event", "suggested"):
        when = "오늘" if it.date == today else "내일" if it.date == tomorrow else _md(it.date)
        parts.append(f"{when} {it.time}".strip() if it.time else f"{when} 종일")
        if it.kind == "suggested":
            parts.append("확정 필요")
    else:
        d, t = _deadline_parts(it.deadline)
        if d:
            if it.overdue:
                parts.append(f"마감 지남 {_md(d)}" + (f" {t}" if t else ""))
            elif d == today:
                parts.append("마감 오늘" + (f" {t}" if t else ""))
            elif d == tomorrow:
                parts.append("마감 내일" + (f" {t}" if t else ""))
            else:
                parts.append(f"마감 {_md(d)}" + (f" {t}" if t else ""))
        text = f"{it.action} {it.title}"
        if any(w in text for w in _REPLY_WORDS):
            parts.append("회신·제출 필요")
        elif any(w in text for w in _ATTEND_WORDS):
            parts.append("참석")
        if not d and it.importance in ("긴급", "중요"):
            parts.append(it.importance)
        if not parts:
            parts.append("새 소식")
    return " · ".join(parts)


def _minutes(hhmm: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", hhmm or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def warnings(items: list, now: datetime) -> list[dict]:
    """규칙으로 찾는 경고: 같은 날 확정 일정 시간 겹침(끝 시각이 없으면 1시간으로 봄), 마감 지남"""
    out = []
    evs = [it for it in items if it.kind == "event" and _minutes(it.time) is not None]
    for i, a in enumerate(evs):
        a0 = _minutes(a.time)
        a1 = _minutes(a.end_time) or a0 + 60
        for b in evs[i + 1:]:
            if b.date != a.date:
                continue
            b0 = _minutes(b.time)
            b1 = _minutes(b.end_time) or b0 + 60
            if a0 < b1 and b0 < a1:
                out.append({"kind": "overlap", "nos": [a.no, b.no],
                            "text": f"{a.time} {a.title} 과(와) {b.time} {b.title} 시간이 겹쳐요"})
    over = [it.no for it in items if it.overdue]
    if over:
        out.append({"kind": "overdue", "nos": over, "text": f"마감이 지난 일이 {len(over)}건 있어요"})
    return out


def headline(items: list, suggested: list, overflow: int, now: datetime) -> str:
    today = now.strftime("%Y-%m-%d")
    deadlines = sum(1 for it in items if it.kind == "todo" and (it.overdue or _deadline_parts(it.deadline)[0] == today))
    events = sum(1 for it in items if it.kind == "event" and it.date == today)
    parts = [f"할 일 {len(items)}건"]
    if events:
        parts.append(f"오늘 일정 {events}건")
    if deadlines:
        parts.append(f"오늘까지 마감 {deadlines}건")
    if suggested:
        parts.append(f"확정할 일정 {len(suggested)}건")
    if overflow:
        parts.append(f"그 밖에 {overflow}건")
    return " · ".join(parts) if items or suggested else "오늘 챙길 일이 없어요"


_PUBLIC = ("no", "kind", "ref_id", "title", "date", "time", "end_time", "deadline", "importance",
           "sender", "source", "overdue", "reason_code", "reason")


def _public(it: PlanItem) -> dict:
    d = asdict(it)
    out = {k: d[k] for k in _PUBLIC}
    out.update({k: v for k, v in it.extra.items() if v not in (None, "")})
    return out


def build_plan(storage, now: datetime, slot: str, last_plan_at: str | None = None) -> dict:
    items, suggested, overflow = collect(storage, now, last_plan_at)
    return {
        "slot": slot, "made_at": now_iso(now), "source": "rule",
        "headline": headline(items, suggested, overflow, now),
        "items": [_public(it) for it in items],
        "suggested": [_public(it) for it in suggested],
        "warnings": warnings(items, now),
        "overflow": overflow,
    }


def run_plan(storage, now: datetime, slot: str) -> dict | None:
    """이 회차 정리를 맡아서(두 프로그램이 동시에 해도 한 번만) 만들고 저장. 못 맡으면 None."""
    plan_date = now.strftime("%Y-%m-%d")
    stamp = now_iso(now)
    if not storage.claim_plan(plan_date, slot, stamp):
        return None
    try:
        data = build_plan(storage, now, slot, storage.last_plan_finished_before(stamp))
    except Exception:
        # 다음 확인 때 다시 맡을 수 있게 (claimed_at 을 오래전으로)
        storage.conn.execute("UPDATE daily_plans SET claimed_at=NULL WHERE plan_date=? AND slot=? AND state='running'",
                             (plan_date, slot))
        storage.conn.commit()
        raise
    storage.finish_plan(plan_date, slot, data, "rule", stamp)
    return data


def live_view(storage, now: datetime) -> dict:
    """'오늘' 탭에 보일 것: 오늘 마지막 정리를 바탕으로, 그 뒤 완료·내일로 미룬 것은 빼고 보여 줌.
    정리가 아직 없으면 지금 규칙으로 바로 만들어 보여 줌(저장 안 함)."""
    plan = storage.get_plan(now.strftime("%Y-%m-%d"))
    if not plan:
        data = build_plan(storage, now, "preview")
        data["saved"] = False
        return data
    data = dict(plan["data"])
    states = storage.todo_states()
    t0 = now.strftime("%Y-%m-%d")

    def still_open(x) -> bool:
        kind = "event" if x.get("kind") in ("event", "suggested") else "message"
        st = states.get((kind, x.get("ref_id")))
        if st and (st["state"] == "done" or (st["state"] == "snoozed" and st["until"] > t0)):
            return False
        if kind == "message":
            row = storage.conn.execute("SELECT done FROM messages WHERE id=?", (x.get("ref_id"),)).fetchone()
            return bool(row) and not row["done"]
        row = storage.conn.execute("SELECT status FROM calendar_events WHERE id=?", (x.get("ref_id"),)).fetchone()
        if not row:
            return False
        return row["status"] == ("suggested" if x.get("kind") == "suggested" else "confirmed")

    data["items"] = [x for x in data.get("items", []) if still_open(x)]
    data["suggested"] = [x for x in data.get("suggested", []) if still_open(x)]
    keep = {x["no"] for x in data["items"]}
    data["warnings"] = [w for w in data.get("warnings", []) if all(n in keep for n in w.get("nos", []))]
    data["saved"] = True
    data["finished_at"] = plan.get("finished_at")
    return data

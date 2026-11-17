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
    school: str = ""          # 학교 업무 관련성 (알림 범위 판단용)
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


def collect(storage, now: datetime, last_plan_at: str | None = None, prev_plan: dict | None = None,
            carry_out: list | None = None, holidays: dict | None = None,
            upcoming_out: list | None = None) -> tuple[list, list, int]:
    """(할 일 목록, 확정할 일정 목록, 넘쳐서 뺀 수). 할 일 목록은 규칙 순서로 번호가 매겨져 있음.
    할 일 목록의 일정은 **오늘 것만**. 다음 근무일(금요일이면 월요일)의 확정 일정은 upcoming_out 에 따로 담아 줌
    (예전엔 내일 일정이 할 일에 섞여 '할 일 N건'에 세어졌음 — 2026-11-16 사용자 제보).
    prev_plan: 지난 정리(없으면 DB 에서 찾음). carry_out: 주면 40건 제한 전 모든 할 일 id 를 담아 줌(다음에 이어 받기용)."""
    today = now.date()
    t0 = today.isoformat()
    t1 = next_workday(today, holidays or {}).isoformat()     # 다음 근무일 (미리 보기·확정할 일정 범위)
    states = storage.todo_states()

    def hidden(kind, ref_id) -> bool:
        st = states.get((kind, ref_id))
        if not st:
            return False
        if st["state"] == "done":
            return True
        return st["state"] == "snoozed" and (st["until"] or "") > t0

    # '내일로' 미뤘다가 오늘(또는 그 전) 돌아온 것 — 원래 날짜·받은 시각이 범위 밖이어도 다시 보여 줌
    back = [(k, ref) for (k, ref), st in states.items() if st["state"] == "snoozed" and (st["until"] or "") <= t0]

    items: list[PlanItem] = []
    suggested: list[PlanItem] = []
    events = storage.calendar_events(t0, t1)
    seen = {e["id"] for e in events}
    events += [e for e in storage.calendar_events_by_ids([ref for k, ref in back if k == "event"])
               if e["id"] not in seen and e["status"] == "confirmed"]
    # 같은 일을 원문 소식으로 다시 보이지 않게: 완료·미룬 일정도 연결 확인엔 넣음 (취소·지운 일정은 빼서,
    # 그 일정에 이어진 새 후속 소식(예: 취소 공지 속 제출 업무)이 숨지 않게)
    state_events = storage.calendar_events_by_ids([ref for k, ref in states if k == "event"])
    link_ids = ([e["id"] for e in events if e["status"] == "confirmed"]
                + [e["id"] for e in state_events if e["status"] == "confirmed"])
    upcoming: list[PlanItem] = []
    for e in events:
        if e["status"] == "confirmed":
            if hidden("event", e["id"]):
                continue
            it = PlanItem(kind="event", ref_id=e["id"], title=e["title"], date=e["date"],
                          time=e.get("start_time") or "", end_time=e.get("end_time") or "",
                          sender=e.get("msg_sender") or "")
            if e["date"] > t0:
                upcoming.append(it)      # 다음 근무일 일정 → 미리 보기 (할 일·번호·건수에 안 넣음)
            else:
                items.append(it)
        elif e["status"] == "suggested":
            suggested.append(PlanItem(kind="suggested", ref_id=e["id"], title=e["title"], date=e["date"],
                                      time=e.get("start_time") or "", end_time=e.get("end_time") or "",
                                      sender=e.get("msg_sender") or "",
                                      extra={"dup_of": e.get("dup_of"), "dup_kind": e.get("dup_kind") or ""}))
    linked = storage.messages_linked_to_events(link_ids)

    since = last_plan_at or ""
    d_from = (today - timedelta(days=DEADLINE_PAST_DAYS)).isoformat()
    d_to = (today + timedelta(days=DEADLINE_AHEAD_DAYS)).isoformat()
    # 지난 정리에 있던 할 일은 완료할 때까지 이어 받음 (마감 없는 소식이 다음 정리에서 사라지지 않게)
    if prev_plan is None:
        prev_plan = storage.last_plan_before(now_iso(now)) if hasattr(storage, "last_plan_before") else None
    prev_data = (prev_plan or {}).get("data") or {}
    carry = prev_data.get("carry_ids")
    if not isinstance(carry, list):      # carry_ids 가 없던 예전 정리는 보인 할 일만
        carry = [x.get("ref_id") for x in prev_data.get("items", []) if x.get("kind") == "todo"]
    carry += [ref for k, ref in back if k == "message"]
    carry = [int(i) for i in carry if isinstance(i, int) and not isinstance(i, bool)]
    for m in storage.plan_messages(since, d_from, d_to, include_ids=carry):
        if m["id"] in linked or hidden("message", m["id"]):
            continue
        d, _ = _deadline_parts(m.get("deadline") or "")
        in_window = bool(d) and d <= d_to     # 마감이 오래 지난 것(새로 받았거나 이어 받은 것)은 '마감 지남'으로
        if not in_window and (m.get("category") in SKIP_CATEGORIES):
            continue      # 마감 없는 새 소식 중 참고·광고는 할 일이 아님
        summary_lines = (m.get("summary") or "").strip().splitlines()
        title = (m.get("subject") or "").strip() or (summary_lines[0] if summary_lines else "")
        actions = m.get("action_items") or []
        items.append(PlanItem(kind="todo", ref_id=m["id"], title=str(title)[:80] or "(제목 없음)",
                              deadline=(m.get("deadline") or "") if in_window else "",
                              importance=m.get("importance") or "", category=m.get("category") or "",
                              sender=m.get("sender") or "", source=m.get("source") or "",
                              received_at=m.get("received_at") or "", school=m.get("school_relevance") or "",
                              action=str(actions[0]) if actions else ""))
    for it in items:
        it.overdue = _is_overdue(it, now)
    if carry_out is not None:
        carry_out.extend(it.ref_id for it in items if it.kind == "todo")
    ordered = rule_order(items, now)
    overflow = max(0, len(ordered) - MAX_ITEMS)
    ordered = ordered[:MAX_ITEMS]
    upcoming.sort(key=lambda x: (x.date, x.time or "99:99", x.ref_id))
    for it in ordered + suggested + upcoming:
        it.reason_code = rule_reason(it, now)
        it.reason = reason_text(it, now)
    suggested.sort(key=lambda x: (x.date, x.time or "99:99", x.ref_id))
    if upcoming_out is not None:
        upcoming_out.extend(upcoming)
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
    return " · ".join(parts) if items or suggested or overflow else "오늘 챙길 일이 없어요"


def headline_from_public(items: list, suggested: list, overflow: int, now: datetime) -> str:
    """화면에 남은 항목(dict)으로 총평을 다시 만듦 (완료·이동 뒤 '할 일 N건' 이 그대로 남지 않게)"""
    today = now.strftime("%Y-%m-%d")
    deadlines = sum(1 for x in items if x.get("kind") == "todo"
                    and (x.get("overdue") or _deadline_parts(x.get("deadline") or "")[0] == today))
    events = sum(1 for x in items if x.get("kind") == "event" and x.get("date") == today)
    parts = [f"할 일 {len(items)}건"]
    if events:
        parts.append(f"오늘 일정 {events}건")
    if deadlines:
        parts.append(f"오늘까지 마감 {deadlines}건")
    if suggested:
        parts.append(f"확정할 일정 {len(suggested)}건")
    if overflow:
        parts.append(f"그 밖에 {overflow}건 ([다시 정리]로 보기)")
    return " · ".join(parts) if items or suggested or overflow else "오늘 챙길 일이 없어요"


_PUBLIC = ("no", "kind", "ref_id", "title", "date", "time", "end_time", "deadline", "importance",
           "sender", "source", "school", "overdue", "reason_code", "reason")


def _public(it: PlanItem) -> dict:
    d = asdict(it)
    out = {k: d[k] for k in _PUBLIC}
    out.update({k: v for k, v in it.extra.items() if v not in (None, "")})
    return out


def upcoming_items(storage, now: datetime, holidays: dict | None = None) -> list:
    """다음 근무일까지(내일~다음 근무일)의 확정 일정 — 지금 달력에서 바로 만듦 (완료·미룬 것은 뺌)"""
    today = now.date()
    t0 = today.isoformat()
    t1 = next_workday(today, holidays or {}).isoformat()
    states = storage.todo_states()
    out = []
    for e in storage.calendar_events((today + timedelta(days=1)).isoformat(), t1):
        if e["status"] != "confirmed":
            continue
        st = states.get(("event", e["id"]))
        if st and (st["state"] == "done" or (st["state"] == "snoozed" and (st["until"] or "") > t0)):
            continue
        out.append(PlanItem(kind="event", ref_id=e["id"], title=e["title"], date=e["date"],
                            time=e.get("start_time") or "", end_time=e.get("end_time") or "",
                            sender=e.get("msg_sender") or ""))
    out.sort(key=lambda x: (x.date, x.time or "99:99", x.ref_id))
    for it in out:
        it.reason_code = rule_reason(it, now)
        it.reason = reason_text(it, now)
    return out


def upcoming_label(day: str, now: datetime) -> str:
    """미리 보기 칸 제목: '내일 11/17(화)' 또는 (금요일이면) '다음 근무일 11/23(월)'"""
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    return ("내일 " if day == tomorrow else "다음 근무일 ") + _md(day)


def build_plan(storage, now: datetime, slot: str, last_plan_at: str | None = None,
               prev_plan: dict | None = None, holidays: dict | None = None) -> dict:
    carry: list = []
    upcoming: list = []
    items, suggested, overflow = collect(storage, now, last_plan_at, prev_plan, carry, holidays, upcoming)
    next_day = next_workday(now.date(), holidays or {}).isoformat()
    return {
        "upcoming": [_public(it) for it in upcoming],      # 다음 근무일까지의 확정 일정 (할 일 아님)
        "upcoming_date": next_day,
        "upcoming_label": upcoming_label(next_day, now),
        "carry_ids": carry,     # 화면엔 안 씀: 40건 넘어 안 보인 할 일도 다음 정리에 이어 받기용
        "slot": slot, "made_at": now_iso(now), "source": "rule",
        "headline": headline(items, suggested, overflow, now),
        "items": [_public(it) for it in items],
        "suggested": [_public(it) for it in suggested],
        "warnings": warnings(items, now),
        "overflow": overflow,
    }


def run_plan(storage, now: datetime, slot: str, holidays: dict | None = None) -> dict | None:
    """이 회차 정리를 맡아서(두 프로그램이 동시에 해도 한 번만) 만들고 저장. 못 맡으면 None."""
    plan_date = now.strftime("%Y-%m-%d")
    stamp = now_iso(now)
    # 실행권을 맡기 전에 지난 정리를 먼저 읽어 둠 ('다시 정리'는 맡을 때 지난 수동 정리를 running 으로 바꿔서
    # 그 뒤엔 찾을 수 없음 → 직전 수동 정리의 할 일이 이어지지 않던 문제)
    prev = storage.last_plan_before(stamp)
    # 처음 정리하는 날(이전 정리가 없음)은 최근 하루 동안 받은 소식을 새 소식으로 봄
    since = (prev or {}).get("finished_at") or now_iso(now - timedelta(days=1))
    if not storage.claim_plan(plan_date, slot, stamp):
        return None
    try:
        data = build_plan(storage, now, slot, since, prev_plan=prev or {}, holidays=holidays)
    except Exception:
        storage.release_plan(plan_date, slot, stamp)   # 다음 확인 때 다시 맡을 수 있게
        raise
    # 내가 맡은 실행일 때만 저장 (멈춘 사이 다른 실행이 넘겨받았으면 그쪽 결과를 그대로 둠)
    if not storage.finish_plan(plan_date, slot, data, "rule", stamp, claimed_at=stamp):
        return None
    return data


def live_view(storage, now: datetime, holidays: dict | None = None) -> dict:
    """'오늘' 탭에 보일 것: 오늘 마지막 정리를 바탕으로, 그 뒤 완료·내일로 미룬 것은 빼고 보여 줌.
    정리가 아직 없으면 지금 규칙으로 바로 만들어 보여 줌(저장 안 함)."""
    plan = storage.get_plan(now.strftime("%Y-%m-%d"))
    if not plan:
        since = storage.last_plan_finished_before(now_iso(now)) or now_iso(now - timedelta(days=1))
        data = build_plan(storage, now, "preview", since, holidays=holidays)
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

    items = [x for x in data.get("items", []) if still_open(x)]
    # 예전(이 수정 전)에 저장한 정리엔 내일 일정이 할 일에 섞여 있음 → 날짜를 보고 미리 보기로 옮김
    later = [x for x in items if x.get("kind") == "event" and (x.get("date") or "") > t0]
    items = [x for x in items if x not in later]
    # 미리 보기는 저장된 정리 대신 지금 달력에서 바로 만듦 — 정리 뒤 새로 넣은 일정, 예전 금요일 정리에
    # 없던 월요일 일정도 보이게 (예전 정리의 'later' 일정은 위에서 할 일에서만 뺌)
    upcoming = [_public(it) for it in upcoming_items(storage, now, holidays)]
    data["upcoming_label"] = upcoming_label(next_workday(now.date(), holidays or {}).isoformat(), now)
    # 번호는 1부터 다시 (완료·이동으로 빈 번호가 생기지 않게), 경고도 새 번호로
    renum = {}
    for i, x in enumerate(items, 1):
        renum[x.get("no")] = i
        x["no"] = i
    warns = []
    for w in data.get("warnings", []):
        if w.get("kind") == "overdue":
            continue                      # 마감 지남 경고는 남은 항목으로 아래에서 다시 만듦
        nos = w.get("nos", [])
        if all(n in renum for n in nos):
            warns.append({**w, "nos": [renum[n] for n in nos]})
    over = [x["no"] for x in items if x.get("overdue")]
    if over:
        warns.append({"kind": "overdue", "nos": over, "text": f"마감이 지난 일이 {len(over)}건 있어요"})
    data["items"], data["upcoming"], data["warnings"] = items, upcoming, warns
    data["suggested"] = [x for x in data.get("suggested", []) if still_open(x)]
    data["headline"] = headline_from_public(items, data["suggested"], int(data.get("overflow") or 0), now)
    data["saved"] = True
    data["finished_at"] = plan.get("finished_at")
    return data


def is_workday(d: date, holidays: dict) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def next_workday(d: date, holidays: dict) -> date:
    """'내일로' 미룰 때: 다음 근무일 (주말·공휴일 건너뜀)"""
    x = d + timedelta(days=1)
    for _ in range(30):
        if is_workday(x, holidays):
            return x
        x += timedelta(days=1)
    return d + timedelta(days=1)

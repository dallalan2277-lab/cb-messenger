"""충북소통메신저(eztalk.exe) 창 글자 → 보낸 사람·본문 파싱 (순수 파이썬, Windows 아니어도 테스트 가능).

Project Monday 의 messenger_watch.py (실제 메신저로 검증된 규칙)에서 가져왔습니다.
- 알림 창(TFrmAlarm):  [..., 종류(쪽지…), 시각, 보낸 사람, 본문…]
- 쪽지 보기 창(TFrmViewNote): '보낸사람 :' 줄 + 머리글 → 본문 전체 덩어리 → 줄별 조각
- 보안 쪽지("보안"·"비밀번호" 표시)는 내용을 쓰지 않음
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta

_ZERO = dict.fromkeys(map(ord, "﻿​‌‍⁠"), None)  # 보이지 않는 글자
TIME_RE = re.compile(r"\d{1,2}\s*(?::|시)\s*\d{1,2}")
KIND_RE = re.compile(r"^(쪽지|메시지|메세지|대화|공지|알림)")
LABELS = ("보낸", "받는", "수신", "받은쪽지", "보낸쪽지", "받은 쪽지", "보낸 쪽지")
NOT_BODY = {"님", "님의", "님이", "님께"}
SECURE_WORDS = ("보안", "비밀번호")

SENDER_LABEL_RE = re.compile(r"^보낸\s*사람\s*[:：]?\s*(.*)$")
TAG_RE = re.compile(r"^(?:받은|보낸)\s*쪽지\s*(.*)$")
TIME_ONLY_RE = re.compile(r"^\(?\s*(?:\d{2,4}\s*[./년]\s*)?\d{1,2}\s*[./월]\s*\d{1,2}\s*일?\s*\d{1,2}\s*(?::|시)\s*\d{1,2}\s*분?\s*\)?$")
NAME_HEAD_RE = re.compile(r"^([가-힣A-Za-z][가-힣A-Za-z0-9·.\s]{0,14}?)\s*(?:[-–—]\s*\(|\(|\s[-–—]\s)")


def clean_text(s):
    return (s or "").translate(_ZERO).strip()


def tidy_list(texts):
    """보이지 않는 글자를 지우고, 빈 것과 바로 앞과 같은 것을 뺌"""
    out = []
    for t in texts:
        t = clean_text(t)
        if t and (not out or out[-1] != t):
            out.append(t)
    return out


def secure_why(texts):
    """보안 쪽지로 본 근거 (없으면 ""). ※ 안내 문구와 4자 이하 짧은 글(버튼 이름 등)은 제외"""
    for t in texts:
        if t.startswith("※") or len(t) <= 4:
            continue
        if any(w in t for w in SECURE_WORDS):
            return t[:60]
    return ""


def _is_label(t):
    return t.startswith(LABELS)


def sender_name(s):
    """'홍길동 - (223) 부서/상태글' 처럼 이름 뒤에 붙은 글을 떼고 이름만. 못 가르면 그대로"""
    s = clean_text(s)
    m = NAME_HEAD_RE.match(s)
    return m.group(1).strip() if m else s


def parse_alarm(texts):
    texts = tidy_list(texts)
    why = secure_why(texts)
    ev = {"source": "alarm", "sender": "", "kind": "", "time": "", "title": "", "body": "",
          "secure": bool(why), "why": why}
    kind_i = next((i for i, t in enumerate(texts) if KIND_RE.match(t)), None)
    time_i = next((i for i, t in enumerate(texts) if TIME_RE.search(t)), None)
    if kind_i is not None:
        ev["kind"] = texts[kind_i]
    anchor = time_i if time_i is not None else kind_i
    if time_i is not None:
        ev["time"] = texts[time_i]
    if anchor is not None and anchor + 1 < len(texts):
        ev["sender"] = texts[anchor + 1]
        ev["body"] = " ".join(texts[anchor + 2:]).strip()
        ev["why"] = secure_why(texts[:anchor + 2])  # 본문 속 단어는 제외
        ev["secure"] = bool(ev["why"])
    return _finish(ev)


def _find_sender(texts):
    for i, t in enumerate(texts):
        m = SENDER_LABEL_RE.match(t)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and not TIME_ONLY_RE.match(rest):
            return sender_name(rest), t, i
        for u in texts[i + 1:i + 5]:
            if u.startswith("※"):
                break
            if _is_label(u) or TIME_ONLY_RE.match(u) or u.startswith("[") or not re.search(r"[가-힣A-Za-z]", u):
                continue
            return sender_name(u), u, i
        return "", "", i
    return "", "", None


def _find_title(texts):
    for i, t in enumerate(texts):
        m = TAG_RE.match(t)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and not rest.startswith(("보기", "함")):
            return rest
        if i + 1 < len(texts) and not _is_label(texts[i + 1]) and not texts[i + 1].startswith("※"):
            return texts[i + 1]
    return ""


def parse_view(texts):
    texts = tidy_list(texts)
    why = secure_why(texts)
    ev = {"source": "view", "sender": "", "kind": "쪽지", "time": "", "title": "", "body": "",
          "secure": bool(why), "why": why}
    name, sender_raw, send_i = _find_sender(texts)
    if name:
        ev["sender"] = name
    elif send_i is not None or any(t.startswith("보낸") for t in texts):
        send_i = next((i for i, t in enumerate(texts) if t.startswith("보낸")), None)
        if send_i is not None:
            sender_raw = next((t for t in texts[send_i + 1:]
                               if not _is_label(t) and not t.startswith("[") and re.search(r"[가-힣A-Za-z]", t)), "")
            ev["sender"] = sender_name(sender_raw)
    title = _find_title(texts)
    if title:
        ev["title"] = title[:120]
    ev["time"] = next((t for t in texts if TIME_RE.search(t) and len(t) <= 40), "")
    if send_i is not None and sender_raw and sender_raw in texts[send_i:]:
        # 본문 덩어리는 바로 다음 글(첫 줄 조각)이 그 앞부분과 같다는 점으로 찾음. 못 찾으면 가장 긴 글.
        start = texts.index(sender_raw, send_i) + 1
        # 보낸 사람 이름 바로 뒤에 붙는 소속·전화번호 줄('312(교실) / 070-… / 담당 / 1-2담임', 앞에 '- ' 붙은 것도)은 본문이 아님:
        # 이름 다음부터 시각·'받는사람'·'※' 안내문 전까지가 그 머리글 구역
        head_end = next((i for i in range(start, len(texts))
                         if TIME_RE.search(texts[i]) or texts[i].startswith(("받는", "※"))), len(texts))
        # (머리글 구역의 앞 3줄 중 '/' 로 나뉜 줄만 — 본문이 머리글처럼 보이는 옛 창 모양에서 본문을 지우지 않게)
        profile = {re.sub(r"^[-–—·\s]+", "", t) for t in texts[start:min(head_end, start + 3)] if t.count("/") >= 2}
        # 소통메신저 보기 창은 '※「정보통신망…」' 안내문 다음에 본문이 옴 → 안내문 뒤에 글이 있으면 그 뒤만 본문 후보
        notice = max((i for i in range(start, len(texts) - 1) if texts[i].startswith("※")), default=None)
        # '받는사람' 줄 뒤의 받는 사람 목록(이름들·인원수)은 본문이 아님 — 안내문(※)까지, 안내문이 아직 없으면 끝까지
        skip: set[int] = set()
        to_i = next((i for i in range(start, len(texts)) if texts[i].startswith("받는")), None)
        if to_i is not None:
            to_end = next((i for i in range(to_i + 1, len(texts)) if texts[i].startswith("※")), len(texts))
            skip = set(range(to_i, to_end))
        if notice is not None:
            start = notice + 1
        pool = [t for i, t in enumerate(texts) if i >= start and i not in skip and not (
                    t.startswith("※") or _is_label(t) or t == sender_raw or t == ev["time"]
                    or t == title or TIME_ONLY_RE.match(t) or re.sub(r"^[-–—·\s]+", "", t) in profile)]
        norm = lambda x: re.sub(r"\s+", "", x)  # noqa: E731
        body = ""
        for i in range(len(pool) - 1):
            a_, b_ = norm(pool[i]), norm(pool[i + 1])
            if b_ and len(b_) >= 2 and a_.startswith(b_[:20]) and len(a_) >= len(b_.rstrip("…")) - 1:
                body = pool[i]
                break
        if not body and pool:
            body = max(pool, key=len)
        ev["body"] = body
        # 본문과 본문의 줄별 조각 속 '보안 점검 안내' 등은 보안 쪽지 표시가 아니므로 제외
        # (Monday 원본은 본문 덩어리만 빼서, 조각 줄에 '보안'이 있으면 보안 쪽지로 잘못 판단했음)
        nb = norm(body)
        ev["why"] = secure_why([t for t in texts if not (nb and norm(t) and norm(t) in nb)])
        ev["secure"] = bool(ev["why"])
    return _finish(ev)


def _finish(ev):
    body = ev["body"].strip()
    ev["body"] = body
    ev["ok"] = bool(ev["sender"] and body and body not in NOT_BODY and not ev["secure"])
    ev["key"] = hashlib.sha1((ev["sender"] + "|" + body[:40]).encode("utf-8")).hexdigest()[:16]
    ev["ts"] = time.time()
    return ev


_SENT_RE = re.compile(
    r"(?:(?P<y>\d{4})\s*[./년-]\s*)?(?:(?P<mo>\d{1,2})\s*[./월-]\s*(?P<d>\d{1,2})\s*일?\.?\s*(?:\([^)]*\))?\s*)?"
    r"(?P<ap>오전|오후|AM|PM|am|pm)?\s*(?P<h>\d{1,2})\s*(?::|시)\s*(?P<mi>\d{1,2})")


def sent_time(text, now=None):
    """쪽지 창에 적힌 보낸 시각 글자 → datetime (모르면 None).
    예: '2026.11.09 15:25', '11/09 오후 3:25', '11월 9일 15시 25분', '오후 3:25'(날짜 없으면 오늘, 미래면 어제)"""
    m = _SENT_RE.search(text or "")
    if not m:
        return None
    now = now or datetime.now()
    try:
        h, mi = int(m["h"]), int(m["mi"])
        if m["ap"] in ("오후", "PM", "pm") and h < 12:
            h += 12
        elif m["ap"] in ("오전", "AM", "am") and h == 12:
            h = 0
        if m["mo"]:
            year = int(m["y"]) if m["y"] else now.year
            dt = datetime(year, int(m["mo"]), int(m["d"]), h, mi)
            if not m["y"] and dt > now + timedelta(days=1):   # 연도 없이 12월 쪽지를 1월에 연 경우
                dt = dt.replace(year=year - 1)
            return dt
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return dt - timedelta(days=1) if dt > now + timedelta(minutes=5) else dt
    except ValueError:   # 25시·13월 같은 잘못된 값
        return None


def is_reopened_old(ev, watch_started, grace_min=10, now=None):
    """감시를 켜기 전에 보낸 쪽지를 보기 창으로 다시 연 것인지.
    알림 창(새 쪽지가 왔을 때 뜨는 창)은 언제나 새 쪽지로 봄. 보낸 시각을 못 읽으면 새 쪽지로 봄."""
    if ev.get("source") != "view":
        return False
    dt = sent_time(ev.get("time", ""), now)
    return bool(dt and dt < datetime.fromtimestamp(watch_started) - timedelta(minutes=grace_min))


def _stem(body):
    return body.rstrip("….· ").strip()


class Batch:
    """읽은 쪽지를 잠깐 모아서(조용해질 때까지) 한 번에 넘김.
    같은 쪽지가 알림 창 → 보기 창으로 두 번 읽혀도 한 건으로 합치고, 더 긴 본문으로 보강."""

    def __init__(self, quiet_sec=5.0, recent_sec=1800):
        self.pending: list[dict] = []
        self.recent: list[tuple] = []    # (sender, stem, ts)
        self.quiet = quiet_sec
        self.recent_sec = recent_sec
        self.last_add = 0.0

    @staticmethod
    def _same(a_sender, a_body, b_sender, b_body):
        if a_sender != b_sender:
            return False
        sa, sb = _stem(a_body), _stem(b_body)
        return bool(sa and sb) and (sa.startswith(sb) or sb.startswith(sa))

    def add(self, ev, now=None):
        """'new' / 'dup' / 'upgrade' 반환"""
        now = now or time.time()
        if not ev["ok"]:   # 내용을 못 읽은 것(보안 등): 연달아 두 번 잡혀도 한 건
            if any((not p["ok"]) and p["source"] != ev["source"] and now - p["ts"] < 5 for p in self.pending):
                return "dup"
            self.pending.append(ev)
            self.last_add = now
            return "new"
        for p in self.pending:
            if p["ok"] and self._same(p["sender"], p["body"], ev["sender"], ev["body"]):
                if len(ev["body"]) > len(p["body"]):
                    p["body"], p["key"] = ev["body"], ev["key"]
                    if ev.get("title") and not p.get("title"):
                        p["title"] = ev["title"]
                    return "upgrade"
                return "dup"
        for s, b, ts in self.recent:
            if now - ts < self.recent_sec and self._same(s, b, ev["sender"], ev["body"]):
                return "dup"
        self.pending.append(ev)
        self.last_add = now
        return "new"

    def ready(self, now=None):
        now = now or time.time()
        return bool(self.pending) and now - self.last_add >= self.quiet

    def flush(self, now=None):
        now = now or time.time()
        out, self.pending = self.pending, []
        for ev in out:
            if ev["ok"]:
                self.recent.append((ev["sender"], _stem(ev["body"]), now))
        self.recent = [r for r in self.recent if now - r[2] < self.recent_sec]
        return out

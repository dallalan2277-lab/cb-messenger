"""네이버 메일 수집기 테스트 — 가짜 IMAP 서버 사용 (실제 계정·네트워크 없음). 메일은 모두 가상 예시."""
import imaplib
import re
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors.mail_content import html_to_text
from app.collectors.naver_mail import MailAuthError, MailError, NaverMailCollector, check_account, verify_login
from app.storage import Storage

SEOUL = timezone(timedelta(hours=9))
TODAY = datetime.now(SEOUL).replace(hour=9, minute=10, second=0, microsecond=0)


def mk(subject, body, sender="교무부 <office@school.kr>", html=None, attach=None, big=False):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = sender, subject, f"<{abs(hash(subject))}@test>"
    m["Date"] = TODAY.strftime("%a, %d %b %Y %H:%M:%S +0900")
    if html:
        m.set_content(body)
        m.add_alternative(html, subtype="html")
        m.clear_content() if False else None
    else:
        m.set_content(body + ("x" * (2 * 1024 * 1024 + 10) if big else ""))
    if attach:
        m.add_attachment(b"data", maintype="application", subtype="pdf", filename=attach)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, mails, validity=7, password="app-pw"):
        self.mails = dict(mails)            # uid -> (datetime, raw)
        self.validity, self.password = validity, password
        self.readonly, self.fetch_specs, self.logged_out = None, [], False

    def login(self, user, pw):
        if pw != self.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"ok"]

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.mails)).encode()]

    def response(self, code):
        return code, [str(self.validity).encode()]

    def status(self, box, what):
        nxt = max(self.mails, default=0) + 1
        return "OK", [f'"INBOX" (UIDVALIDITY {self.validity} UIDNEXT {nxt})'.encode()]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            q = " ".join(a for a in args if a)
            if q.startswith("SINCE"):
                d = datetime.strptime(q.split()[1], "%d-%b-%Y").date()
                ids = [u for u, (t, _) in self.mails.items() if t.date() >= d]
            else:
                lo = int(re.search(r"UID (\d+):\*", q).group(1))
                ids = [u for u in self.mails if u >= lo] or [max(self.mails)]   # IMAP: n:* 은 최소 마지막 하나
            return "OK", [" ".join(map(str, sorted(ids))).encode()]
        if cmd == "FETCH":
            uid, spec = int(args[0]), args[1]
            self.fetch_specs.append(spec)
            t, raw = self.mails[uid]
            idate = t.strftime("%d-%b-%Y %H:%M:%S +0900")
            if "HEADER" in spec:
                head = raw.split(b"\n\n", 1)[0] + b"\n\n"
                return "OK", [(f'1 (UID {uid} RFC822.SIZE {len(raw)} INTERNALDATE "{idate}" BODY[HEADER] {{{len(head)}}}'.encode(), head), b")"]
            return "OK", [(f"1 (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        raise AssertionError(cmd)

    def logout(self):
        self.logged_out = True


def collector(tmp_path, fake, **kw):
    st = Storage(str(tmp_path / "t.db"))
    return NaverMailCollector("teacher@naver.com", kw.pop("password", "app-pw"), st, interval_min=2,
                              imap_factory=lambda: fake, **kw), st


def test_account_check():
    assert check_account(" Teacher@Naver.com ") == "teacher@naver.com"
    with pytest.raises(MailError):
        check_account("teacher@gmail.com")


def test_first_poll_reads_today_only_readonly_and_peek(tmp_path):
    old = TODAY - timedelta(days=3)
    fake = FakeIMAP({1: (old, mk("지난 메일", "예전")), 2: (TODAY, mk("회의 안내", "11/12 15:30 회의")),
                     3: (TODAY, mk("자료 요청", "금요일까지 제출 바랍니다", attach="양식.hwp"))})
    col, st = collector(tmp_path, fake)
    msgs = list(col.collect_once())
    assert [m.subject for m in msgs] == ["회의 안내", "자료 요청"]
    assert fake.readonly is True and fake.logged_out
    assert all("PEEK" in s for s in fake.fetch_specs)                    # 읽음 표시 안 바뀜
    assert msgs[1].body.startswith("[제목] 자료 요청") and "[첨부] 양식.hwp" in msgs[1].body
    assert msgs[0].source == "mail" and msgs[0].sender == "교무부 <office@school.kr>"
    assert st.get_mail_cursor("teacher@naver.com")["last_uid"] == 3
    # 간격 전에는 서버에 다시 안 감
    fake.mails[4] = (TODAY, mk("새 공지", "내용"))
    assert list(col.collect_once()) == []
    col.next_at = 0
    assert [m.subject for m in col.collect_once()] == ["새 공지"]       # 다음엔 새 메일만


def test_no_mail_yet_sets_baseline(tmp_path):
    old = TODAY - timedelta(days=5)
    fake = FakeIMAP({1: (old, mk("옛날", "x")), 2: (old, mk("옛날2", "y"))})
    col, st = collector(tmp_path, fake)
    assert list(col.collect_once()) == []
    assert st.get_mail_cursor("teacher@naver.com")["last_uid"] == 2
    fake.mails[3] = (TODAY, mk("오늘 온 메일", "z"))
    col.next_at = 0
    assert [m.subject for m in col.collect_once()] == ["오늘 온 메일"]


def test_big_mail_headers_only_and_html(tmp_path):
    fake = FakeIMAP({1: (TODAY, mk("큰 메일", "본문", big=True)),
                     2: (TODAY, mk("HTML 메일", "", html="<html><style>.x{}</style><body><p>안녕하세요</p><script>alert(1)</script><p>회의는 <b>3시</b></p></body></html>"))})
    col, _ = collector(tmp_path, fake)
    big, h = list(col.collect_once())
    assert big.partial and "2MB" in big.body
    assert "alert" not in h.body and "회의는 3시" in h.body


def test_wrong_password_pauses(tmp_path):
    errs = []
    fake = FakeIMAP({1: (TODAY, mk("a", "b"))})
    col, _ = collector(tmp_path, fake, password="wrong", on_error=errs.append)
    assert list(col.collect_once()) == []
    assert col.paused_reason and "애플리케이션 비밀번호" in errs[0]
    col.next_at = 0
    assert list(col.collect_once()) == [] and len(errs) == 1            # 틀린 비밀번호로 계속 시도하지 않음
    with pytest.raises(MailAuthError):
        verify_login("teacher@naver.com", "wrong", imap_factory=lambda: FakeIMAP({}))


def test_uidvalidity_change_rebaselines(tmp_path):
    fake = FakeIMAP({1: (TODAY, mk("첫 메일", "a"))})
    col, st = collector(tmp_path, fake)
    list(col.collect_once())
    fake2 = FakeIMAP({10: (TODAY - timedelta(days=9), mk("아주 옛날", "x")), 11: (TODAY, mk("다시 받은 오늘 메일", "b"))}, validity=99)
    col._factory = lambda: fake2
    col.next_at = 0
    assert [m.subject for m in col.collect_once()] == ["다시 받은 오늘 메일"]
    assert st.get_mail_cursor("teacher@naver.com")["uidvalidity"] == 99


def test_html_to_text():
    assert html_to_text("<div>가&nbsp;나</div><div>다</div>") == "가 나\n\n다"

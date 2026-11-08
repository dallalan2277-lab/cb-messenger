"""네이버 메일 수집기 (IMAP, 읽기 전용).

MailDesk 0.3.0 의 방식을 참고해 새로 작성했습니다.
- imap.naver.com:993 (SSL), 네이버 주소 + '애플리케이션 비밀번호'
- 받은편지함을 EXAMINE(읽기 전용)으로 열고, 본문은 BODY.PEEK 로 가져와 읽음 표시를 바꾸지 않음
- 서버 메일을 지우거나 옮기지 않음. 보내기 기능 없음
- 처음 연결한 날(한국 시각 0시) 이후 도착한 메일만, 마지막으로 본 UID 다음부터 가져옴
- 한 번에 최대 50통, 2MB 넘는 메일은 제목·보낸 사람만
- 확인 간격(기본 2분)마다만 서버에 접속 (collect_once 는 자주 불러도 됨)

네이버 준비: 네이버 메일 환경설정 > POP3/IMAP 설정 > IMAP/SMTP 사용함,
           2단계 인증 후 '애플리케이션 비밀번호' 발급 → 그 비밀번호를 입력
"""
from __future__ import annotations

import hashlib
import imaplib
import logging
import re
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from typing import Callable, Iterable

from ..models import Message
from .base import Collector
from .mail_content import extract_body, parse_headers

log = logging.getLogger(__name__)
HOST, PORT = "imap.naver.com", 993
MAX_PER_POLL = 50
MAX_SIZE = 2 * 1024 * 1024
SEOUL = timezone(timedelta(hours=9))
NAVER_RE = re.compile(r"^[a-z0-9_.\-]+@naver\.com$")


class MailError(RuntimeError):
    """사용자에게 그대로 보여 줘도 되는 한국어 오류"""


class MailAuthError(MailError):
    pass


def check_account(account: str) -> str:
    a = (account or "").strip().lower()
    if not NAVER_RE.match(a):
        raise MailError("네이버 메일 주소(...@naver.com)를 입력하세요.")
    return a


def friendly(e: Exception, stage: str) -> MailError:
    if isinstance(e, MailError):
        return e
    if isinstance(e, socket.gaierror):
        return MailError(f"[{stage}] 네이버 서버 주소를 찾지 못했습니다. 인터넷 연결을 확인하세요.")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return MailError(f"[{stage}] 네이버 서버 응답 시간이 초과되었습니다.")
    if isinstance(e, ssl.SSLError):
        return MailError(f"[{stage}] 네이버 서버와 보안 연결을 맺지 못했습니다.")
    if isinstance(e, ConnectionRefusedError):
        return MailError(f"[{stage}] 메일 서버 접속이 거절되었습니다. 학교 업무망에서는 막혀 있을 수 있습니다.")
    if isinstance(e, OSError):
        return MailError(f"[{stage}] 네트워크 오류로 메일을 확인하지 못했습니다.")
    if isinstance(e, imaplib.IMAP4.error):
        return MailError(f"[{stage}] 네이버 서버가 요청을 처리하지 못했습니다.")
    return MailError(f"[{stage}] 메일을 확인하지 못했습니다.")


def _imap_date(d: datetime) -> str:
    return d.strftime("%d-%b-%Y")   # IMAP SEARCH SINCE 형식 (영문 월)


def _num(resp, key: str) -> int | None:
    for item in resp or []:
        s = item.decode() if isinstance(item, bytes) else str(item)
        m = re.search(rf"{key}\s+(\d+)", s)
        if m:
            return int(m.group(1))
    return None


class NaverMailCollector(Collector):
    name = "mail"

    def __init__(self, account: str, password: str, storage, interval_min: float = 2.0,
                 imap_factory: Callable[[], imaplib.IMAP4] | None = None, on_error: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.account = check_account(account)
        if not password:
            raise MailError("애플리케이션 비밀번호가 없습니다. 메일 설정에서 다시 연결하세요.")
        self._password = password
        self.storage = storage
        self.interval = max(1.0, float(interval_min)) * 60
        self._factory = imap_factory or (lambda: imaplib.IMAP4_SSL(HOST, PORT, timeout=30))
        self.on_error = on_error
        self.clock = clock
        self.next_at = 0.0           # 처음에는 바로 확인
        self.paused_reason = ""      # 로그인 실패 등으로 멈춤
        self.last_check = 0.0
        self.last_error = ""

    # ---------- 바깥에서 부르는 것 ----------
    def check_now(self):
        self.next_at = 0.0
        self.paused_reason = ""

    def collect_once(self) -> Iterable[Message]:
        now = self.clock()
        if self.paused_reason or now < self.next_at:
            return []
        self.next_at = now + self.interval
        try:
            msgs = self.poll()
            self.last_error = ""
            return msgs
        except MailAuthError as e:
            self.paused_reason = str(e)      # 비밀번호가 틀리면 계속 시도하지 않음 (계정 잠김 방지)
            self._report(str(e))
        except MailError as e:
            self._report(str(e))
        return []

    def _report(self, msg: str):
        self.last_error = msg
        log.warning("메일 확인 실패: %s", msg)
        if self.on_error:
            self.on_error(msg)

    # ---------- 한 번 확인 ----------
    def poll(self) -> list[Message]:
        stage = "접속"
        client = None
        try:
            client = self._factory()
            stage = "로그인"
            try:
                client.login(self.account, self._password)
            except imaplib.IMAP4.error as e:
                raise MailAuthError("네이버 로그인에 실패했습니다. 메일 주소와 애플리케이션 비밀번호, "
                                    "IMAP 사용 설정을 확인하세요.") from e
            stage = "받은편지함 열기"
            typ, data = client.select("INBOX", readonly=True)      # EXAMINE (읽기 전용)
            if typ != "OK":
                raise MailError("네이버 서버가 받은편지함을 읽기 전용으로 여는 요청을 거절했습니다.")
            validity = None
            try:
                _, dat = client.response("UIDVALIDITY")
                if dat and dat[0] and re.fullmatch(rb"\d+", dat[0].strip()):
                    validity = int(dat[0])
            except Exception:
                pass
            if validity is None:
                typ, st = client.status("INBOX", "(UIDVALIDITY UIDNEXT)")
                validity = _num(st, "UIDVALIDITY")
            if validity is None:
                raise MailError("네이버 서버에서 받은편지함 식별 정보가 오지 않았습니다.")
            stage = "새 메일 목록 확인"
            uids, since = self._new_uids(client, validity)
            stage = "메일 읽기"
            out, last_ok = [], None
            for uid in uids[:MAX_PER_POLL]:
                m = self._read(client, uid, since)
                last_ok = uid
                if m:
                    out.append(m)
            if last_ok is not None:
                self.storage.set_mail_cursor(self.account, validity, last_ok, since)
            if len(uids) > MAX_PER_POLL:
                self.next_at = 0.0           # 남은 메일은 바로 이어서
            self.last_check = self.clock()
            return out
        except MailError:
            raise
        except Exception as e:
            raise friendly(e, stage) from e
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass

    def _new_uids(self, client, validity: int) -> tuple[list[int], str]:
        cur = self.storage.get_mail_cursor(self.account)
        if cur is None or cur["uidvalidity"] != validity:
            # 처음이거나 서버의 받은편지함 기준이 바뀜 → 시작일(처음 연결한 날 0시) 이후 메일부터
            since = cur["since_date"] if cur else datetime.now(SEOUL).date().isoformat()
            d = datetime.fromisoformat(since)
            typ, data = client.uid("SEARCH", None, "SINCE", _imap_date(d))
            if typ != "OK":
                raise MailError("네이버 서버가 새 메일 목록 조회를 거절했습니다.")
            uids = sorted(int(x) for x in (data[0] or b"").split())
            if not uids:   # 기준만 저장 (아직 메일 없음)
                typ, st = client.status("INBOX", "(UIDNEXT)")
                nxt = _num(st, "UIDNEXT") or 1
                self.storage.set_mail_cursor(self.account, validity, nxt - 1, since)
            return uids, since
        since = cur["since_date"]
        typ, data = client.uid("SEARCH", None, f"UID {cur['last_uid'] + 1}:*")
        if typ != "OK":
            raise MailError("네이버 서버가 새 메일 목록 조회를 거절했습니다.")
        uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > cur["last_uid"])
        return uids, since

    def _read(self, client, uid: int, since: str) -> Message | None:
        typ, data = client.uid("FETCH", str(uid), "(UID RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        meta = data[0][0].decode(errors="replace")
        size = _num([meta], "RFC822.SIZE") or 0
        when = self._internaldate(meta)
        if when and when.date().isoformat() < since:
            return None
        if size > MAX_SIZE:
            hdr = parse_headers(BytesParser(policy=policy.default).parsebytes(data[0][1]))
            body, files, partial = "(메일이 2MB를 넘어 본문을 가져오지 않았습니다. 네이버 메일에서 확인하세요.)", [], True
        else:
            typ, full = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if typ != "OK" or not full or not isinstance(full[0], tuple):
                return None
            msg = BytesParser(policy=policy.default).parsebytes(full[0][1])
            hdr = parse_headers(msg)
            body, files = extract_body(msg)
            partial = False
        text = f"[제목] {hdr['subject']}\n\n{body.strip() or '(본문 없음)'}"
        if files:
            text += "\n\n[첨부] " + ", ".join(files[:10])
        ext = hdr["message_id"] or f"{self.account}:{uid}"
        return Message(sender=hdr["sender"], body=text, room="메일", source=self.name,
                       external_id=hashlib.sha1(ext.encode("utf-8", "replace")).hexdigest()[:20],
                       received_at=(when or datetime.now(SEOUL)).astimezone(SEOUL).replace(tzinfo=None).isoformat(timespec="seconds"),
                       subject=hdr["subject"], partial=partial)

    @staticmethod
    def _internaldate(meta: str) -> datetime | None:
        mm = re.search(r'INTERNALDATE "([^"]+)"', meta)
        if not mm:
            return None
        try:
            return datetime.strptime(mm.group(1), "%d-%b-%Y %H:%M:%S %z").astimezone(SEOUL)
        except ValueError:
            return None


def verify_login(account: str, password: str, imap_factory=None) -> None:
    """설정 화면의 [연결 확인]: 로그인 + 받은편지함 읽기 전용 열기만 해 봄"""
    account = check_account(account)
    client = (imap_factory or (lambda: imaplib.IMAP4_SSL(HOST, PORT, timeout=20)))()
    try:
        try:
            client.login(account, password)
        except imaplib.IMAP4.error as e:
            raise MailAuthError("로그인 실패: 메일 주소·애플리케이션 비밀번호·IMAP 사용 설정을 확인하세요.") from e
        typ, _ = client.select("INBOX", readonly=True)
        if typ != "OK":
            raise MailError("받은편지함을 열지 못했습니다.")
    except MailError:
        raise
    except Exception as e:
        raise friendly(e, "연결 확인") from e
    finally:
        try:
            client.logout()
        except Exception:
            pass

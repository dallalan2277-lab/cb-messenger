"""메일 원문(바이트) → 보낸 사람·제목·본문 글자. HTML 은 글자로만 바꾸고 이미지·스크립트는 실행하지 않음."""
from __future__ import annotations

import html
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

MAX_BODY_CHARS = 250_000


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "head", "title", "noscript", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "td":
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(s: str) -> str:
    p = _HTMLText()
    try:
        p.feed(s)
        p.close()
        text = "".join(p.out)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", s)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _part_text(part) -> str:
    try:
        return part.get_content()
    except Exception:
        raw = part.get_payload(decode=True) or b""
        for enc in (part.get_content_charset() or "utf-8", "cp949", "euc-kr", "utf-8"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "replace")


def extract_body(msg: EmailMessage) -> tuple[str, list[str]]:
    """(본문 글자, 첨부파일 이름들). text/plain 우선, 없으면 HTML 을 글자로."""
    plain, htmls, files = [], [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        name = part.get_filename()
        if disp == "attachment" or (name and disp != "inline"):
            if name:
                files.append(name)
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            htmls.append(_part_text(part))
    if any(t.strip() for t in plain):
        body = "\n\n".join(t.strip() for t in plain if t.strip())
    elif htmls:
        body = "\n\n".join(html_to_text(h) for h in htmls)
    else:
        body = ""
    return body[:MAX_BODY_CHARS], files


def parse_mail(raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    return parse_headers(msg) | dict(zip(("body", "attachments"), extract_body(msg)))


def parse_headers(msg) -> dict:
    def hdr(name):
        try:
            return str(msg.get(name, "") or "").strip()
        except Exception:
            return ""
    addrs = getaddresses([hdr("From")])
    name, addr = addrs[0] if addrs else ("", "")
    sender = f"{name} <{addr}>" if name and addr else (addr or name or "(보낸 사람 없음)")
    date = ""
    try:
        d = parsedate_to_datetime(hdr("Date"))
        date = d.astimezone().isoformat(timespec="seconds") if d else ""
    except Exception:
        pass
    return {"sender": sender, "sender_addr": addr.lower(), "subject": hdr("Subject") or "(제목 없음)",
            "message_id": hdr("Message-ID"), "date": date}

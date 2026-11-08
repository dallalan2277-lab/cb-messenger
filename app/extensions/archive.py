"""쪽지 원문·요약을 월별 파일로 보관 — 이 PC(로컬)와 구글 드라이브를 따로 켜고 끔.

Project Monday 의 원문 보관 방식을 참고했습니다.
- 구글 드라이브는 API 가 아니라 PC 의 'Google Drive 데스크톱' 동기화 폴더에 파일을 쓰는 방식
  (파일을 쓰면 드라이브 프로그램이 알아서 클라우드로 올림)
- 지우거나 고치지 않고 덧붙이기만 함:  쪽지원문_2026-11.txt / 쪽지요약_2026-11.txt
- 드라이브 폴더에 못 쓰면 이 PC 의 '드라이브_대기' 폴더에 임시로 쌓아 둠 (sync_pending() 으로 나중에 옮김)
- 드라이브(클라우드)에는 기본으로 개인정보를 가린 원문을 올림 (설정으로 끌 수 있음)
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path

from ..models import Analysis, Message

DRIVE_SUBDIR = "충북메신저AI_쪽지기록"
PENDING_DIRNAME = "드라이브_대기"


def find_gdrive_root() -> str:
    """Google Drive 데스크톱 동기화 폴더 자동 찾기 (못 찾으면 '')."""
    home = Path.home()
    cands = [Path(r"D:\Google Drive")]
    cands += [Path(f"{c}:\\{n}") for c in "GHIJKDEF" for n in ("내 드라이브", "My Drive")]
    cands += [home / "Google Drive" / "내 드라이브", home / "Google Drive" / "My Drive", home / "Google Drive"]
    for c in cands:
        try:
            if c.is_dir():
                return str(c)
        except OSError:
            continue
    return ""


def resolve_gdrive_dir(configured: str) -> str:
    """설정값이 있으면 그대로, 없으면 자동으로 찾은 드라이브 아래 기본 폴더."""
    if configured.strip():
        return configured.strip()
    root = find_gdrive_root()
    return str(Path(root) / DRIVE_SUBDIR) if root else ""


class Archiver:
    def __init__(self, *, local_enabled: bool, local_dir: str,
                 gdrive_enabled: bool, gdrive_dir: str,
                 fmt: str = "txt", gdrive_redact: bool = True, redact_fn=None,
                 include_summary: bool = True):
        self.local_enabled = local_enabled
        self.local_dir = local_dir
        self.gdrive_enabled = gdrive_enabled
        self.gdrive_dir = resolve_gdrive_dir(gdrive_dir) if gdrive_enabled else ""
        self.ext = "md" if fmt.lower() == "md" else "txt"
        self.gdrive_redact = gdrive_redact
        self.redact_fn = redact_fn or (lambda s: s)
        self.include_summary = include_summary
        self.pending_dir = str(Path(local_dir) / PENDING_DIRNAME)
        self.lock = threading.Lock()
        self.status = {"local": "", "gdrive": "", "last_error": ""}
        if gdrive_enabled and not self.gdrive_dir:
            self.status["last_error"] = ("구글 드라이브 폴더를 찾지 못했습니다. Google Drive 데스크톱을 설치하거나 "
                                         "설정에서 폴더를 직접 지정하세요. 그동안은 이 PC에 임시로 보관합니다.")

    @property
    def enabled(self) -> bool:
        return self.local_enabled or self.gdrive_enabled

    # ---------- 형식 ----------
    def _file(self, folder: str, kind: str, when: datetime) -> Path:
        return Path(folder) / f"{kind}_{when:%Y-%m}.{self.ext}"

    def _header(self, kind: str, when: datetime) -> str:
        title = "쪽지 원문" if kind == "쪽지원문" else "쪽지 요약"
        h = "# " if self.ext == "md" else ""
        return (f"{h}{title} {when:%Y-%m}\n\n충북메신저 AI 비서가 기록했습니다. "
                "보안 쪽지와 내용을 읽지 못한 쪽지는 기록하지 않습니다.\n\n" + ("---\n\n" if self.ext == "md" else "=" * 40 + "\n\n"))

    def _sep(self) -> str:
        return "\n---\n\n" if self.ext == "md" else "\n" + "-" * 40 + "\n\n"

    def _raw_entry(self, m: Message, body: str) -> str:
        when = m.received_at.replace("T", " ")
        if self.ext == "md":
            body = re.sub(r"(?m)^(#+)", r"\\\1", body)   # 본문 속 '#' 줄이 제목처럼 보이지 않게
            head = f"## {when} · 보낸이: {m.sender}" + (f" · {m.room}" if m.room else "")
        else:
            head = f"[{when}] 보낸이: {m.sender}" + (f" ({m.room})" if m.room else "")
        return f"{head}\n\n{body}\n{self._sep()}"

    def _summary_line(self, m: Message, a: Analysis, sender: str) -> str:
        when = m.received_at.replace("T", " ")[:16]
        parts = [f"[{when}] [{a.importance}/{a.category}] {sender}: {a.summary}"]
        if a.deadline:
            parts.append(f"   마감: {a.deadline}")
        parts += [f"   할 일: {x}" for x in a.action_items]
        parts += [f"   일정: {e.date} {e.start_time} {e.title}".rstrip() for e in a.events]
        line = "\n".join(parts) + "\n"
        return ("- " + line) if self.ext == "md" else line

    # ---------- 쓰기 ----------
    def _append(self, folder: str, kind: str, text: str, when: datetime) -> str:
        fp = self._file(folder, kind, when)
        fp.parent.mkdir(parents=True, exist_ok=True)
        new = not fp.exists()
        with open(fp, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(self._header(kind, when))
            f.write(text)
        return str(fp)

    def _write(self, kind: str, local_text: str, drive_text: str, when: datetime) -> None:
        with self.lock:
            if self.local_enabled:
                try:
                    self.status["local"] = self._append(self.local_dir, kind, local_text, when)
                except OSError as e:
                    self.status["last_error"] = f"이 PC 저장 실패: {e}"[:200]
            if self.gdrive_enabled:
                done = False
                if self.gdrive_dir:
                    try:
                        self.status["gdrive"] = self._append(self.gdrive_dir, kind, drive_text, when)
                        done = True
                    except OSError as e:
                        self.status["last_error"] = f"구글 드라이브 저장 실패 → 이 PC에 임시 보관: {e}"[:200]
                if not done:
                    try:
                        self._append(self.pending_dir, kind, drive_text, when)
                    except OSError as e:
                        self.status["last_error"] = f"임시 보관도 실패: {e}"[:200]

    def save_message(self, m: Message) -> None:
        if not self.enabled:
            return
        when = _dt(m.received_at)
        drive_body = self.redact_fn(m.body) if self.gdrive_redact else m.body
        drive_sender = self.redact_fn(m.sender) if self.gdrive_redact else m.sender
        drive_msg = Message(sender=drive_sender, body=drive_body, room=m.room, received_at=m.received_at)
        self._write("쪽지원문", self._raw_entry(m, m.body), self._raw_entry(drive_msg, drive_body), when)

    def save_analysis(self, m: Message, a: Analysis) -> None:
        if not (self.enabled and self.include_summary):
            return
        when = _dt(m.received_at)
        drive_sender = self.redact_fn(m.sender) if self.gdrive_redact else m.sender
        self._write("쪽지요약", self._summary_line(m, a, m.sender), self._summary_line(m, a, drive_sender), when)

    def sync_pending(self) -> int:
        """드라이브에 못 올려 이 PC에 쌓아 둔 파일을 드라이브 폴더로 덧붙여 옮김. 옮긴 파일 수."""
        if not (self.gdrive_enabled and self.gdrive_dir and os.path.isdir(self.pending_dir)):
            return 0
        n = 0
        with self.lock:
            for fp in sorted(Path(self.pending_dir).glob(f"*.{self.ext}")):
                try:
                    text = fp.read_text(encoding="utf-8")
                    # 대기 파일의 머리글은 떼고 본문만 덧붙임
                    sep = "---\n\n" if self.ext == "md" else "=" * 40 + "\n\n"
                    body = text.split(sep, 1)[1] if sep in text else text
                    kind = fp.stem.split("_")[0]
                    when = datetime.strptime(fp.stem.split("_")[1], "%Y-%m")
                    self._append(self.gdrive_dir, kind, body, when)
                    fp.unlink()
                    n += 1
                except (OSError, ValueError, IndexError) as e:
                    self.status["last_error"] = f"대기 파일 옮기기 실패: {e}"[:200]
        return n


def _dt(iso: str) -> datetime:
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return datetime.now()


def build_archiver(settings, redact_fn=None) -> Archiver:
    return Archiver(
        local_enabled=settings.archive_local_enabled, local_dir=settings.archive_local_dir,
        gdrive_enabled=settings.archive_gdrive_enabled, gdrive_dir=settings.archive_gdrive_dir,
        fmt=settings.archive_format, gdrive_redact=settings.archive_gdrive_redact,
        redact_fn=redact_fn, include_summary=settings.archive_include_summary,
    )

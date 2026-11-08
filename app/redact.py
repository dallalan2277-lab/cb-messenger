"""AI로 보내기 전에 개인정보를 가리는 모듈.

정규식 기반이라 완벽하지 않습니다. 학생 이름처럼 패턴이 없는 정보는
.env 의 REDACT_NAMES 에 직접 등록해야 가려집니다.
"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("[주민번호]", re.compile(r"\b\d{6}\s?-\s?[1-8]\d{6}\b")),
    ("[전화번호]", re.compile(r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b")),
    ("[전화번호]", re.compile(r"\b0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}\b")),
    ("[이메일]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("[계좌번호]", re.compile(r"\b\d{3,6}-\d{2,6}-\d{2,8}(-\d{1,3})?\b")),
    # 3학년 2반 15번 / 3-2-15 형태의 학적 정보
    ("[학번]", re.compile(r"\b\d{1}\s?학년\s?\d{1,2}\s?반\s?\d{1,2}\s?번\b")),
    ("[학번]", re.compile(r"\b\d-\d{1,2}-\d{1,2}번\b")),
]


def redact(text: str, names: list[str] | None = None) -> str:
    """텍스트에서 개인정보를 가려서 돌려줍니다."""
    if not text:
        return text
    out = text
    for label, pat in _PATTERNS:
        out = pat.sub(label, out)
    for i, name in enumerate(sorted(set(names or []), key=len, reverse=True)):
        if name:
            out = out.replace(name, f"[이름{i + 1}]")
    return out

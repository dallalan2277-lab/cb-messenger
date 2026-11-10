"""다른 캘린더의 일정 파일 → 바탕화면 캘린더 일정 목록 (읽기만 함, 저장은 CalendarAPI 가).

읽을 수 있는 파일
- .ics   : 구글·네이버·Outlook 캘린더에서 내보낸 파일 (반복 일정은 첫 번째만)
- .xlsx  : 표(날짜·제목·시간·장소 칸) 또는 주간 달력 모양(맨 왼쪽 '2026.11', 칸 안 첫 줄이 날짜 숫자)
- .csv / .tsv : 표 (UTF-8 또는 한글 Windows(CP949) 글자)
- .txt   : 한 줄에 일정 하나 ('2026-11-12 14:00 학년 협의회') 또는 날짜 줄 아래에 일정 줄들
옛 엑셀(.xls)은 읽지 못함 → 엑셀에서 '다른 이름으로 저장 → .xlsx' 안내.

추가 부품 없이 파이썬 기본 기능만 씀 (엑셀은 zip 안의 XML 을 직접 읽음). 인터넷·AI 로 보내지 않음.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_EVENTS = 3000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RANGE_DAYS = 31        # '11.12~11.14' 같은 기간 일정은 하루씩 나눔 (너무 길면 첫날만)
SUPPORTED = (".ics", ".xlsx", ".csv", ".tsv", ".txt")

FIELDS = ("date", "title", "start_time", "end_time", "location", "note")
FIELD_LABELS = {"date": "날짜", "title": "제목", "start_time": "시작 시각", "end_time": "끝 시각",
                "location": "장소", "note": "메모"}
# 머리글 글자 → 칸 종류 (앞쪽이 우선)
HEADER_WORDS = {
    "date": ("날짜", "일자", "일시", "시작일", "날", "date", "day", "start date"),
    "title": ("제목", "일정", "행사", "내용", "행사명", "일정명", "할일", "할 일", "title", "summary", "subject", "event"),
    "start_time": ("시작시간", "시작 시간", "시작시각", "시작", "시간", "시각", "start time", "start", "time"),
    "end_time": ("종료시간", "종료 시간", "종료", "끝", "마침", "end time", "end"),
    "location": ("장소", "위치", "location", "where", "place"),
    "note": ("메모", "비고", "설명", "참고", "대상", "note", "description", "memo", "notes"),
}


class ImportError_(ValueError):
    """사용자에게 보여 줄 가져오기 오류 (한국어)"""


# ───────────────────────── 날짜·시각 읽기 ─────────────────────────
_D_FULL = re.compile(r"(?<!\d)(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?(?!\d)")
_D_SHORT = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:[./]|월\s*)\s*(\d{1,2})\s*일?(?![\d])")
_T_AMPM = re.compile(r"(오전|오후|AM|PM|am|pm)\s*(\d{1,2})\s*(?:[:시]\s*(\d{1,2})\s*분?)?")
_T_COLON = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_T_KOR = re.compile(r"(?<!\d)(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|반)?")
_WEEKDAY = re.compile(r"\(\s*[월화수목금토일]\s*\)|[월화수목금토일]요일")


def _mkdate(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_date(value, default_year: int | None = None):
    """'2026-11-12', '2026.11.12.', '2026년 11월 12일', '11/12', '11월 12일(목)', 엑셀 날짜 숫자 → date"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}(?:\.\d+)?", s):          # 엑셀 날짜 숫자 (1900 기준)
        n = float(s)
        if 20000 < n < 80000:
            return date(1899, 12, 30) + timedelta(days=int(n))
        return None
    if re.fullmatch(r"\d{8}", s):                     # 20261112
        return _mkdate(s[:4], s[4:6], s[6:])
    m = _D_FULL.search(s)
    if m:
        return _mkdate(*m.groups())
    m = _D_SHORT.search(s)
    if m:
        return _mkdate(default_year or date.today().year, *m.groups())
    return None


def parse_time(text, school_pm: bool = True) -> str:
    """'14:00', '오후 2시', '2시 30분', '3시 반' → 'HH:MM' (없으면 '').
    school_pm: '2:00'·'2시'처럼 오전·오후가 없고 1~6시면 학교 일정으로 보고 오후로."""
    s = str(text or "")
    m = _T_AMPM.search(s)
    if m:
        ap, h, mi = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if ap in ("오후", "PM", "pm") and h < 12:
            h += 12
        elif ap in ("오전", "AM", "am") and h == 12:
            h = 0
    else:
        m = _T_COLON.search(s)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
        else:
            m = _T_KOR.search(s)
            if not m:
                return ""
            h = int(m.group(1))
            mi = 30 if "반" in m.group(0) else int(m.group(2) or 0)
        if school_pm and 1 <= h <= 6:
            h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return ""
    return f"{h:02d}:{mi:02d}"


def _time_range(text) -> tuple[str, str]:
    """'14:00~15:30', '오후 2시-3시' → ('14:00', '15:30')"""
    s = str(text or "")
    parts = re.split(r"\s*[~∼〜]\s*|\s+-\s+|(?<=\d)\s*-\s*(?=\d|오)", s, maxsplit=1)
    start = parse_time(parts[0])
    end = parse_time(parts[1]) if len(parts) > 1 else ""
    if end and start and end < start and int(end[:2]) < 12:   # '오후 2시~4시' → 16:00
        e = int(end[:2]) + 12
        end = f"{e:02d}:{end[3:]}" if e <= 23 else ""
    return start, end


def _cell_time(value) -> str:
    """엑셀 시각 칸: 0.5833(=14:00) 같은 숫자도 읽음"""
    s = str(value or "").strip()
    if re.fullmatch(r"0?\.\d+", s):
        mins = round(float(s) * 24 * 60)
        return f"{mins // 60:02d}:{mins % 60:02d}" if mins < 24 * 60 else ""
    if re.fullmatch(r"\d{5}\.\d+", s):            # 날짜+시각 숫자
        frac = float(s) % 1
        mins = round(frac * 24 * 60)
        return f"{mins // 60:02d}:{mins % 60:02d}" if 0 < mins < 24 * 60 else ""
    return parse_time(s, school_pm=False) if re.search(r"\d", s) else ""


def _strip_lead(text: str) -> str:
    """제목 앞에 남은 날짜·요일·시각·구분 기호를 떼어 냄"""
    s = text
    for rx in (_D_FULL, _D_SHORT):
        s = rx.sub(" ", s, count=1) if rx.match(s.strip()) else s
    s = _WEEKDAY.sub(" ", s)
    s = re.sub(r"^[\s\-–—·•*▶▷○●◦:|,~]+", "", s)      # '- 9:00 회의' 의 앞 기호부터
    s = re.sub(r"^\s*(?:(?:오전|오후|AM|PM)\s*)?\d{1,2}\s*(?::\d{2}|시\s*(?:\d{1,2}\s*분|반)?)"
               r"(?:\s*[~∼〜-]\s*(?:(?:오전|오후)\s*)?\d{1,2}\s*(?::\d{2}|시\s*(?:\d{1,2}\s*분|반)?))?", " ", s)
    s = re.sub(r"^[\s\-–—·•*:|,~]+", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _event(d: date, title, start="", end="", location="", note="") -> dict:
    return {"date": d.isoformat(), "title": str(title or "").strip()[:200], "start_time": start or "",
            "end_time": end or "", "location": str(location or "").strip()[:300], "note": str(note or "").strip()[:2000]}


def _expand(d1: date, d2: date | None, **kw) -> list[dict]:
    """기간 일정 → 하루씩 (끝 날짜 포함). 너무 길거나 거꾸로면 첫날만, 메모에 기간 표시."""
    if not d2 or d2 <= d1:
        return [_event(d1, **kw)]
    days = (d2 - d1).days + 1
    if days > MAX_RANGE_DAYS:
        note = (kw.get("note") or "")
        kw["note"] = (note + "\n" if note else "") + f"기간: {d1.isoformat()} ~ {d2.isoformat()}"
        return [_event(d1, **kw)]
    return [_event(d1 + timedelta(days=i), **kw) for i in range(days)]


# ───────────────────────── 파일 → 글자 ─────────────────────────
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx_rows(path: Path) -> list[list[str]]:
    """첫 번째 시트를 글자 표로 (빈 칸은 ''). 수식은 마지막 계산값."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ImportError_("엑셀 파일을 열 수 없습니다. 옛 엑셀(.xls)이면 엑셀에서 '다른 이름으로 저장 → .xlsx' 로 바꿔 주세요.") from e
    with z:
        names = set(z.namelist())
        sheet_path = "xl/worksheets/sheet1.xml"
        try:   # 첫 번째 시트의 실제 파일 이름 (sheet1.xml 이 아닐 수 있음)
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            first = wb.find("m:sheets/m:sheet", _NS)
            rid = first.get(f"{{{_NS['r']}}}id") if first is not None else None
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels.findall("rel:Relationship", _NS):
                if rel.get("Id") == rid:
                    target = rel.get("Target").lstrip("/")
                    sheet_path = target if target.startswith("xl/") else "xl/" + target
        except (KeyError, ET.ParseError, AttributeError):
            pass
        if sheet_path not in names:
            raise ImportError_("엑셀 파일 안에서 시트를 찾지 못했습니다.")
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        sheet = ET.fromstring(z.read(sheet_path))
    data = sheet.find("m:sheetData", _NS)
    rows: list[list[str]] = []
    for row in (data if data is not None else []):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            ref = c.get("r") or ""
            if not ref:
                continue
            v = c.find("m:v", _NS)
            t = c.get("t")
            if t == "inlineStr":
                isel = c.find("m:is", _NS)
                val = "".join(x.text or "" for x in isel.iter(f"{{{_NS['m']}}}t")) if isel is not None else ""
            elif v is None:
                val = ""
            elif t == "s":
                try:
                    val = shared[int(v.text)]
                except (ValueError, IndexError, TypeError):
                    val = ""
            else:
                val = v.text or ""
            cells[_col_index(ref)] = val.replace("\r", "")
        r = int(row.get("r") or len(rows) + 1)
        while len(rows) < r - 1:
            rows.append([])
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


# ───────────────────────── .ics ─────────────────────────
def _ics_unescape(s: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s)


def _ics_dt(prop: str, value: str):
    """(date, 'HH:MM' 또는 '') — UTC(Z)는 이 PC 시각으로 바꿈"""
    value = value.strip()
    if "VALUE=DATE" in prop.upper() or re.fullmatch(r"\d{8}", value):
        return _mkdate(value[:4], value[4:6], value[6:8]), ""
    m = re.fullmatch(r"(\d{8})T(\d{2})(\d{2})(\d{2})?(Z?)", value)
    if not m:
        return None, ""
    dt = datetime.strptime(m.group(1) + m.group(2) + m.group(3), "%Y%m%d%H%M")
    if m.group(5):
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return dt.date(), dt.strftime("%H:%M")


def parse_ics(text: str) -> tuple[list[dict], list[str]]:
    lines = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n")).split("\n")   # 접힌 줄 펴기
    events, warnings, cur, repeat = [], [], None, 0
    for line in lines:
        if line.strip() == "BEGIN:VEVENT":
            cur = {}
        elif line.strip() == "END:VEVENT":
            if cur is not None:
                d1, t1 = cur.get("start", (None, ""))
                if d1:
                    d2, t2 = cur.get("end", (None, ""))
                    if d2 and not t1 and not t2:     # 종일 일정의 끝 날짜는 '다음 날'로 적힘
                        d2 -= timedelta(days=1)
                    same_day = not d2 or d2 == d1
                    events += _expand(d1, None if (t1 or same_day) else d2,
                                      title=cur.get("SUMMARY", "") or "(제목 없음)",
                                      start=t1, end=t2 if same_day or t1 else "",
                                      location=cur.get("LOCATION", ""), note=cur.get("DESCRIPTION", ""))
                    if cur.get("RRULE"):
                        repeat += 1
            cur = None
        elif cur is not None and ":" in line:
            prop, value = line.split(":", 1)
            name = prop.split(";", 1)[0].upper()
            if name == "DTSTART":
                cur["start"] = _ics_dt(prop, value)
            elif name == "DTEND":
                cur["end"] = _ics_dt(prop, value)
            elif name in ("SUMMARY", "LOCATION", "DESCRIPTION"):
                cur[name] = _ics_unescape(value).strip()
            elif name == "RRULE":
                cur["RRULE"] = value
    if repeat:
        warnings.append(f"반복 일정 {repeat}건은 첫 번째 날짜만 가져옵니다.")
    return events, warnings


# ───────────────────────── 표 (엑셀·csv) ─────────────────────────
def _norm_head(s: str) -> str:
    return re.sub(r"[\s()\[\]:_*]", "", str(s or "")).lower()


def guess_mapping(header: list[str]) -> dict[str, int]:
    """머리글 → {칸 종류: 몇 번째 칸}"""
    out: dict[str, int] = {}
    heads = [_norm_head(h) for h in header]
    for field in ("date", "end_time", "start_time", "location", "note", "title"):
        for word in HEADER_WORDS[field]:
            w = _norm_head(word)
            hit = next((i for i, h in enumerate(heads) if h and i not in out.values()
                        and (h == w or (len(w) >= 2 and h.startswith(w)))), None)
            if hit is not None:
                out[field] = hit
                break
    return out


def _find_header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:15]):
        m = guess_mapping(row)
        if "date" in m and ("title" in m or len(m) >= 2):
            return i
    return None


def _guess_without_header(rows: list[list[str]]) -> dict[str, int]:
    """머리글이 없을 때: 날짜가 가장 많이 든 칸 = 날짜, 글자가 가장 긴 칸 = 제목"""
    width = max((len(r) for r in rows), default=0)
    best, best_n = None, 0
    for c in range(width):
        n = sum(1 for r in rows[:200] if c < len(r) and parse_date(r[c]))
        if n > best_n:
            best, best_n = c, n
    if best is None:
        return {}
    lens = [(sum(len(r[c]) for r in rows[:200] if c < len(r) and not parse_date(r[c])), c)
            for c in range(width) if c != best]
    out = {"date": best}
    if lens:
        out["title"] = max(lens)[1]
    return out


def parse_table(rows: list[list[str]], mapping: dict | None = None) -> dict:
    rows = [[str(c or "").strip() for c in r] for r in rows]
    header_i = _find_header(rows)
    header = rows[header_i] if header_i is not None else []
    auto = guess_mapping(header) if header_i is not None else _guess_without_header(rows)
    use = {k: int(v) for k, v in (mapping or auto).items() if k in FIELDS and v not in (None, "", -1)}
    body = rows[header_i + 1:] if header_i is not None else rows
    width = max((len(r) for r in rows), default=0)
    columns = header + [""] * (width - len(header)) if header else [""] * width
    columns = [c or f"{i + 1}번째 칸" for i, c in enumerate(columns)]
    result = {"columns": columns, "mapping": use, "auto_mapping": auto, "events": [], "warnings": []}
    if "date" not in use or "title" not in use:
        result["warnings"].append("날짜 칸과 제목 칸을 골라 주세요.")
        return result
    year = None
    skipped = 0
    for r in body:
        get = lambda k: r[use[k]] if k in use and use[k] < len(r) else ""   # noqa: E731
        if not any(r):
            continue
        raw_date = get("date")
        d = parse_date(raw_date, year)
        if not d:
            skipped += 1
            continue
        year = d.year
        title = get("title") or _strip_lead(raw_date)
        if not title:
            skipped += 1
            continue
        start = _cell_time(get("start_time")) if "start_time" in use else ""
        end = _cell_time(get("end_time")) if "end_time" in use else ""
        if not start:                               # '2026-11-12 14:00' 처럼 날짜 칸에 시각이 같이 있으면
            start, end2 = _time_range(re.sub(r"^\d{5}$", "", raw_date)) if not re.fullmatch(r"[\d.]+", raw_date) else ("", "")
            if re.fullmatch(r"\d{5}\.\d+", raw_date):
                start = _cell_time(raw_date)
            end = end or end2
        d2 = None
        rng = re.split(r"\s*[~∼〜]\s*", raw_date, maxsplit=1)
        if len(rng) == 2:
            d2 = parse_date(rng[1], d.year)
            if d2 is None and re.fullmatch(r"\d{1,2}\s*일?", rng[1].strip()):
                d2 = _mkdate(d.year, d.month, re.match(r"\d+", rng[1].strip()).group())
        result["events"] += _expand(d, d2, title=title, start=start, end=end,
                                    location=get("location"), note=get("note"))
    if skipped:
        result["warnings"].append(f"날짜를 읽지 못한 줄 {skipped}개는 건너뜁니다.")
    return result


# ───────────────────────── 주간 달력 모양 엑셀 ─────────────────────────
def looks_weekly(rows: list[list[str]]) -> bool:
    has_month = any(r and re.fullmatch(r"\d{4}\.\d{1,2}", (r[0] or "").strip()) for r in rows[:40])
    day_cells = sum(1 for r in rows for c in r[1:8] if re.match(r"^\d{1,2}(?:\n|$)", (c or "").strip()))
    return has_month and day_cells >= 7


def parse_weekly(rows: list[list[str]]) -> dict:
    """맨 왼쪽 칸 '2026.11' + 그 오른쪽 7칸에 '12\\n일정1\\n일정2' 모양의 주간 달력 (학교 연간 계획 등)"""
    year = month = prev_day = None
    events = []
    for r in rows:
        a = (r[0] if r else "").strip()
        if a and re.fullmatch(r"\d{4}\.\d{1,2}", a) and year is None:
            year, month = (int(x) for x in a.split("."))   # 첫 표시만 씀 ('2026.10' 이 숫자로 2026.1 이 되는 문제 피함)
        if year is None:
            continue
        for raw in r[1:8]:
            lines = [ln.strip() for ln in (raw or "").split("\n")]
            if not lines or not re.fullmatch(r"\d{1,2}", lines[0]):
                continue
            day = int(lines[0])
            if prev_day is not None and day < prev_day:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
            prev_day = day
            d = _mkdate(year, month, day)
            if not d:
                continue
            first = True
            for line in lines[1:]:
                if not line:
                    continue
                if line.startswith("(") and not first and events and events[-1]["date"] == d.isoformat():
                    events[-1]["title"] = (events[-1]["title"] + " " + line)[:200]   # '(장소)' 줄은 위 일정에 붙임
                    continue
                first = False
                start, end = _time_range(line)
                events.append(_event(d, line, start, end))
    return {"columns": [], "mapping": {}, "auto_mapping": {}, "events": events, "warnings": []}


# ───────────────────────── 텍스트 ─────────────────────────
def parse_lines(text: str) -> dict:
    """'2026-11-12 14:00 학년 협의회' 한 줄씩, 또는 '11월 12일(목)' 줄 아래 '- 학년 협의회' 줄들"""
    events, skipped, cur, year = [], 0, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        d = parse_date(line, year)
        lead = _D_FULL.match(line) or _D_SHORT.match(line)
        if d and lead:
            year = d.year
            rest = line[lead.end():]
            d2 = None
            m = re.match(r"\s*(?:\([^)]*\))?\s*[~∼〜]\s*(.+)", rest)
            if m:
                tail = m.group(1)
                tail_d = _D_FULL.match(tail) or _D_SHORT.match(tail)
                if tail_d:
                    d2 = parse_date(tail_d.group(0), d.year)
                    rest = tail[tail_d.end():]
                else:
                    dd = re.match(r"(\d{1,2})\s*일?", tail)
                    if dd:
                        d2 = _mkdate(d.year, d.month, dd.group(1))
                        rest = tail[dd.end():]
            title = _strip_lead(rest)
            if not title:                                   # 날짜만 있는 줄 → 아래 줄들의 날짜
                cur = d
                continue
            start, end = _time_range(rest)
            events += _expand(d, d2, title=title, start=start, end=end)
            cur = None
        elif cur and re.match(r"^[-–—·•*▶▷○●◦]|\d{1,2}\s*(?::|시)|오전|오후", line) or (cur and raw[:1] in " \t"):
            title = _strip_lead(line)
            if title:
                start, end = _time_range(line)
                events.append(_event(cur, title, start, end))
        else:
            skipped += 1
    warnings = [f"날짜를 찾지 못한 줄 {skipped}개는 건너뜁니다."] if skipped else []
    return {"columns": [], "mapping": {}, "auto_mapping": {}, "events": events, "warnings": warnings}


def _looks_table_text(text: str) -> bool:
    first = [ln for ln in text.splitlines()[:10] if ln.strip()]
    if len(first) < 2:
        return False
    sep = "\t" if "\t" in first[0] else ","
    return all(ln.count(sep) >= 1 for ln in first[:3]) and bool(guess_mapping(next(csv.reader([first[0]], delimiter=sep))))


def _csv_rows(text: str, path_suffix: str) -> list[list[str]]:
    sample = text[:4000]
    sep = "\t" if path_suffix == ".tsv" or sample.count("\t") > sample.count(",") else ","
    return [r for r in csv.reader(io.StringIO(text), delimiter=sep)]


# ───────────────────────── 입구 ─────────────────────────
def parse_file(path, mapping: dict | None = None) -> dict:
    """{kind, name, columns, mapping, auto_mapping, events, warnings} — 실패하면 ImportError_"""
    p = Path(path)
    if not p.is_file():
        raise ImportError_("파일을 찾을 수 없습니다.")
    suffix = p.suffix.lower()
    if suffix == ".xls":
        raise ImportError_("옛 엑셀(.xls) 파일은 읽지 못합니다. 엑셀에서 '다른 이름으로 저장 → Excel 통합 문서(.xlsx)' 로 바꿔 주세요.")
    if suffix not in SUPPORTED:
        raise ImportError_("읽을 수 있는 파일: .ics(캘린더), .xlsx(엑셀), .csv, .txt")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ImportError_("파일이 너무 큽니다 (20MB 까지).")
    if suffix == ".ics":
        events, warnings = parse_ics(_read_text(p))
        res = {"kind": "ics", "columns": [], "mapping": {}, "auto_mapping": {}, "events": events, "warnings": warnings}
    elif suffix == ".xlsx":
        rows = read_xlsx_rows(p)
        if looks_weekly(rows) and not mapping:
            res = {"kind": "weekly", **parse_weekly(rows)}
        else:
            res = {"kind": "table", **parse_table(rows, mapping)}
    else:
        text = _read_text(p)
        if suffix in (".csv", ".tsv") or _looks_table_text(text):
            res = {"kind": "table", **parse_table(_csv_rows(text, suffix), mapping)}
        else:
            res = {"kind": "text", **parse_lines(text)}
    res["name"] = p.name
    res["events"] = [e for e in res["events"] if e["title"]]
    if len(res["events"]) > MAX_EVENTS:
        res["warnings"].append(f"일정이 너무 많아 앞의 {MAX_EVENTS}건만 가져옵니다.")
        res["events"] = res["events"][:MAX_EVENTS]
    if not res["events"] and not res["warnings"]:
        res["warnings"].append("이 파일에서 일정을 찾지 못했습니다.")
    return res

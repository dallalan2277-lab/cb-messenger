"""일정 제목과 시각을 비교하는 독립적인 중복 후보 판정기."""

from collections import Counter
from datetime import date
import re
import unicodedata


MAYBE_THRESHOLD = 0.4
CONTAINMENT_SCORE = 0.9
MIN_CONTAINMENT_LENGTH = 3

_ENDINGS = ("회의참석", "안내", "개최", "운영", "실시", "알림", "예정", "공지", "관련", "참석")
# 공통 행사 종류만 같은 서로 다른 제목을 동일한 일로 보지 않는다.
_GENERIC_SUFFIXES = ("협의회",)
_YEAR_SEMESTER = re.compile(r"\d{4}\s*학년도|\d{4}\s*년(?:도)?|\d+\s*학기")
_DATE_TIME = re.compile(
    r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|"
    r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}[-./]\d{1,2}|\d{1,2}\s*월|\d{1,2}\s*일|"
    r"\d{1,2}\s*[:시]\s*\d{1,2}(?:\s*분)?|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"
)
_IDENTIFIER = re.compile(r"(\d+)\s*(학년|반|차)")


def _text(value):
    return unicodedata.normalize("NFKC", value if isinstance(value, str) else "").casefold()


def _compact(value):
    return "".join(char for char in value if char.isalnum())


def title_key(title):
    text = _YEAR_SEMESTER.sub("", _text(title))
    text = _compact(text)
    # '회의참석'에서 회의는 일정의 핵심이므로 지우지 않는다.
    while text:
        for ending in _ENDINGS:
            if text.endswith(ending):
                text = text[:-len(ending)] + ("회의" if ending == "회의참석" else "")
                break
        else:
            break
    return text


def identifiers(title):
    text = _DATE_TIME.sub(" ", _YEAR_SEMESTER.sub(" ", _text(title)))
    found = {}
    for match in _IDENTIFIER.finditer(text):
        found.setdefault(match.group(2), set()).add(str(int(match.group(1))))
    # 독립 숫자도 구분하되, 날짜와 시각을 먼저 빼서 오판하지 않는다.
    text = _IDENTIFIER.sub(" ", text)
    numbers = re.findall(r"(?<![\w])\d+(?![\w])", text)
    if numbers:
        found["숫자"] = {str(int(number)) for number in numbers}
    return found


def title_score(a, b):
    left, right = title_key(a), title_key(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= MIN_CONTAINMENT_LENGTH and shorter in longer:
        return CONTAINMENT_SCORE
    left_pairs = Counter(left[i:i + 2] for i in range(len(left) - 1))
    right_pairs = Counter(right[i:i + 2] for i in range(len(right) - 1))
    total = sum(left_pairs.values()) + sum(right_pairs.values())
    return 2 * sum((left_pairs & right_pairs).values()) / total if total else 0.0


def _relation(a, b):
    if not a and not b:
        return "same"
    if not a or not b:
        return "one_empty"
    return "same" if a == b else "differ"


def _time_key(value):
    """'9:00' 과 '09:00' 을 같은 시각으로 (HH:MM 이 아니면 글자 그대로)"""
    text = _text(value).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else text


def time_rel(a, b):
    return _relation(_time_key(a), _time_key(b))


def place_rel(a, b):
    return _relation(_compact(_text(a)), _compact(_text(b)))


def _only_generic_suffix_in_common(left, right):
    for suffix in _GENERIC_SUFFIXES:
        if left.endswith(suffix) and right.endswith(suffix):
            a, b = left[:-len(suffix)], right[:-len(suffix)]
            if a and b and not (set(a) & set(b)):
                return True
    return False


def classify(new: dict, old: dict) -> tuple[str, float, str]:
    new, old = new or {}, old or {}
    left, right = title_key(new.get("title")), title_key(old.get("title"))
    score = title_score(new.get("title"), old.get("title"))
    if not left or not right:
        return "different", score, "제목이 비어 있어 비교할 수 없어요."
    if not new.get("date") or new.get("date") != old.get("date"):
        return "different", score, "날짜가 다르거나 비어 있어요."
    left_ids, right_ids = identifiers(new.get("title")), identifiers(old.get("title"))
    for kind in left_ids.keys() & right_ids.keys():
        if left_ids[kind] != right_ids[kind]:
            return "different", score, "%s 표시가 서로 달라요." % kind
    if _only_generic_suffix_in_common(left, right):
        return "different", score, "행사 종류만 같고 구체적인 제목이 달라요."
    if score < MAYBE_THRESHOLD:
        return "different", score, "제목이 충분히 비슷하지 않아요."
    if left_ids.keys() != right_ids.keys():
        return "maybe", score, "한쪽에만 학년이나 차수 등의 표시가 있어요."
    if time_rel(new.get("start_time"), old.get("start_time")) == "differ":
        return "maybe", score, "시각이 달라 확인이 필요해요."
    if place_rel(new.get("location"), old.get("location")) == "differ":
        return "maybe", score, "장소가 달라 확인이 필요해요."
    if left == right:
        return "same", score, "제목이 같고 시각과 장소가 충돌하지 않아요."
    return "maybe", score, "제목이 비슷해 확인이 필요해요."


def best_matches(new, olds):
    """같은 날짜의 후보를 (기존 일정, 판정, 점수, 이유)로 점수순 반환."""
    matches = []
    for old in olds:
        kind, score, reason = classify(new, old)
        if kind != "different":
            matches.append((old, kind, score, reason))
    return sorted(matches, key=lambda match: match[2], reverse=True)


CHANGE_WORDS = ("변경", "연기", "순연", "앞당", "바뀌", "조정")
# '변동 없습니다'·'문제 없습니다' 같은 말을 취소로 잡지 않게 좁게 둠 (그래도 묻기만 함)
CANCEL_WORDS = ("취소", "열리지 않", "진행하지 않", "개최하지 않", "실시하지 않")


def has_any(text, words):
    return any(word in (text or "") for word in words)


_DATE_PATTERN = re.compile(
    r"(?<![\d./:~\-])(?:"
    r"(?P<ky>\d{4})\s*년\s*(?P<kym>\d{1,2})\s*월\s*(?P<kyd>\d{1,2})\s*일"
    r"|(?P<year>\d{4})[-./](?P<ym>\d{1,2})[-./](?P<yd>\d{1,2})(?:\.(?!\d))?(?![\d/:]|\.\d)"
    r"|(?P<km>\d{1,2})\s*월\s*(?P<kd>\d{1,2})\s*일"
    r"|(?P<nm>\d{1,2})[/.](?P<nd>\d{1,2})(?:\.(?!\d))?(?![\d/.:])(?!\s*(?:시간|분|배|%|퍼센트|점|개|명|원|km|kg|cm|mm))"
    r"|(?P<day>\d{1,2})\s*일(?!\s*(?:간|동안|이내|이상|이하|정도|째|소요|씩))"
    r")(?!\d)"
)


def _nearest_year(month, day, reference):
    best = None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((value - reference).days) < abs((best - reference).days):
            best = value
    return best


def date_mentions(text: str, ref_date: str) -> list[tuple[str, int, int]]:
    """본문 속 날짜 (YYYY-MM-DD, 시작 위치, 끝 위치) 를 나온 순서대로.
    - 연도가 없으면 기준일과 가장 가까운 해. 'YYYY년 M월 D일'·'YYYY-MM-DD' 는 그 해.
    - 월 없이 'D일' 만 있으면 앞에 나온 날짜의 달(없으면 기준일의 달), 앞 날짜보다 작으면 다음 달 ('31일에서 1일로').
    - '3.5시간'·'1~2일'·'3일간' 같은 시간·기간은 날짜로 보지 않음."""
    try:
        reference = date.fromisoformat(ref_date)
    except (ValueError, TypeError):
        return []
    out, last = [], None
    for match in _DATE_PATTERN.finditer(text or ""):
        g = match.groupdict()
        value = None
        try:
            if g["ky"]:
                value = date(int(g["ky"]), int(g["kym"]), int(g["kyd"]))
            elif g["year"]:
                value = date(int(g["year"]), int(g["ym"]), int(g["yd"]))
            elif g["km"] or g["nm"]:
                month, day = int(g["km"] or g["nm"]), int(g["kd"] or g["nd"])
                value = date(last.year, month, day) if last and last.month <= month else _nearest_year(month, day, reference)
            else:
                day = int(g["day"])
                base = last or reference
                year, month = base.year, base.month
                if last and day < last.day:
                    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
                value = date(year, month, day) if last else _nearest_year(month, day, reference)
        except ValueError:
            value = None
        if value is not None:
            out.append((value.isoformat(), match.start(), match.end()))
            last = value
    return out


def dates_in_text(text: str, ref_date: str) -> set[str]:
    """본문의 날짜 집합 (date_mentions 참고)."""
    return {iso for iso, _, _ in date_mentions(text, ref_date)}


_MOVE_LINK = re.compile(r"(에서|→|->|⇒|➝|부터)")


def moved_dates(text: str, ref_date: str) -> set[tuple[str, str]]:
    """'19일에서 20일로', '11/19 → 11/20' 처럼 앞 날짜에서 뒤 날짜로 옮긴다는 짝 (옛 날짜, 새 날짜)."""
    found = set()
    mentions = date_mentions(text, ref_date)
    for (a, _, a_end), (b, b_start, _) in zip(mentions, mentions[1:]):
        between = (text or "")[a_end:b_start]
        if a != b and len(between) <= 15 and _MOVE_LINK.search(between):
            found.add((a, b))
    return found


# 취소처럼 보이지만 취소가 아닌 말 (신청 취소, 취소하지 않음 등) — 미리 지우고 봄
_NOT_CANCEL = re.compile(r"신청\s*취소|취소\s*신청|취소\s*시|취소\s*하지\s*않|취소\s*되지\s*않|취소\s*없|취소\s*문의|취소\s*가능")


def cancel_positions(compact_text: str) -> list[int]:
    """공백을 없앤 본문에서 취소 표현의 위치들 (취소가 아닌 말은 뺌)"""
    cleaned = _NOT_CANCEL.sub(lambda m: "#" * len(m.group(0)), compact_text or "")
    out = []
    for word in CANCEL_WORDS:
        w = word.replace(" ", "")
        start = cleaned.find(w)
        while start >= 0:
            out.append(start)
            start = cleaned.find(w, start + 1)
    return out

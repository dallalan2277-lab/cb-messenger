"""일정 제목과 시각을 비교하는 독립적인 중복 후보 판정기."""

from collections import Counter
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

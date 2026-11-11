"""중복 판정: 모두 실제 메시지와 관계없는 가상 일정."""

import pytest

from app.event_match import (
    MAYBE_THRESHOLD, best_matches, classify, identifiers, place_rel,
    time_rel, title_key, title_score,
)


def event(title, time="", place="", date="2026-11-20"):
    return {"title": title, "date": date, "start_time": time, "location": place}


@pytest.mark.parametrize("left,right,expected", [
    (event("학부모 상담주간 안내"), event("2학기 학부모 상담 주간 운영"), "same"),
    (event("교직원 회의", "14:00"), event("교직원 회의 개최", "14:00"), "same"),
    (event("교직원 회의", "14:00"), event("교직원 회의 개최"), "same"),
    (event("교직원 회의", "14:00"), event("교직원 회의", "15:00"), "maybe"),
    (event("교직원 회의", "14:00"), event("교직원 회의", "14:30"), "maybe"),
    (event("교직원 회의", place="시청각실"), event("교직원 회의", place="도서관"), "maybe"),
    (event("2학기 학부모 상담"), event("학부모 상담"), "same"),
    (event("학부모 상담"), event("1학년 학부모 상담"), "maybe"),
    (event("2026학년도 회의"), event("회의"), "same"),
    (event("1학년 학부모 상담"), event("3학년 학부모 상담"), "different"),
    (event("교과협의회"), event("학년협의회"), "different"),
    (event("1차 협의회"), event("2차 협의회"), "different"),
    (event("2반 상담"), event("3반 상담"), "different"),
    (event("교직원 회의"), event("교직원 회의", date="2026-11-21"), "different"),
    (event("교직원 회의", date=""), event("교직원 회의", date=""), "different"),
    (event("학부모 상담 추가"), event("학부모 상담"), "maybe"),
])
def test_classify_examples(left, right, expected):
    kind, score, reason = classify(left, right)
    assert kind == expected
    assert 0 <= score <= 1
    assert reason
    assert classify(right, left)[0] == expected


def test_all_candidates_are_returned_in_score_order():
    new = event("교직원 회의")
    morning, afternoon = event("교직원 회의", "09:00"), event("교직원 회의", "15:00")
    other = event("교직원 회의", date="2026-11-21")
    similar = event("교직원 회의 준비")
    matches = best_matches(new, [similar, afternoon, other, morning])
    assert [item[0] for item in matches] == [afternoon, morning, similar]
    assert [item[1] for item in matches] == ["same", "same", "maybe"]
    assert all(len(item) == 4 for item in matches)


def test_identifiers_exclude_calendar_numbers():
    assert identifiers("2026학년도 2학기 11월 20일 14:30 1학년 2반 3차") == {
        "학년": {"1"}, "반": {"2"}, "차": {"3"}}
    assert identifiers("2026-11-20 14시 30분 회의") == {}
    assert identifiers("11/20 20일 11월 회의") == {}
    assert identifiers("1차 및 2차 협의회") == {"차": {"1", "2"}}
    assert classify(event("1차 및 2차 협의회"), event("1차 협의회"))[0] == "different"


def test_normalization_and_relations():
    assert title_key(" 2026학년도 (교직원 회의 참석) ") == "교직원회의"
    assert title_key("교직원 회의") == "교직원회의"
    assert time_rel("", "") == "same"
    assert time_rel(None, "14:00") == "one_empty"
    assert time_rel("14:00", "14:30") == "differ"
    assert place_rel("시청각 실", "시청각실") == "same"
    assert place_rel(None, "도서관") == "one_empty"
    assert place_rel("도서관", "시청각실") == "differ"
    assert place_rel("회의실", "실") == "differ"


def test_title_score_dice_and_containment():
    assert MAYBE_THRESHOLD == 0.4
    assert title_score("가나다라", "나다라마") == pytest.approx(2 / 3)
    assert title_score("가나다", "가나다라") == 0.9
    assert title_score("ab", "abc") == pytest.approx(2 / 3)
    assert title_score("a", "a") == 1.0
    assert title_score("a", "b") == 0.0


@pytest.mark.parametrize("title", [None, "", "   ", "()", "（ ）", "(안내)"])
def test_empty_titles_are_different(title):
    assert classify(event(title), event(title))[0] == "different"
    assert classify(event(title), event("회의"))[0] == "different"
    assert title_score(title, "회의") == 0.0


def test_english_numbers_and_long_titles():
    assert classify(event("Project 123"), event("PROJECT 123"))[0] == "same"
    assert classify(event("Project 123"), event("Project 456"))[0] == "different"
    assert classify(event("123"), event("123"))[0] == "same"
    long_title = "가상교육계획" * 2000
    assert classify(event(long_title), event(long_title + " 안내"))[0] == "same"


def test_time_with_and_without_leading_zero_is_same():
    assert time_rel("9:00", "09:00") == "same"
    assert time_rel("9:00", "09:30") == "differ"

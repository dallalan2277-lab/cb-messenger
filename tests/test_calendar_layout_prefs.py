"""달력 칸 배치 환경설정의 허용값 검증."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.desktop_calendar.api import CalendarAPI, DEFAULT_PREFS
from app.storage import Storage


def _api(tmp_path):
    return CalendarAPI(Storage(str(tmp_path / "calendar.db")), tmp_path / "prefs.json")


@pytest.mark.parametrize(("layout", "lines"), [
    ("fit", 4),
    ("expanded", 6),
    ("expanded", 8),
])
def test_set_prefs_saves_valid_calendar_layout_values(tmp_path, layout, lines):
    api = _api(tmp_path)

    saved = api.set_prefs({"calendar_layout": layout, "calendar_lines": lines})

    assert saved["calendar_layout"] == layout
    assert saved["calendar_lines"] == lines
    assert _api(tmp_path).get_prefs()["calendar_layout"] == layout
    assert _api(tmp_path).get_prefs()["calendar_lines"] == lines


@pytest.mark.parametrize(("layout", "lines", "expected_layout", "expected_lines"), [
    ("wide", 6, DEFAULT_PREFS["calendar_layout"], 6),
    ("expanded", 5, "expanded", DEFAULT_PREFS["calendar_lines"]),
    (None, "6", DEFAULT_PREFS["calendar_layout"], DEFAULT_PREFS["calendar_lines"]),
])
def test_set_prefs_resets_invalid_calendar_layout_values_to_defaults(
        tmp_path, layout, lines, expected_layout, expected_lines):
    api = _api(tmp_path)
    api.set_prefs({"calendar_layout": "expanded", "calendar_lines": 8})

    saved = api.set_prefs({"calendar_layout": layout, "calendar_lines": lines})

    assert saved["calendar_layout"] == expected_layout
    assert saved["calendar_lines"] == expected_lines
    assert _api(tmp_path).get_prefs()["calendar_layout"] == expected_layout
    assert _api(tmp_path).get_prefs()["calendar_lines"] == expected_lines


def test_expanded_layout_sizes_every_week_by_chosen_lines():
    """넓게 4·6·8줄은 모든 주를 그 줄 수 높이로 맞춰야 한다.
    (예전엔 일정이 N개 넘는 주만 키워서, 일정이 적은 달은 골라도 화면이 그대로였다.)"""
    html = (Path(__file__).resolve().parent.parent
            / "app" / "desktop_calendar" / "calendar.html").read_text(encoding="utf-8")
    assert "rowHeightForLines(currentLines(), metrics)" in html
    assert "Math.min(currentLines(), counts[index])" not in html


@pytest.mark.parametrize(("px", "expected"), [
    (120, 120),
    (150.6, 151),
    (5, 40),          # 너무 작으면 최소 40
    (9999, 600),      # 너무 크면 최대 600
    ("120", DEFAULT_PREFS["calendar_row_px"]),
    (True, DEFAULT_PREFS["calendar_row_px"]),
    (float("nan"), DEFAULT_PREFS["calendar_row_px"]),
    (float("inf"), DEFAULT_PREFS["calendar_row_px"]),
    (None, DEFAULT_PREFS["calendar_row_px"]),
])
def test_custom_row_height_is_clamped(tmp_path, px, expected):
    """주 사이 경계를 끌어 정한 칸 높이(직접 맞춤)는 40~600 정수로만 저장."""
    api = _api(tmp_path)
    saved = api.set_prefs({"calendar_layout": "custom", "calendar_row_px": px})
    assert saved["calendar_layout"] == "custom"
    assert saved["calendar_row_px"] == expected
    assert _api(tmp_path).get_prefs()["calendar_row_px"] == expected


def test_menu_has_folded_window_place_list_and_row_resizers():
    html = (Path(__file__).resolve().parent.parent
            / "app" / "desktop_calendar" / "calendar.html").read_text(encoding="utf-8")
    assert 'id="placeList"' in html and 'id="placeBtn"' in html   # 창 위치·크기는 접어 둠
    assert "addRowResizers(grid, heights, gap)" in html             # 주 사이 경계 끌기

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

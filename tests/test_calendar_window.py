"""바탕화면 캘린더 창 동작 테스트 (가짜 Win32, Windows 없이)

2026-11-18 제보: ① 트레이 아이콘을 눌러도 다른 창에 가려 안 보임 ② 창이 화면 위로 밀려 다시 잡을 수 없음
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.desktop_calendar.widget import BAR_PX, BAR_VISIBLE, Host, keep_bar_on_screen

AREA = (0, 0, 1920, 1040)


# ───────── keep_bar_on_screen (순수 계산) ─────────
def test_inside_geometry_unchanged():
    assert keep_bar_on_screen((600, 100, 1200, 800), [AREA]) == (600, 100, 1200, 800)


def test_top_bar_cannot_go_above_screen():
    x, y, w, h = keep_bar_on_screen((1500, -300, 1200, 800), [AREA])
    assert y == 0 and (w, h) == (1200, 800)
    assert x + BAR_VISIBLE <= 1920                    # 막대가 오른쪽으로도 잡을 만큼 남음


def test_bar_kept_visible_at_bottom_and_sides():
    assert keep_bar_on_screen((100, 2000, 800, 600), [AREA])[1] == 1040 - BAR_PX
    assert keep_bar_on_screen((-5000, 100, 800, 600), [AREA])[0] == -800 + BAR_VISIBLE
    assert keep_bar_on_screen((9000, 100, 800, 600), [AREA])[0] == 1920 - BAR_VISIBLE


def test_picks_nearest_monitor_and_allows_spanning():
    areas = [(0, 0, 1920, 1040), (1920, 0, 2560, 1400)]
    assert keep_bar_on_screen((1500, 100, 1200, 800), areas) == (1500, 100, 1200, 800)   # 두 모니터에 걸쳐도 그대로
    x, y, _, _ = keep_bar_on_screen((3000, -500, 1200, 800), areas)
    assert (x, y) == (3000, 0)                        # 오른쪽 모니터 안으로


def test_floats_and_no_areas():
    assert keep_bar_on_screen((10.6, 20.4, 800, 600), []) == (11, 20, 800, 600)


# ───────── Host (가짜 Win32) ─────────
class FakeW32:
    def __init__(self, rect=(600, 100, 1200, 800)):
        self._rect = list(rect)
        self.areas = [AREA]
        self.minimized = False
        self.fg = (999, "Chrome_WidgetWin_1")         # 브라우저가 앞에 있음
        self.z = []
        self.activate_ok = True

    def rect(self, hwnd):
        return tuple(self._rect)

    def place(self, hwnd, x, y, w, h):
        self._rect = [int(x), int(y), int(w), int(h)]

    def work_areas(self):
        return list(self.areas)

    def is_minimized(self, hwnd):
        return self.minimized

    def is_visible(self, hwnd):
        return True

    def restore_noactivate(self, hwnd):
        self.minimized = False

    def foreground_class(self):
        return self.fg

    def to_top_noactivate(self, hwnd):
        self.z.append("top")

    def to_bottom(self, hwnd):
        self.z.append("bottom")

    def activate(self, hwnd):
        self.z.append("activate")
        if self.activate_ok:
            self.fg = (hwnd, "WindowsForms10.Window")
        return self.activate_ok


class FakeAPI:
    def __init__(self):
        self.prefs = {}

    def get_prefs(self):
        return dict(self.prefs)

    def set_prefs(self, p):
        self.prefs.update(p)
        return dict(self.prefs)


class FakeWindow:
    def __init__(self):
        self.shown = 0

    def show(self):
        self.shown += 1

    def hide(self):
        pass


def _host(**kw):
    h = Host.__new__(Host)                              # Win32() 를 만들지 않음 (Windows 없이)
    h.api, h.w32, h.hwnd, h.window = FakeAPI(), FakeW32(**kw), 1234, FakeWindow()
    h._init_state()
    return h


def test_tray_show_brings_calendar_to_front_not_bottom():
    h = _host()
    assert h.show() is True
    assert h.window.shown == 1
    assert "activate" in h.w32.z and "bottom" not in h.w32.z
    h._tick()                                           # 캘린더가 입력을 받는 중 → 그대로 둠
    assert h.w32.z[-1] == "activate"
    h.w32.fg = (999, "Chrome_WidgetWin_1")              # 사용자가 브라우저를 누름 → 다시 바탕화면 쪽으로
    h._tick()
    assert h.w32.z[-1] == "bottom"


def test_tray_show_keeps_on_top_briefly_when_activation_refused():
    h = _host()
    h.w32.activate_ok = False
    h.show()
    h._tick()
    assert h.w32.z[-1] == "top"                         # 잠깐(PEEK_SEC) 앞에 보여 줌
    h._peek_until = 0.0                                 # 시간이 지나면
    h._tick()
    assert h.w32.z[-1] == "bottom"


def test_drag_cannot_push_bar_off_screen():
    h = _host(rect=(600, 100, 1200, 800))
    h.begin_drag(1.25)
    h.drag_to(900, -700)                                # 오른쪽 위로 세게 끎
    x, y, w, h_ = h.w32.rect(0)
    assert y == 0 and x + BAR_VISIBLE <= 1920 and (w, h_) == (1200, 800)
    assert h.end_drag() is True
    assert h.api.prefs["y"] == 0


def test_resize_cannot_push_off_screen():
    h = _host(rect=(600, 100, 1200, 800))
    h.begin_drag(1.0)
    h.resize_drag("w", 5000, 0)                         # 왼쪽 가장자리를 오른쪽 끝 너머로
    x, _, w, _ = h.w32.rect(0)
    assert x + BAR_VISIBLE <= 1920 and w == Host.MIN_W
    h.end_drag()


def test_late_end_drag_is_ignored_and_moves_after_end_do_nothing():
    h = _host()
    h.begin_drag(1.0)
    assert h.end_drag() is True
    before = h.w32.rect(0)
    h.api.prefs.clear()
    assert h.end_drag() is False                        # 늦게 온 '끝' 신호: 다시 저장하지 않음
    assert h.drag_to(500, 500) is False                 # 끝난 뒤의 이동은 무시
    assert h.w32.rect(0) == before and h.api.prefs == {}


def test_stale_drag_state_is_released_by_tick():
    h = _host()
    h.begin_drag(1.0)
    h._tick()
    assert h._dragging is True                          # 방금 시작했으면 건드리지 않음
    h._drag_ts -= Host.STALE_DRAG_SEC + 1               # 놓은 신호를 놓쳐 오래 멈춤
    h._tick()
    assert h._dragging is False                         # 1초 점검을 다시 시작 (끌기 번호는 남겨 이어질 수 있게)
    assert h.w32.z[-1] == "bottom"                      # 다시 평소처럼 바탕화면 쪽으로


def test_tick_moves_offscreen_window_back():
    h = _host(rect=(1500, -400, 1200, 800))             # 절전·해상도 변화 등으로 화면 위로 나감
    h._tick()
    assert h.w32.rect(0)[1] == 0 and h.api.prefs["y"] == 0


def test_tick_ignores_minimized_rect_until_restored():
    h = _host(rect=(-32000, -32000, 160, 28))
    h.w32.minimized = True

    def restore(hwnd):                                  # 되살리면 원래 위치로 돌아옴
        h.w32.minimized = False
        h.w32._rect = [600, 100, 1200, 800]

    h.w32.restore_noactivate = restore
    h._tick()
    assert h.w32.rect(0) == (600, 100, 1200, 800)


def test_snap_clears_stuck_drag_restores_and_shows_once():
    h = _host(rect=(1500, -400, 1200, 800))
    h.w32.minimized = True
    h.begin_drag(1.0)                                   # 끌기 상태가 남은 채로
    assert h.snap("right") is True
    assert h._dragging is False and h._drag is None and h.w32.minimized is False
    x, y, w, hh = h.w32.rect(0)
    assert x + w == 1920 - 12 and y == 12               # 한 번에 오른쪽 2/3 로
    assert h.w32.z[-1] == "activate"                    # 가려 있지 않게 앞으로
    assert h.drag_to(500, 500) is False                 # 남은 이동 요청이 되돌린 위치를 덮어쓰지 않음
    assert h.w32.rect(0) == (x, y, w, hh)


def test_tray_peek_ends_when_user_clicks_another_app_before_first_check():
    h = _host()
    h.w32.activate_ok = False                           # Windows 가 앞으로 꺼내기를 막음 → 잠깐 위에 보여 둠
    h.w32.fg = (55, "Shell_TrayWnd")                   # 트레이를 누른 상태
    h.show()
    h._tick()
    assert h.w32.z[-1] == "top"
    h.w32.fg = (777, "Notepad")                         # 바로 다른 앱을 누름
    h._tick()
    assert h.w32.z[-1] == "bottom"


def test_late_requests_from_old_drag_are_ignored():
    h = _host(rect=(600, 100, 1200, 800))
    t1 = h.begin_drag(1.0)
    t2 = h.begin_drag(1.0)                              # 새 끌기가 시작됨
    assert t1 and t2 and t1 != t2
    assert h.end_drag(t1) is False                      # 지난 끌기의 늦은 '끝' → 새 끌기를 끝내지 않음
    assert h.drag_to(10, 10, t1) is False
    assert h.drag_to(10, 10, t2) is True and h.w32.rect(0)[:2] == (610, 110)
    assert h.end_drag(t2) is True


def test_snap_invalidates_inflight_drag_token():
    h = _host()
    t = h.begin_drag(1.0)
    h.snap("right")
    snapped = h.w32.rect(0)
    assert h.drag_to(-900, -900, t) is False and h.w32.rect(0) == snapped


def test_minimized_coordinates_are_never_saved():
    h = _host()
    t = h.begin_drag(1.0)
    h.w32.minimized, h.w32._rect = True, [-32000, -32000, 160, 28]   # 끄는 중 Win+D
    h.api.prefs.clear()
    assert h.end_drag(t) is True
    assert "x" not in h.api.prefs
    h.w32.minimized = False                             # 최소화 표시가 풀렸는데 좌표가 아직 남은 순간
    h._fix_offscreen()
    assert "x" not in h.api.prefs and h.w32.rect(0)[0] == -32000


def test_snap_after_hide_turns_auto_check_back_on():
    h = _host()
    h.hide()
    h.snap("right")
    assert h.visible is True
    h.w32.fg = (999, "Chrome_WidgetWin_1")
    h._tick()
    assert h.w32.z[-1] == "bottom"


def test_held_button_after_stale_release_continues_drag():
    h = _host(rect=(600, 100, 1200, 800))
    t = h.begin_drag(1.0)
    h._drag_ts -= Host.STALE_DRAG_SEC + 1               # 버튼을 누른 채 오래 멈춤
    h._tick()
    assert h._dragging is False
    assert h.drag_to(20, 0, t) is True and h._dragging is True   # 다시 움직이면 이어짐


def test_monitor_choice_follows_bar_not_center():
    from app.desktop_calendar.widget import bar_area_index
    areas = [(0, 0, 1920, 1040), (-1280, 0, 1280, 1400)]
    # 막대 왼쪽 300px 만 보조 화면(왼쪽)에, 창 가운데는 주 화면
    assert bar_area_index((-300, 100, 1200, 800), areas) == 0
    assert bar_area_index((-1000, 100, 1200, 800), areas) == 1
    # 높이가 다른 화면: 막대가 큰 화면 아래쪽에 보이는 위치는 그대로
    assert keep_bar_on_screen((-1200, 1200, 1000, 600), areas) == (-1200, 1200, 1000, 600)

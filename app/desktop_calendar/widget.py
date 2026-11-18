"""바탕화면 캘린더 위젯 (Windows) — 테두리 없는 창을 항상 모든 창의 맨 아래에 붙여 둠.

- 보기에는 바탕화면의 일부처럼 보이고, 클릭·글자 입력은 보통 창처럼 됨
  (진짜 배경 속(WorkerW)에 넣으면 입력이 안 되므로 Rainmeter 의 '바탕화면에 붙이기'와 같은 방식을 씀)
- 작업 표시줄·Alt+Tab 에 나타나지 않음. 트레이 아이콘으로 보이기/숨기기/끝내기
- 기본 크기: 고른 모니터 작업 영역의 2/3 (오른쪽). 끌어서 옮기거나 가장자리로 크기를 바꾸면 기억함
- Win+D(바탕화면 보기)로 숨겨져도 다시 나타남
- 트레이 아이콘을 누르면(또는 '되돌리기') 잠깐 맨 앞으로 꺼내 보여 주고, 다른 창을 누르면 다시 바탕화면 쪽으로
- 위쪽 막대(창을 잡는 곳)는 늘 화면 안에 남게 함 — 화면 밖으로 나가 다시 잡을 수 없게 되는 일 막기
- --watch 와 함께 켜면 소통메신저 감시도 같이 돌리고, 새 일정이 생기면 바로 다시 그림

필요: pip install pywebview pystray pillow  (requirements.txt 에 포함, Windows 11 은 WebView2 기본 내장)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .api import CalendarAPI

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
TITLE = "충북메신저AI_바탕화면캘린더"
DESKTOP_CLASSES = ("Progman", "WorkerW")
SNAP = {  # (가로 비율, 세로 비율, 가로 위치)
    "right": (0.66, 1.0, "right"),
    "left": (0.66, 1.0, "left"),
    "center": (0.66, 1.0, "center"),
    "full": (1.0, 1.0, "left"),
}


# ───────────────────────── Win32 ─────────────────────────
class Win32:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.windll.user32
        self.user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        self.user32.GetWindowLongPtrW.restype = ctypes.c_void_p

    @staticmethod
    def dpi_aware():
        """좌표를 실제 화면 픽셀로 맞춤 (배율 125%·150% 모니터에서도 크기가 맞게). 창 만들기 전에 한 번"""
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def work_areas(self) -> list[tuple[int, int, int, int]]:
        """모니터별 작업 영역(작업 표시줄 제외) [(x, y, w, h)], 주 모니터가 먼저"""
        ct, wt = self.ct, self.wt

        class MONITORINFO(ct.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        out = []
        PROC = ct.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)

        def cb(hmon, hdc, rect, lp):
            mi = MONITORINFO()
            mi.cbSize = ct.sizeof(MONITORINFO)
            if self.user32.GetMonitorInfoW(hmon, ct.byref(mi)):
                r = mi.rcWork
                out.append(((mi.dwFlags & 1) == 0, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
            return True

        self.user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
        out.sort(key=lambda t: (t[0], t[1][0]))          # 주 모니터(flag=1) 먼저, 나머지는 왼쪽부터
        return [a for _, a in out] or [(0, 0, 1920, 1040)]

    def find(self, title: str) -> int:
        return int(self.user32.FindWindowW(None, title) or 0)

    def rect(self, hwnd) -> tuple[int, int, int, int]:
        r = self.wt.RECT()
        self.user32.GetWindowRect(hwnd, self.ct.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def place(self, hwnd, x, y, w, h):
        SWP_NOZORDER, SWP_NOACTIVATE = 0x4, 0x10
        self.user32.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE)

    def to_bottom(self, hwnd):
        self.user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)   # HWND_BOTTOM, NOSIZE|NOMOVE|NOACTIVATE

    def to_top_noactivate(self, hwnd):
        self.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)   # HWND_TOP

    def hide_from_taskbar(self, hwnd):
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
        st = int(self.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
        self.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (st | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)

    def set_alpha(self, hwnd, alpha: float):
        """창 전체 투명도 (0.3~1). WebView2 창은 '배경만 투명'이 Windows 에서 안 돼서(뒤에 회색 바탕이 비침)
        창 전체를 겹침 창(WS_EX_LAYERED)으로 만들어 바탕화면이 비쳐 보이게 함 — 글자도 같이 옅어짐."""
        GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x80000, 0x2
        a = max(0.3, min(1.0, float(alpha)))
        st = int(self.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
        if not st & WS_EX_LAYERED:
            self.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, st | WS_EX_LAYERED)
        return bool(self.user32.SetLayeredWindowAttributes(hwnd, 0, int(round(a * 255)), LWA_ALPHA))

    def foreground_class(self) -> tuple[int, str]:
        h = self.user32.GetForegroundWindow()
        buf = self.ct.create_unicode_buffer(64)
        self.user32.GetClassNameW(h, buf, 64)
        return int(h or 0), buf.value

    def is_minimized(self, hwnd) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def restore_noactivate(self, hwnd):
        self.user32.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE

    def is_visible(self, hwnd) -> bool:
        return bool(self.user32.IsWindowVisible(hwnd))

    def activate(self, hwnd) -> bool:
        """맨 앞으로 올리고 입력을 받게 해 봄. Windows 가 막으면(다른 프로그램이 앞에 있을 때) 억지로 하지 않음
        — 그때는 Host 가 잠깐(PEEK_SEC) 앞에 보여 두고, 사용자가 캘린더를 누르면 그대로 쓸 수 있음.
        (다른 프로그램 입력에 끼어드는 AttachThreadInput 은 그 프로그램이 멈추면 같이 멈출 수 있어 쓰지 않음)"""
        self.to_top_noactivate(hwnd)
        return bool(self.user32.SetForegroundWindow(hwnd))


def initial_geometry(area, fraction=0.66, anchor="right", margin=12):
    """작업 영역 (x, y, w, h) 안에서 기본 위치·크기"""
    x, y, w, h = area
    ww = int(w * fraction) - margin * 2 if fraction < 1 else w - margin * 2
    hh = h - margin * 2
    if anchor == "left":
        xx = x + margin
    elif anchor == "center":
        xx = x + (w - ww) // 2
    else:
        xx = x + w - ww - margin
    return xx, y + margin, max(ww, 480), max(hh, 360)


BAR_PX = 60          # 위쪽 막대(창을 잡는 곳) 높이: 화면 40px × 배율 150% 까지 넉넉히 (배율을 알면 bar_px)
BAR_VISIBLE = 120    # 막대 가로로 최소 이만큼은 화면 안에
MINIMIZED_XY = -30000  # 최소화된 창의 좌표는 (-32000, -32000) — 저장·보정하면 안 됨


def bar_px(dpr=None) -> int:
    """위쪽 막대 높이(화면 40px + 여유)를 실제 픽셀로"""
    try:
        return max(BAR_PX, int(round(44 * float(dpr))))
    except (TypeError, ValueError):
        return BAR_PX


def bar_area_index(geom, areas, bar=BAR_PX) -> int:
    """위쪽 막대가 가장 많이 걸친 모니터 (안 걸치면 막대 가운데에서 가장 가까운 모니터)"""
    x, y, w, _ = geom
    best, best_overlap = 0, 0
    for i, (ax, ay, aw, ah) in enumerate(areas):
        ov = max(0, min(x + w, ax + aw) - max(x, ax)) * max(0, min(y + bar, ay + ah) - max(y, ay))
        if ov > best_overlap:
            best, best_overlap = i, ov
    if best_overlap:
        return best
    px, py = x + w // 2, y + bar // 2

    def dist(i):
        ax, ay, aw, ah = areas[i]
        dx = max(ax - px, 0, px - (ax + aw - 1))
        dy = max(ay - py, 0, py - (ay + ah - 1))
        return dx * dx + dy * dy

    return min(range(len(areas)), key=dist)


def keep_bar_on_screen(geom, areas, bar=BAR_PX, visible=BAR_VISIBLE):
    """창을 다시 잡을 수 있게 위쪽 막대를 모니터 작업 영역 안으로 (크기는 그대로).
    위로는 작업 영역 맨 위보다 못 올라가고, 아래로는 막대가 보일 만큼만, 좌우로는 막대가 visible 만큼 남게."""
    x, y, w, h = (int(round(v)) for v in geom)
    if not areas:
        return x, y, w, h
    vis = min(visible, w)
    ax, ay, aw, ah = areas[bar_area_index((x, y, w, h), areas, bar)]
    ny = min(max(y, ay), ay + max(ah - bar, 0))
    nx = min(max(x, ax - w + vis), ax + aw - vis)
    return nx, ny, w, h


def clamp_to_areas(geom, areas, min_visible=80):
    """저장된 위치가 지금 모니터 구성 밖이면(모니터 하나 뺐을 때 등) None"""
    x, y, w, h = geom
    for ax, ay, aw, ah in areas:
        if x + w - min_visible > ax and x + min_visible < ax + aw and y + min_visible > ay - 40 and y < ay + ah - min_visible:
            return geom
    return None


# ───────────────────────── 창 조작 (화면 JS 가 api.win(...) 으로 부름) ─────────────────────────
class Host:
    """창 조작. 트레이 스레드·화면(js_api) 스레드·1초 점검 스레드가 함께 부르므로 창 위치·끌기 상태는 _lock 안에서만 바꿈"""
    MIN_W, MIN_H = 480, 360
    STALE_DRAG_SEC = 10.0   # 끌기 신호가 이만큼 끊기면(놓은 신호를 놓친 경우) 자동 점검을 다시 시작
    PEEK_SEC = 5.0          # 트레이로 꺼냈는데 입력을 못 받았을 때 앞에 보여 두는 시간
    SHELL_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                     "TopLevelWindowForOverflowXamlIsland", "Xaml_WindowedPopupClass")

    def __init__(self, api: CalendarAPI):
        self.api = api
        self.w32 = Win32()
        self.hwnd = 0
        self.window = None
        self._init_state()

    def _init_state(self):
        self._lock = threading.RLock()
        self._drag = None          # (시작 위치, 배율, 작업 영역) — 끌기 번호가 맞을 때만 씀
        self._drag_id = 0
        self._dragging = False     # True 동안 1초 점검이 창을 건드리지 않음
        self._drag_ts = 0.0
        self._peek_until = 0.0
        self._dpr = 1.5
        self.visible = True

    # -- 시작 시 배치
    def setup(self):
        for _ in range(50):
            self.hwnd = self.w32.find(TITLE)
            if self.hwnd:
                break
            time.sleep(0.1)
        if not self.hwnd:
            log.error("캘린더 창을 찾지 못했습니다.")
            return
        self.w32.hide_from_taskbar(self.hwnd)
        p = self.api.get_prefs()
        areas = self.w32.work_areas()
        geom = None
        if p.get("w") and p.get("h") and int(p.get("x", 0)) > MINIMIZED_XY:
            geom = clamp_to_areas((p["x"], p["y"], p["w"], p["h"]), areas)
        if geom is None:
            mon = min(int(p.get("monitor", 0)), len(areas) - 1)
            geom = initial_geometry(areas[mon], p.get("fraction", 0.66), p.get("anchor", "right"))
        self.w32.place(self.hwnd, *keep_bar_on_screen(geom, areas, bar_px(self._dpr)))
        self.set_opacity(p.get("opacity", 0.92))
        self.w32.to_bottom(self.hwnd)
        threading.Thread(target=self._keep_on_desktop, daemon=True).start()

    def _keep_on_desktop(self):
        """1초마다 _tick"""
        while True:
            time.sleep(1.0)
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                log.debug("keep_on_desktop: %s", e)

    def _tick(self):
        """최소화되면 되살리고, 화면 밖이면 되돌리고, 바탕화면을 보는 중·트레이로 꺼낸 직후면 위로, 아니면 맨 아래로"""
        with self._lock:
            if not self.hwnd or not self.visible:
                return
            if self._dragging:
                if time.monotonic() - self._drag_ts < self.STALE_DRAG_SEC:
                    return
                log.info("끌기 신호가 끊겨 자동 점검을 다시 시작합니다.")
                self._dragging = False         # _drag 는 남겨 둠: 버튼을 누른 채 멈췄던 거면 다음 이동에서 이어짐
            self._restore_if_hidden()
            self._fix_offscreen()
            fg, cls = self.w32.foreground_class()
            if fg == self.hwnd:
                self._peek_until = 0.0         # 캘린더를 쓰는 중 → 다른 창을 누를 때까지 그대로
                return
            peeking = time.monotonic() < self._peek_until and (cls in self.SHELL_CLASSES or fg == self._peek_from)
            if cls in DESKTOP_CLASSES or peeking:
                self.w32.to_top_noactivate(self.hwnd)   # Win+D / 바탕화면 클릭 / 트레이로 막 꺼냄 → 보이게
            else:
                self._peek_until = 0.0         # 다른 창을 눌렀거나 시간이 지남 → 다시 바탕화면 쪽으로
                self.w32.to_bottom(self.hwnd)

    def _restore_if_hidden(self):
        if self.w32.is_minimized(self.hwnd) or not self.w32.is_visible(self.hwnd):
            self.w32.restore_noactivate(self.hwnd)

    def _normal_rect(self):
        """최소화 중이면 None (그때 좌표 (-32000,-32000) 은 저장·보정하면 안 됨)"""
        if self.w32.is_minimized(self.hwnd):
            return None
        r = tuple(self.w32.rect(self.hwnd))
        return None if r[0] <= MINIMIZED_XY or r[1] <= MINIMIZED_XY else r

    def _fix_offscreen(self):
        """절전·해상도 변경·끌기 실수로 위쪽 막대가 화면 밖에 있으면 안으로 되돌림"""
        cur = self._normal_rect()
        if cur is None:
            return
        fixed = keep_bar_on_screen(cur, self.w32.work_areas(), bar_px(self._dpr))
        if tuple(fixed) != cur:
            log.info("캘린더 창이 화면 밖에 있어 되돌립니다: %s → %s", cur, fixed)
            self.w32.place(self.hwnd, *fixed)
            self._save()

    def peek(self):
        """잠깐 맨 앞으로 꺼냄 (트레이 클릭·되돌리기). 캘린더를 누르면 계속 쓰고, 다른 창을 누르면 다시 바탕화면 쪽으로"""
        with self._lock:
            if not self.hwnd:
                return False
            if not self.visible:
                self.visible = True
                if self.window:
                    self.window.show()
            self._restore_if_hidden()
            self._peek_from = self.w32.foreground_class()[0]
            self._peek_until = time.monotonic() + self.PEEK_SEC
            try:
                ok = self.w32.activate(self.hwnd)
            except Exception as e:  # noqa: BLE001
                log.debug("peek: %s", e)
                ok = False
            if ok:
                self._peek_until = 0.0   # 입력을 받는 앞 창이 됨 → 다른 창을 누르면 바로 뒤로 (_tick)
            return ok

    _peek_from = 0

    def _save(self):
        r = self._normal_rect()
        if r is None:
            return False
        x, y, w, h = r
        self.api.set_prefs({"x": x, "y": y, "w": w, "h": h})
        return True

    # -- JS 에서 부르는 것들
    def set_opacity(self, alpha=0.92):
        if not self.hwnd:
            return False
        try:
            return self.w32.set_alpha(self.hwnd, alpha)
        except (TypeError, ValueError, OSError) as e:
            log.debug("set_opacity: %s", e)
            return False

    def begin_drag(self, dpr=1.0):
        """끌기 시작. 끌기 번호를 돌려줌 — 화면은 이 번호를 drag_to·resize_drag·end_drag 에 넘김 (늦게 온 지난 요청 거르기)"""
        with self._lock:
            rect = self._normal_rect()
            if rect is None:
                return 0
            try:
                self._dpr = float(dpr or 1.0)
            except (TypeError, ValueError):
                self._dpr = 1.0
            self._drag_id += 1
            self._dragging = True
            self._drag_ts = time.monotonic()
            self._drag = (rect, self._dpr, self.w32.work_areas())
            return self._drag_id

    def _current_drag(self, token):
        if not self._drag or (token is not None and token != self._drag_id):
            return None
        self._dragging = True
        self._drag_ts = time.monotonic()
        return self._drag

    def drag_to(self, dx, dy, token=None):
        with self._lock:
            d = self._current_drag(token)
            if not d:
                return False
            (x, y, w, h), s, areas = d
            self.w32.place(self.hwnd, *keep_bar_on_screen((x + dx * s, y + dy * s, w, h), areas, bar_px(s)))
            return True

    def resize_drag(self, direction, dx, dy, token=None):
        with self._lock:
            d = self._current_drag(token)
            if not d:
                return False
            (x, y, w, h), s, areas = d
            dx, dy = dx * s, dy * s
            nx, ny, nw, nh = x, y, w, h
            if "e" in direction:
                nw = max(self.MIN_W, w + dx)
            if "s" in direction:
                nh = max(self.MIN_H, h + dy)
            if "w" in direction:
                nw = max(self.MIN_W, w - dx)
                nx = x + (w - nw)
            self.w32.place(self.hwnd, *keep_bar_on_screen((nx, ny, nw, nh), areas, bar_px(s)))
            return True

    def end_drag(self, token=None):
        with self._lock:
            if not self._drag or (token is not None and token != self._drag_id):
                return False          # 이미 끝난 끌기·지난 끌기의 늦은 신호 → 위치를 다시 저장하지 않음
            self._dragging = False
            self._drag = None
            self._save()              # 맨 아래로 보내기는 1초 점검이 함 (캘린더를 쓰는 중이면 그대로 둠)
            return True

    def _reset_for_move(self):
        """되돌리기·모니터 옮기기 전에: 남은 끌기를 무효로 하고, 숨겨졌거나 최소화돼 있으면 되살림"""
        self._dragging, self._drag = False, None
        self._drag_id += 1
        if not self.visible:
            self.visible = True
            if self.window:
                self.window.show()
        self._restore_if_hidden()

    def snap(self, preset="right"):
        with self._lock:
            if not self.hwnd:
                return False
            self._reset_for_move()
            frac, _, anchor = SNAP.get(preset, SNAP["right"])
            areas = self.w32.work_areas()
            mon = self._current_monitor(areas)
            self.w32.place(self.hwnd, *initial_geometry(areas[mon], frac, anchor))
            self.api.set_prefs({"monitor": mon, "anchor": anchor, "fraction": frac})
            self._save()
            self.peek()
            return True

    def next_monitor(self):
        with self._lock:
            areas = self.w32.work_areas()
            if len(areas) < 2 or not self.hwnd:
                return False
            self._reset_for_move()
            mon = (self._current_monitor(areas) + 1) % len(areas)
            p = self.api.get_prefs()
            self.w32.place(self.hwnd, *initial_geometry(areas[mon], p.get("fraction", 0.66), p.get("anchor", "right")))
            self.api.set_prefs({"monitor": mon})
            self._save()
            self.peek()
            return True

    def _current_monitor(self, areas):
        """위쪽 막대가 있는 모니터 (keep_bar_on_screen 과 같은 기준)"""
        r = self._normal_rect()
        if r is None:
            return 0
        return bar_area_index(r, areas, bar_px(self._dpr))

    def hide(self):
        with self._lock:
            self.visible = False
            if self.window:
                self.window.hide()
            return True

    def show(self):
        """트레이 '캘린더 보이기'(아이콘 클릭): 다른 창에 가려 있어도 잠깐 맨 앞으로 꺼내 보여 줌"""
        with self._lock:
            self.visible = True
            if self.window:
                self.window.show()
            self.peek()
            return True

    def quit(self):
        """프로그램 끝내기 (업데이트 설치 직전에 api 가 부름). 트레이 아이콘도 함께 정리."""
        fn = getattr(self, "_on_quit", None)
        if fn:
            fn(getattr(self, "_tray", None))
        elif self.window:
            self.window.destroy()
        return True

    def settings_changed(self):
        """설정 창에서 저장한 뒤: 화면이 메모 기능 상태 등을 다시 읽음"""
        if self.window:
            try:
                self.window.evaluate_js("window.calendarSettingsChanged && window.calendarSettingsChanged()")
            except Exception:
                pass

    def refresh(self):
        if self.window:
            try:
                self.window.evaluate_js("window.calendarRefresh && window.calendarRefresh()")
            except Exception:
                pass


# ───────────────────────── 트레이 아이콘 ─────────────────────────
def _tray_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 10, 58, 58), 8, fill=(124, 172, 248, 255))
    d.rectangle((6, 10, 58, 22), fill=(66, 110, 200, 255))
    for i, x in enumerate((18, 32, 46)):
        d.rectangle((x - 3, 4, x + 3, 16), fill=(230, 236, 245, 255))
    for r in range(2):
        for c in range(3):
            d.rectangle((14 + c * 14, 28 + r * 13, 22 + c * 14, 36 + r * 13), fill=(255, 255, 255, 230))
    return img


def start_tray(host: Host, on_quit):
    try:
        import pystray
    except ImportError:
        log.warning("pystray 가 없어 트레이 아이콘 없이 실행합니다.")
        return None
    menu = pystray.Menu(
        pystray.MenuItem("캘린더 보이기", lambda: host.show(), default=True),
        pystray.MenuItem("캘린더 숨기기", lambda: host.hide()),
        pystray.MenuItem("오른쪽 2/3 로 되돌리기", lambda: host.snap("right")),
        pystray.MenuItem("다른 모니터로 옮기기", lambda: host.next_monitor()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("끝내기", lambda icon: on_quit(icon)),
    )
    icon = pystray.Icon("cb_calendar", _tray_image(), "충북메신저 AI 캘린더", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


# ───────────────────────── 실행 ─────────────────────────
def run(settings, with_watch: bool = False):
    if os.name != "nt":
        raise SystemExit("바탕화면 캘린더는 Windows 에서만 실행됩니다. (화면 미리보기는 calendar.html 을 브라우저로 여세요)")
    try:
        import webview
    except ImportError as e:
        raise SystemExit("pywebview 가 없습니다. 1_설치하기.bat 을 다시 실행하세요.") from e
    from ..storage import Storage

    Win32.dpi_aware()
    data_dir = Path(settings.db_path).parent
    api = CalendarAPI(Storage(settings.db_path), data_dir / "calendar_prefs.json")
    host = Host(api)
    api._host = host

    window = webview.create_window(
        TITLE, url=str(HERE / "calendar.html"), js_api=api,
        width=1200, height=800, frameless=True, easy_drag=False, resizable=True,
        # transparent=True 는 Windows(WinForms+WebView2)에서 진짜로 비치지 않고 회색 폼 바탕이 비침 → 끄고,
        # 투명도는 창 전체 투명도(Host.set_opacity)로
        transparent=False, background_color="#12161E", focus=True,
    )
    host.window = window

    def on_quit(icon=None):
        try:
            if icon:
                icon.stop()
            if getattr(host, "_plan", None):
                host._plan.stop()
            from . import settings_window
            settings_window.close_open()   # 설정 창이 열려 있으면 함께 닫음
        finally:
            window.destroy()

    host._on_quit = on_quit

    def started():
        host.setup()
        host._tray = start_tray(host, on_quit)
        if with_watch:
            api._watch_on_change = host.refresh   # 시작이 실패해도 [감시 다시 시작]으로 켤 수 있게
            try:
                from ..runner import Runner
                api._runner = Runner(settings, on_change=host.refresh).start()   # 쪽지 감시 + 메일 확인 + AI
            except Exception as e:  # noqa: BLE001
                log.error("감시를 시작하지 못했습니다: %s", e)
        try:   # 하루 2번 '오늘 할 일 순서' 정리·알림 (감시와 따로, 감시를 꺼도 동작)
            from ..plan_scheduler import PlanScheduler
            host._plan = PlanScheduler(settings.db_path, on_change=host.refresh).start()
        except Exception as e:  # noqa: BLE001
            log.error("오늘 할 일 정리를 시작하지 못했습니다: %s", e)

    webview.start(started, gui="edgechromium", debug=bool(os.getenv("CAL_DEBUG")))

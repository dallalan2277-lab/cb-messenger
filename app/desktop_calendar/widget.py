"""바탕화면 캘린더 위젯 (Windows) — 테두리 없는 창을 항상 모든 창의 맨 아래에 붙여 둠.

- 보기에는 바탕화면의 일부처럼 보이고, 클릭·글자 입력은 보통 창처럼 됨
  (진짜 배경 속(WorkerW)에 넣으면 입력이 안 되므로 Rainmeter 의 '바탕화면에 붙이기'와 같은 방식을 씀)
- 작업 표시줄·Alt+Tab 에 나타나지 않음. 트레이 아이콘으로 보이기/숨기기/끝내기
- 기본 크기: 고른 모니터 작업 영역의 2/3 (오른쪽). 끌어서 옮기거나 가장자리로 크기를 바꾸면 기억함
- Win+D(바탕화면 보기)로 숨겨져도 다시 나타남
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


def clamp_to_areas(geom, areas, min_visible=80):
    """저장된 위치가 지금 모니터 구성 밖이면(모니터 하나 뺐을 때 등) None"""
    x, y, w, h = geom
    for ax, ay, aw, ah in areas:
        if x + w - min_visible > ax and x + min_visible < ax + aw and y + min_visible > ay - 40 and y < ay + ah - min_visible:
            return geom
    return None


# ───────────────────────── 창 조작 (화면 JS 가 api.win(...) 으로 부름) ─────────────────────────
class Host:
    MIN_W, MIN_H = 480, 360

    def __init__(self, api: CalendarAPI):
        self.api = api
        self.w32 = Win32()
        self.hwnd = 0
        self.window = None
        self._drag = None
        self._dragging = False
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
        if p.get("w") and p.get("h"):
            geom = clamp_to_areas((p["x"], p["y"], p["w"], p["h"]), areas)
        if geom is None:
            mon = min(int(p.get("monitor", 0)), len(areas) - 1)
            geom = initial_geometry(areas[mon], p.get("fraction", 0.66), p.get("anchor", "right"))
        self.w32.place(self.hwnd, *geom)
        self.w32.to_bottom(self.hwnd)
        threading.Thread(target=self._keep_on_desktop, daemon=True).start()

    def _keep_on_desktop(self):
        """1초마다: 최소화되면 되살리고, 바탕화면을 보는 중이면 위로, 아니면 맨 아래로"""
        while True:
            time.sleep(1.0)
            try:
                if not self.hwnd or not self.visible or self._dragging:
                    continue
                if self.w32.is_minimized(self.hwnd) or not self.w32.is_visible(self.hwnd):
                    self.w32.restore_noactivate(self.hwnd)
                fg, cls = self.w32.foreground_class()
                if fg == self.hwnd:
                    continue                      # 입력 중에는 건드리지 않음
                if cls in DESKTOP_CLASSES:
                    self.w32.to_top_noactivate(self.hwnd)   # Win+D / 바탕화면 클릭 → 보이게
                else:
                    self.w32.to_bottom(self.hwnd)
            except Exception as e:  # noqa: BLE001
                log.debug("keep_on_desktop: %s", e)

    def _save(self):
        x, y, w, h = self.w32.rect(self.hwnd)
        self.api.set_prefs({"x": x, "y": y, "w": w, "h": h})

    # -- JS 에서 부르는 것들
    def begin_drag(self, dpr=1.0):
        self._dragging = True
        self._drag = (self.w32.rect(self.hwnd), float(dpr or 1.0))
        return True

    def drag_to(self, dx, dy):
        if not self._drag:
            return False
        (x, y, w, h), s = self._drag
        self.w32.place(self.hwnd, x + dx * s, y + dy * s, w, h)
        return True

    def resize_drag(self, direction, dx, dy):
        if not self._drag:
            return False
        (x, y, w, h), s = self._drag
        dx, dy = dx * s, dy * s
        nx, ny, nw, nh = x, y, w, h
        if "e" in direction:
            nw = max(self.MIN_W, w + dx)
        if "s" in direction:
            nh = max(self.MIN_H, h + dy)
        if "w" in direction:
            nw = max(self.MIN_W, w - dx)
            nx = x + (w - nw)
        self.w32.place(self.hwnd, nx, ny, nw, nh)
        return True

    def end_drag(self):
        self._dragging = False
        self._drag = None
        self._save()
        self.w32.to_bottom(self.hwnd)
        return True

    def snap(self, preset="right"):
        frac, _, anchor = SNAP.get(preset, SNAP["right"])
        areas = self.w32.work_areas()
        mon = self._current_monitor(areas)
        self.w32.place(self.hwnd, *initial_geometry(areas[mon], frac, anchor))
        self.api.set_prefs({"monitor": mon, "anchor": anchor, "fraction": frac})
        self._save()
        return True

    def next_monitor(self):
        areas = self.w32.work_areas()
        if len(areas) < 2:
            return False
        mon = (self._current_monitor(areas) + 1) % len(areas)
        p = self.api.get_prefs()
        self.w32.place(self.hwnd, *initial_geometry(areas[mon], p.get("fraction", 0.66), p.get("anchor", "right")))
        self.api.set_prefs({"monitor": mon})
        self._save()
        return True

    def _current_monitor(self, areas):
        x, y, w, h = self.w32.rect(self.hwnd)
        cx, cy = x + w // 2, y + h // 2
        for i, (ax, ay, aw, ah) in enumerate(areas):
            if ax <= cx < ax + aw and ay <= cy < ay + ah:
                return i
        return 0

    def hide(self):
        self.visible = False
        if self.window:
            self.window.hide()
        return True

    def show(self):
        self.visible = True
        if self.window:
            self.window.show()
            self.w32.to_bottom(self.hwnd)
        return True

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
        transparent=True, background_color="#12161E", focus=True,
    )
    host.window = window

    def on_quit(icon=None):
        try:
            if icon:
                icon.stop()
        finally:
            window.destroy()

    def started():
        host.setup()
        start_tray(host, on_quit)
        if with_watch:
            try:
                from ..runner import Runner
                api._runner = Runner(settings, on_change=host.refresh).start()   # 쪽지 감시 + 메일 확인 + AI
            except Exception as e:  # noqa: BLE001
                log.error("감시를 시작하지 못했습니다: %s", e)

    webview.start(started, gui="edgechromium", debug=bool(os.getenv("CAL_DEBUG")))

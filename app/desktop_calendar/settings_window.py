"""설정 창 (pywebview + settings.html) — 캘린더와 같은 모양의 설정 화면.

- 캘린더가 켜져 있으면 같은 프로그램 안에서 창을 하나 더 띄움 (저장하면 감시에 바로 적용).
- 따로 켤 때는 `python main.py settings` (run_standalone).
pywebview 는 Windows 전용이라 함수 안에서 import 합니다.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from .settings_api import SettingsAPI

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
TITLE = "충북메신저 AI 설정"
SIZE = (900, 760)
MIN_SIZE = (640, 480)
BACKGROUND = {"dark": "#161A22", "light": "#FAFAFC"}   # settings.html 의 body 배경과 같게 (뜨는 순간 번쩍임 방지)

_lock = threading.Lock()
_current = {"window": None}


def _to_front(window) -> bool:
    """이미 열려 있는 설정 창을 앞으로. 창이 이미 닫혔으면 False"""
    try:
        window.restore()
        window.show()
        window.on_top = True     # 잠깐 맨 위로 올렸다가 풀면 다른 창 앞으로 나옴
        window.on_top = False
        return True
    except Exception:  # noqa: BLE001
        return False


def _match_titlebar(dark: bool) -> None:
    """창 제목 줄도 테마에 맞춤 (Windows 10 20H1 이상, 안 되면 조용히 넘어감)"""
    try:
        import ctypes
        from ctypes import wintypes
        user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            value = ctypes.c_int(1 if dark else 0)
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                         ctypes.byref(value), ctypes.sizeof(value))
    except Exception:  # noqa: BLE001
        pass


def open_window(prefs_path, on_saved=None, usage_fn=None, in_calendar: bool = False):
    """설정 창을 띄움 (이미 열려 있으면 그 창을 앞으로). webview.start() 전·후 모두 부를 수 있음."""
    import webview
    with _lock:
        old = _current["window"]
        if old is not None and _to_front(old):
            return old
        api = SettingsAPI(prefs_path, on_saved=on_saved, usage_fn=usage_fn, in_calendar=in_calendar)
        theme = api._theme()
        window = webview.create_window(
            TITLE, url=str(HERE / "settings.html"), js_api=api,
            width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE, resizable=True,
            background_color=BACKGROUND[theme], text_select=True,
        )
        api._window = window

        def closed():
            with _lock:
                if _current["window"] is window:
                    _current["window"] = None

        window.events.closed += closed
        window.events.shown += lambda: _match_titlebar(theme == "dark")
        _current["window"] = window
        return window


def close_open() -> None:
    """캘린더를 끝낼 때 설정 창도 함께 닫음 (안 닫으면 프로그램이 안 끝남)"""
    with _lock:
        window, _current["window"] = _current["window"], None
    if window is not None:
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass


def run_standalone(settings) -> None:
    """설정 창만 따로 켜기. 캘린더가 켜져 있으면 저장 뒤 캘린더 ⚙ 메뉴의 '감시 다시 시작'이 필요."""
    import os
    if os.name != "nt":
        raise SystemExit("설정 창은 Windows 에서만 실행됩니다. (화면 미리보기는 settings.html 을 브라우저로 여세요)")
    try:
        import webview
    except ImportError as e:
        raise SystemExit("pywebview 가 없습니다. 1_설치하기.bat 을 다시 실행하세요.") from e
    data_dir = Path(settings.db_path).parent
    holder = {}

    def usage(since: str) -> list[dict]:
        if "storage" not in holder:
            from ..storage import Storage
            holder["storage"] = Storage(settings.db_path)
        return holder["storage"].usage_summary(since)

    open_window(data_dir / "calendar_prefs.json", usage_fn=usage, in_calendar=False)
    webview.start(gui="edgechromium", debug=bool(os.getenv("CAL_DEBUG")))

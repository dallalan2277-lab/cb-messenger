# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 설정 — packaging\build.bat 이 사용합니다.
#   CBMessengerAI.exe : 창 없이 실행 (더블클릭 = 바탕화면 캘린더 + 쪽지·메일 감시)
#   cbm.exe           : 명령 프롬프트용 (chatgpt-login, messenger-check, mail-check ...)
# 두 exe 가 같은 _internal 폴더를 함께 씁니다 (onedir).
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(SPECPATH, "app.ico")

datas = [
    (os.path.join(ROOT, "app", "desktop_calendar", "calendar.html"), os.path.join("app", "desktop_calendar")),
    (os.path.join(ROOT, "app", "desktop_calendar", "holidays_kr.json"), os.path.join("app", "desktop_calendar")),
    (os.path.join(ROOT, "samples", "sample_messages.json"), "samples"),
    (os.path.join(ROOT, ".env.example"), "."),
]
binaries = []
hiddenimports = []

# DLL·자료 파일을 함께 가져가야 하는 패키지
for pkg in ("webview", "uiautomation", "clr_loader", "pythonnet", "pystray", "win11toast", "keyring"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# win11toast 가 쓰는 winrt 네임스페이스 패키지들
hiddenimports += collect_submodules("winrt")
# 함수 안에서 import 되는 모듈 (분석에서 빠지지 않게)
hiddenimports += collect_submodules("app") + ["gui", "keyring.backends.Windows", "comtypes.stream"]
hiddenimports += collect_submodules("truststore")   # updater 가 함수 안에서 import

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "matplotlib", "numpy", "pandas", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui_exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="CBMessengerAI",
    console=False,
    icon=ICON,
    upx=False,
)
cli_exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="cbm",
    console=True,
    icon=ICON,
    upx=False,
)
coll = COLLECT(
    gui_exe, cli_exe,
    a.binaries, a.datas,
    upx=False,
    name="CBMessengerAI",
)

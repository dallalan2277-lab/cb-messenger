"""Locations of bundled resources and user-writable application files."""

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Union


class _Paths(NamedTuple):
    root: Path
    resource_dir: Path
    user_dir: Path
    env_path: Path
    data_dir: Path


def _compute(
    frozen: bool,
    executable: Union[str, Path],
    meipass: Optional[Union[str, Path]],
    environ: Mapping[str, str],
    file: Union[str, Path],
) -> _Paths:
    """Compute paths without depending on process globals."""
    root = Path(executable).resolve().parent if frozen else Path(file).resolve().parent.parent
    resource_dir = Path(meipass) if frozen else root
    if environ.get("CBM_HOME"):
        user_dir = Path(environ["CBM_HOME"])
    elif frozen:
        user_dir = Path(environ["LOCALAPPDATA"]) / "cb-messenger-ai"
    else:
        user_dir = root
    return _Paths(root, resource_dir, user_dir, user_dir / ".env", user_dir / "data")


FROZEN = getattr(sys, "frozen", False)
ROOT, RESOURCE_DIR, USER_DIR, ENV_PATH, DATA_DIR = _compute(
    FROZEN, sys.executable, getattr(sys, "_MEIPASS", None), os.environ, __file__
)


def ensure_user_dir() -> None:
    """Create user directories and initialize settings without replacing them."""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    example = RESOURCE_DIR / ".env.example"
    if example.is_file():
        try:
            with ENV_PATH.open("xb") as destination, example.open("rb") as source:
                shutil.copyfileobj(source, destination)
        except FileExistsError:
            pass


GUI_EXE_NAME = "CBMessengerAI.exe"   # 창 없는 exe (packaging/cbm.spec). 같은 폴더의 cbm.exe 는 콘솔용


def is_gui_exe() -> bool:
    """설치판의 창 없는 exe(CBMessengerAI.exe)로 실행됐는지. cbm.exe(콘솔)이면 False."""
    return bool(FROZEN) and Path(sys.executable).name.lower() == GUI_EXE_NAME.lower()


def self_command(*args: str) -> list[str]:
    """Return a command that starts this application without a console."""
    executable = Path(sys.executable)
    if FROZEN:
        gui = executable.with_name(GUI_EXE_NAME)   # cbm.exe 에서 불려도 창 없는 exe 로 띄움
        return [str(gui if gui.exists() else executable), *args]
    pythonw = executable.with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else executable), str(ROOT / "main.py"), *args]

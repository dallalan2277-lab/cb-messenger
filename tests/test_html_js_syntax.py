"""화면(html) 속 자바스크립트 문법 검사 — node 가 있는 PC 에서만 (없으면 건너뜀).
0.10.1 작업 중 if/else 사이에 줄을 끼워 넣어 화면 전체가 멈춘 적이 있어 추가."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node 가 없는 PC")
@pytest.mark.parametrize("name", ["calendar.html", "settings.html"])
def test_inline_script_has_valid_syntax(tmp_path, name):
    html = (ROOT / "app" / "desktop_calendar" / name).read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
    assert scripts
    js = tmp_path / (name + ".js")
    js.write_text("\n;\n".join(scripts), encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:500]

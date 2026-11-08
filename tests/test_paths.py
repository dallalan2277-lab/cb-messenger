from pathlib import Path

from app import paths


def test_compute_development_paths(tmp_path):
    root = tmp_path / "project with spaces"
    result = paths._compute(False, tmp_path / "python", None, {}, root / "app" / "paths.py")
    assert result.root == root
    assert result.resource_dir == root
    assert result.user_dir == root
    assert result.env_path == root / ".env"
    assert result.data_dir == root / "data"


def test_compute_frozen_paths_with_korean_and_spaces(tmp_path):
    install = tmp_path / "Program Files" / "충북 메신저"
    local_appdata = tmp_path / "Users" / "홍 길동" / "AppData" / "Local"
    result = paths._compute(
        True, install / "app.exe", install / "_internal",
        {"LOCALAPPDATA": str(local_appdata)}, tmp_path / "unused.py",
    )
    assert result.root == install
    assert result.resource_dir == install / "_internal"
    assert result.user_dir == local_appdata / "cb-messenger-ai"
    assert result.env_path == result.user_dir / ".env"
    assert result.data_dir == result.user_dir / "data"


def test_compute_cbm_home_overrides_both_modes(tmp_path):
    home = tmp_path / "홍 길동" / "custom home"
    for frozen in (False, True):
        result = paths._compute(
            frozen, tmp_path / "install" / "app.exe", tmp_path / "resources",
            {"CBM_HOME": str(home), "LOCALAPPDATA": str(tmp_path / "local")},
            tmp_path / "project" / "app" / "paths.py",
        )
        assert result.user_dir == home
        assert result.env_path == home / ".env"
        assert result.data_dir == home / "data"


def test_ensure_user_dir_copies_once_without_overwriting(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    resources.mkdir()
    example = resources / ".env.example"
    example.write_text("SETTING=first\n", encoding="utf-8")
    user = tmp_path / "홍 길동" / "user data"
    monkeypatch.setattr(paths, "RESOURCE_DIR", resources)
    monkeypatch.setattr(paths, "USER_DIR", user)
    monkeypatch.setattr(paths, "ENV_PATH", user / ".env")
    monkeypatch.setattr(paths, "DATA_DIR", user / "data")

    paths.ensure_user_dir()
    assert (user / "data").is_dir()
    assert (user / ".env").read_text(encoding="utf-8") == "SETTING=first\n"
    (user / ".env").write_text("SETTING=user\n", encoding="utf-8")
    example.write_text("SETTING=second\n", encoding="utf-8")
    paths.ensure_user_dir()
    assert (user / ".env").read_text(encoding="utf-8") == "SETTING=user\n"


def test_ensure_user_dir_without_example(tmp_path, monkeypatch):
    user = tmp_path / "user"
    monkeypatch.setattr(paths, "RESOURCE_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "USER_DIR", user)
    monkeypatch.setattr(paths, "ENV_PATH", user / ".env")
    monkeypatch.setattr(paths, "DATA_DIR", user / "data")
    paths.ensure_user_dir()
    assert (user / "data").is_dir()
    assert not (user / ".env").exists()


def test_self_command_frozen(tmp_path, monkeypatch):
    executable = tmp_path / "Program Files" / "앱.exe"
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths.sys, "executable", str(executable))
    assert paths.self_command("calendar", "--watch") == [str(executable), "calendar", "--watch"]


def test_self_command_frozen_console_prefers_gui_exe(tmp_path, monkeypatch):
    cli = tmp_path / "설치 폴더" / "cbm.exe"
    cli.parent.mkdir()
    gui = cli.with_name(paths.GUI_EXE_NAME)
    gui.touch()
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths.sys, "executable", str(cli))
    assert paths.self_command("gui") == [str(gui), "gui"]
    assert paths.is_gui_exe() is False
    monkeypatch.setattr(paths.sys, "executable", str(gui))
    assert paths.is_gui_exe() is True


def test_self_command_development_prefers_pythonw(tmp_path, monkeypatch):
    executable = tmp_path / "Python" / "python.exe"
    executable.parent.mkdir()
    pythonw = executable.with_name("pythonw.exe")
    monkeypatch.setattr(paths, "FROZEN", False)
    monkeypatch.setattr(paths, "ROOT", tmp_path / "project with spaces")
    monkeypatch.setattr(paths.sys, "executable", str(executable))
    expected_args = [str(paths.ROOT / "main.py"), "gui"]
    assert paths.self_command("gui") == [str(executable), *expected_args]
    pythonw.touch()
    assert paths.self_command("gui") == [str(pythonw), *expected_args]

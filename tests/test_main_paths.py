from types import SimpleNamespace

import pytest

import main


def test_frozen_no_arguments_starts_calendar_watch(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "FROZEN", True)
    monkeypatch.setattr(main, "is_gui_exe", lambda: True)
    monkeypatch.setattr(main, "cmd_calendar", calls.append)
    monkeypatch.setattr(main.sys, "argv", ["CBMessengerAI.exe"])

    main.main()

    assert len(calls) == 1
    assert calls[0].watch is True


def test_frozen_console_exe_without_arguments_shows_usage(monkeypatch):
    monkeypatch.setattr(main, "FROZEN", True)
    monkeypatch.setattr(main, "is_gui_exe", lambda: False)
    monkeypatch.setattr(main.sys, "argv", ["cbm.exe"])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 2


def test_dev_no_arguments_still_requires_command(monkeypatch):
    monkeypatch.setattr(main, "FROZEN", False)
    with pytest.raises(SystemExit) as exc:
        main.main([])
    assert exc.value.code == 2


@pytest.mark.parametrize("no_watch", [False, True])
def test_startup_uses_self_command_and_escapes_vbs(tmp_path, monkeypatch, no_watch):
    startup = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(main, "ROOT", tmp_path / '설치 "폴더"')
    calls = []

    def command(*args):
        calls.append(args)
        return [str(tmp_path / "홍 길동" / "app.exe"), *args]

    monkeypatch.setattr(main, "self_command", command)
    main.cmd_startup(SimpleNamespace(off=False, no_watch=no_watch))

    args = ("calendar",) if no_watch else ("calendar", "--watch")
    assert calls == [args]
    script = (startup / "충북메신저AI_캘린더.vbs").read_text(encoding="utf-16")
    assert f'sh.CurrentDirectory = "{str(main.ROOT).replace(chr(34), chr(34) * 2)}"' in script
    command_line = " ".join(f'"{arg}"' for arg in command(*args)).replace('"', '""')
    assert f'sh.Run "{command_line}", 0, False' in script

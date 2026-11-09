"""바탕화면 캘린더의 업데이트 API — 네트워크·Windows 설치 없이."""
from pathlib import Path
from types import SimpleNamespace

import app
import app.config as config
import app.paths as paths
import app.desktop_calendar.api as calendar_api_module
from app.desktop_calendar.api import CalendarAPI
from app.storage import Storage


def _api(tmp_path):
    return CalendarAPI(Storage(str(tmp_path / "t.db")), tmp_path / "prefs.json")


class FakeUpdateError(Exception):
    pass


def _settings(monkeypatch, enabled=True):
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda reload=False: SimpleNamespace(update_repo="owner/repo", update_check=enabled),
    )


def _updater(check_latest, download=None, launch_installer=None):
    return SimpleNamespace(
        UpdateError=FakeUpdateError,
        check_latest=check_latest,
        download=download or (lambda info: Path("installer.exe")),
        launch_installer=launch_installer or (lambda path, watch=True: None),
    )


def test_update_check_reports_available_update(tmp_path, monkeypatch):
    _settings(monkeypatch)
    calls = []
    info = SimpleNamespace(version="0.7.1", notes="새 기능", html_url="https://example.test/release")
    api = _api(tmp_path)
    api._updater = lambda: _updater(lambda repo, current: calls.append((repo, current)) or info)

    result = api.update_check()

    assert result == {
        "current": app.__version__, "available": True, "latest": "0.7.1", "notes": "새 기능",
        "html_url": "https://example.test/release", "error": "", "disabled": False,
    }
    assert calls == [("owner/repo", app.__version__)]
    assert api._update_info is info


def test_update_check_reports_already_latest(tmp_path, monkeypatch):
    _settings(monkeypatch)
    api = _api(tmp_path)
    api._updater = lambda: _updater(lambda *args: None)

    result = api.update_check()

    assert result["current"] == app.__version__
    assert result["available"] is False and result["error"] == "" and result["disabled"] is False
    assert api._update_info is None


def test_update_check_reports_user_facing_error(tmp_path, monkeypatch):
    _settings(monkeypatch)

    def fail(*args):
        raise FakeUpdateError("업데이트 서버에 연결하지 못했습니다.")

    api = _api(tmp_path)
    api._updater = lambda: _updater(fail)

    result = api.update_check()

    assert result["available"] is False
    assert result["error"] == "업데이트 서버에 연결하지 못했습니다."
    assert api._update_info is None


def test_update_check_disabled_skips_check_but_force_bypasses_it(tmp_path, monkeypatch):
    _settings(monkeypatch, enabled=False)
    calls = []
    api = _api(tmp_path)
    api._updater = lambda: _updater(lambda *args: calls.append(1) or None)

    disabled = api.update_check()
    forced = api.update_check(force=True)

    assert disabled["disabled"] is True and disabled["available"] is False
    assert forced["disabled"] is False and calls == [1]


def test_update_check_caches_for_six_hours_unless_forced(tmp_path, monkeypatch):
    _settings(monkeypatch)
    calls = []
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")
    api = _api(tmp_path)
    api._updater = lambda: _updater(lambda *args: calls.append(1) or info)

    api.update_check()
    api.update_check()
    api.update_check(force=True)

    assert calls == [1, 1]


def test_update_install_refuses_development_mode(tmp_path, monkeypatch):
    _settings(monkeypatch)
    calls = []
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")
    api = _api(tmp_path)
    api._updater = lambda: _updater(
        lambda *args: info,
        download=lambda *args, **kwargs: calls.append("download"),
        launch_installer=lambda *args, **kwargs: calls.append("launch"),
    )
    api.update_check()
    monkeypatch.setattr(paths, "FROZEN", False)

    result = api.update_install()

    assert result == {"ok": False, "error": "개발 모드(파이썬으로 실행)에서는 자동 설치하지 않습니다. 설치판에서 사용하세요."}
    assert calls == []


def test_update_install_downloads_launches_and_quits_host(tmp_path, monkeypatch):
    _settings(monkeypatch)
    calls = []
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")
    installer = tmp_path / "CBMessengerAI-Setup.exe"
    api = _api(tmp_path)
    api._updater = lambda: _updater(
        lambda *args: info,
        download=lambda *args, **kwargs: calls.append(("download", args[0])) or installer,
        launch_installer=lambda *args, **kwargs: calls.append(("launch", args[0])),
    )
    api.update_check()
    monkeypatch.setattr(paths, "FROZEN", True)

    class Host:
        quit_called = False

        def quit(self):
            self.quit_called = True

    class ImmediateTimer:
        instances = []

        def __init__(self, seconds, callback):
            self.seconds = seconds
            self.callback = callback
            self.daemon = False
            self.instances.append(self)

        def start(self):
            self.callback()

    host = Host()
    api._host = host
    monkeypatch.setattr(calendar_api_module.threading, "Timer", ImmediateTimer)

    result = api.update_install()

    assert result == {"ok": True, "version": "0.7.1"}
    assert calls == [("download", info), ("launch", installer)]
    assert host.quit_called is True
    assert ImmediateTimer.instances[0].seconds == 1.5
    assert ImmediateTimer.instances[0].daemon is True


def test_update_install_checks_when_no_info_and_finds_no_update(tmp_path, monkeypatch):
    _settings(monkeypatch)
    calls = []
    api = _api(tmp_path)
    api._updater = lambda: _updater(lambda *args: calls.append(args) or None)

    result = api.update_install()

    assert result == {"ok": False, "error": "새 버전이 없습니다."}
    assert calls == [("owner/repo", app.__version__)]


def test_update_install_reports_download_error_and_allows_retry(tmp_path, monkeypatch):
    _settings(monkeypatch)
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")
    attempts = []
    installer = tmp_path / "CBMessengerAI-Setup.exe"
    launched = []

    def flaky_download(*args, **kwargs):
        attempts.append(args[0])
        if len(attempts) == 1:
            raise FakeUpdateError("설치 파일을 받지 못했습니다.")
        return installer

    class NoopTimer:
        def __init__(self, seconds, callback):
            self.daemon = False

        def start(self):
            pass

    api = _api(tmp_path)
    api._updater = lambda: _updater(
        lambda *args: info,
        download=flaky_download,
        launch_installer=lambda *args, **kwargs: launched.append(args[0]),
    )
    api.update_check()
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(calendar_api_module.threading, "Timer", NoopTimer)

    failed = api.update_install()
    retried = api.update_install()

    assert failed == {"ok": False, "error": "설치 파일을 받지 못했습니다."}
    assert retried == {"ok": True, "version": "0.7.1"}
    assert attempts == [info, info] and launched == [installer]


def test_update_install_reports_launcher_error(tmp_path, monkeypatch):
    _settings(monkeypatch)
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")

    def broken_launcher(*args, **kwargs):
        raise FakeUpdateError("설치 프로그램을 시작하지 못했습니다.")

    api = _api(tmp_path)
    api._updater = lambda: _updater(
        lambda *args: info,
        download=lambda *args, **kwargs: tmp_path / "CBMessengerAI-Setup.exe",
        launch_installer=broken_launcher,
    )
    api.update_check()
    monkeypatch.setattr(paths, "FROZEN", True)

    result = api.update_install()

    assert result == {"ok": False, "error": "설치 프로그램을 시작하지 못했습니다."}
    assert api._update_installing is False


def test_update_install_rejects_double_click(tmp_path, monkeypatch):
    _settings(monkeypatch)
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")
    installer = tmp_path / "CBMessengerAI-Setup.exe"
    api = _api(tmp_path)
    second_calls = []

    def download(*args, **kwargs):
        second_calls.append(api.update_install())
        return installer

    class NoopTimer:
        def __init__(self, seconds, callback):
            self.daemon = False

        def start(self):
            pass

    api._updater = lambda: _updater(lambda *args: info, download=download)
    api.update_check()
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(calendar_api_module.threading, "Timer", NoopTimer)

    result = api.update_install()

    assert result == {"ok": True, "version": "0.7.1"}
    assert second_calls == [{"ok": False, "error": "이미 업데이트 중입니다."}]


def _install_mode(tmp_path, monkeypatch, watching):
    _settings(monkeypatch)
    seen = []
    info = SimpleNamespace(version="0.7.1", notes="", html_url="")
    api = _api(tmp_path)
    api._updater = lambda: _updater(
        lambda *args: info,
        download=lambda *args, **kwargs: tmp_path / "CBMessengerAI_Setup_0.7.1.exe",
        launch_installer=lambda path, watch=True: seen.append(watch),
    )
    if watching:
        api._watch_on_change = lambda: None
    api.update_check()
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(calendar_api_module.threading, "Timer",
                        lambda s, cb: SimpleNamespace(daemon=False, start=lambda: None))
    assert api.update_install()["ok"] is True
    return seen


def test_update_install_keeps_watch_mode(tmp_path, monkeypatch):
    assert _install_mode(tmp_path, monkeypatch, watching=True) == [True]


def test_update_install_keeps_calendar_only_mode(tmp_path, monkeypatch):
    assert _install_mode(tmp_path, monkeypatch, watching=False) == [False]

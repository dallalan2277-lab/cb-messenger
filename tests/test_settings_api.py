"""새 설정 화면의 뒷단(SettingsAPI)과 Claude 상태 확인 — 네트워크·로그인·Windows 없이."""
import json
import subprocess
import time
from types import SimpleNamespace

import pytest

import app.config as config
import app.collectors.naver_mail as naver_mail
import app.llm.chatgpt_auth as auth
import app.llm.chatgpt_plan_provider as plan
import app.llm.claude_cli_provider as cc
import app.secrets_store as secrets_store
from app.config import Settings
from app.desktop_calendar.settings_api import SettingsAPI
from app.llm.base import LLMError


@pytest.fixture
def env(monkeypatch):
    """save_env 가 실제 .env 를 건드리지 않게 가로챔. 저장된 값은 dict 로 모임."""
    saved = {}
    monkeypatch.setattr(config, "save_env", lambda updates, path=None: saved.update(updates))
    monkeypatch.setattr(config, "get_settings", lambda reload=False: Settings())
    monkeypatch.setattr(secrets_store, "load_password", lambda account: None)
    return saved


def _api(tmp_path, **kw):
    return SettingsAPI(tmp_path / "prefs.json", **kw)


def _data(**over):
    d = {
        "models": {"analyze": "chatgpt_plan:gpt-5.6-sol", "digest": "", "fallback": "claude_cli:opus", "reasoning": "low"},
        "skip": {"keywords": " 식단 , ,급식", "min_chars": "5"},
        "archive": {"local_on": True, "local_dir": "C:\\기록", "gdrive_on": False, "gdrive_dir": "",
                    "gdrive_redact": True, "format": "md", "summary_on": False},
        "mail": {"on": False, "account": "", "interval": "5"},
        "notify": {"on": True, "scope": "school", "high_only": False, "eztalk": True, "mail": False, "sound": False,
                   "preview": False, "quiet": True, "quiet_start": "22:00", "quiet_end": "08:00"},
        "school": {"name": " 가상초등학교 ", "duties": "3학년 2반 담임\n정보부", "senders": "교무부", "role": "교사"},
    }
    for k, v in over.items():
        d[k] = {**d[k], **v}
    return d


# ---------- 읽기 ----------
def test_load_has_every_section_and_no_secrets(tmp_path, env):
    (tmp_path / "prefs.json").write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    r = _api(tmp_path, in_calendar=True).load()
    assert r["theme"] == "light" and r["in_calendar"] is True
    assert {"models", "skip", "archive", "mail", "notify", "school", "providers", "model_hints"} <= set(r)
    assert [p["id"] for p in r["providers"]][:2] == ["chatgpt_plan", "claude_cli"]
    assert r["mail"]["password_saved"] is False
    assert "password" not in json.dumps(r).replace("password_saved", "")   # 비밀번호 값은 어디에도 없음
    json.dumps(r)                                                          # 화면으로 보낼 수 있는 값만


def test_theme_defaults_to_dark_when_prefs_missing_or_broken(tmp_path, env):
    assert _api(tmp_path).load()["theme"] == "dark"
    (tmp_path / "prefs.json").write_text("{깨진", encoding="utf-8")
    assert _api(tmp_path).load()["theme"] == "dark"


# ---------- 저장 ----------
def test_save_writes_same_keys_as_old_dialog(tmp_path, env):
    calls = []
    r = _api(tmp_path, on_saved=lambda: calls.append(1) or {"ok": True}).save(_data())
    assert r == {"ok": True, "error": "", "restarted": True} and calls == [1]
    assert env["AI_ANALYZE"] == "chatgpt_plan:gpt-5.6-sol"
    assert env["AI_DIGEST"] == "chatgpt_plan:gpt-5.6-sol"        # 비우면 분석 모델과 같게
    assert env["AI_FALLBACK"] == "claude_cli:opus" and env["AI_REASONING"] == "low"
    assert env["SKIP_AI_KEYWORDS"] == "식단,급식" and env["SKIP_AI_MIN_CHARS"] == "5"
    assert env["ARCHIVE_LOCAL"] == "true" and env["ARCHIVE_FORMAT"] == "md" and env["ARCHIVE_SUMMARY"] == "false"
    assert env["MAIL_ENABLED"] == "false" and env["MAIL_INTERVAL_MIN"] == "5"
    assert env["NOTIFY_SOURCES"] == "eztalk" and env["NOTIFY_QUIET_START"] == "22:00"
    assert env["SCHOOL_NAME"] == "가상초등학교" and env["SCHOOL_DUTIES"] == "3학년 2반 담임, 정보부"


def test_save_without_calendar_reports_not_restarted(tmp_path, env):
    assert _api(tmp_path).save(_data())["restarted"] is False


def test_save_rejects_missing_or_bad_models(tmp_path, env):
    api = _api(tmp_path)
    assert "분석 모델" in api.save(_data(models={"analyze": ""}))["error"]
    assert "제공자" in api.save(_data(models={"analyze": "nope:gpt"}))["error"]
    assert "모델 이름" in api.save(_data(models={"fallback": "claude_cli:opus\nMAIL_ENABLED=true"}))["error"]
    assert env == {}                                             # 잘못된 값이면 아무것도 저장하지 않음


def test_save_values_cannot_inject_extra_env_lines(tmp_path, env):
    r = _api(tmp_path).save(_data(school={"name": "가상초\nAI_ANALYZE=fake", "role": "교사\r\nX=1"},
                                  archive={"local_dir": "C:\\a\nMAIL_ENABLED=true"},
                                  skip={"keywords": "a\nNOTIFY_SOUND=true"}))
    assert r["ok"] is True
    assert all("\n" not in v and "\r" not in v for v in env.values())


def test_save_validates_quiet_hours_only_when_enabled(tmp_path, env):
    api = _api(tmp_path)
    assert "HH:MM" in api.save(_data(notify={"quiet": True, "quiet_start": "25:00"}))["error"]
    assert env == {}
    r = api.save(_data(notify={"quiet": False, "quiet_start": "", "quiet_end": ""}))
    assert r["ok"] is True and env["NOTIFY_QUIET"] == "false"
    assert "NOTIFY_QUIET_START" not in env                       # 끈 채 비워 둔 시각으로 예전 값을 지우지 않음
    assert "알림 범위" in api.save(_data(notify={"scope": "everything"}))["error"]


def test_save_checks_mail_account_when_given(tmp_path, env):
    api = _api(tmp_path)
    assert api.save(_data(mail={"on": True, "account": "someone@example.com"}))["ok"] is False
    assert api.save(_data(mail={"on": True, "account": " Teacher@Naver.com "}))["ok"] is True
    assert env["MAIL_ACCOUNT"] == "teacher@naver.com" and env["MAIL_ENABLED"] == "true"


def test_save_survives_garbage_input(tmp_path, env):
    api = _api(tmp_path)
    assert api.save(None)["ok"] is False
    assert api.save({"models": {"analyze": "fake"}, "skip": {"min_chars": "많이"}})["ok"] is False   # 알림 범위 없음
    r = api.save({"models": {"analyze": "fake"}, "skip": {"min_chars": "많이"}, "notify": {"scope": "all"}})
    assert r["ok"] is True and env["SKIP_AI_MIN_CHARS"] == "0" and env["MAIL_INTERVAL_MIN"] == "2"


# ---------- 메일 ----------
def test_mail_connect_saves_password_only_to_keyring(tmp_path, env, monkeypatch):
    stored, verified = {}, []
    monkeypatch.setattr(naver_mail, "verify_login", lambda acc, pw: verified.append(acc))
    monkeypatch.setattr(secrets_store, "save_password", lambda acc, pw: stored.update({acc: pw}))
    api = _api(tmp_path)
    assert "비밀번호" in api.mail_connect("teacher@naver.com", "  ")["error"]
    r = api.mail_connect(" Teacher@naver.com", " app-pass-1234 ")
    assert r["ok"] is True and r["account"] == "teacher@naver.com" and verified == ["teacher@naver.com"]
    assert stored == {"teacher@naver.com": "app-pass-1234"}
    assert env == {"MAIL_ENABLED": "true", "MAIL_ACCOUNT": "teacher@naver.com"}
    assert "app-pass-1234" not in json.dumps(r) + json.dumps(env)      # 응답·설정 파일에는 비밀번호 없음


def test_mail_connect_reports_login_failure_without_saving(tmp_path, env, monkeypatch):
    def fail(acc, pw):
        raise naver_mail.MailError("아이디 또는 앱 비밀번호가 맞지 않습니다.")

    stored = {}
    monkeypatch.setattr(naver_mail, "verify_login", fail)
    monkeypatch.setattr(secrets_store, "save_password", lambda acc, pw: stored.update({acc: pw}))
    r = _api(tmp_path).mail_connect("teacher@naver.com", "wrong")
    assert r["ok"] is False and "맞지 않습니다" in r["error"] and stored == {} and env == {}


def test_mail_disconnect_deletes_password(tmp_path, env, monkeypatch):
    deleted = []
    monkeypatch.setattr(secrets_store, "delete_password", lambda acc: deleted.append(acc))
    assert _api(tmp_path).mail_disconnect(" Teacher@naver.com ")["ok"] is True
    assert deleted == ["teacher@naver.com"] and env == {"MAIL_ENABLED": "false"}


# ---------- ChatGPT ----------
class _Sess:
    logged_in = True
    logins = []

    def is_logged_in(self):
        return self.logged_in

    def load(self):
        return {"email": "teacher@example.com", "refresh_token": "비밀"}

    def login(self):
        self.logins.append(1)
        return {"email": "teacher@example.com"}

    def logout(self):
        type(self).logged_in = False


def _wait(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.01)
    return False


def test_chatgpt_status_shows_email_but_never_tokens(tmp_path, env, monkeypatch):
    monkeypatch.setattr(auth, "ChatGPTSession", type("S", (_Sess,), {}))
    r = _api(tmp_path).chatgpt_status()
    assert r == {"logged_in": True, "email": "teacher@example.com", "busy": False, "error": ""}


def test_chatgpt_login_runs_in_background_and_restarts_watch(tmp_path, env, monkeypatch):
    sess = type("S", (_Sess,), {"logins": []})
    monkeypatch.setattr(auth, "ChatGPTSession", sess)
    saved = []
    api = _api(tmp_path, on_saved=lambda: saved.append(1) or {"ok": True})
    assert api.chatgpt_login()["ok"] is True
    assert _wait(lambda: not api.chatgpt_status()["busy"] and saved)
    assert sess.logins == [1] and api.chatgpt_status()["error"] == ""


def test_chatgpt_login_failure_is_reported_and_double_click_blocked(tmp_path, env, monkeypatch):
    import threading
    gate = threading.Event()

    class Slow(_Sess):
        def login(self):
            gate.wait(3)
            raise RuntimeError("로그인 시간이 초과되었습니다.")

    monkeypatch.setattr(auth, "ChatGPTSession", Slow)
    api = _api(tmp_path)
    assert api.chatgpt_login()["ok"] is True
    assert "이미 로그인 중" in api.chatgpt_login()["error"]
    gate.set()
    assert _wait(lambda: not api.chatgpt_status()["busy"])
    assert "시간이 초과" in api.chatgpt_status()["error"]


def test_chatgpt_models_cached_and_needs_login(tmp_path, env, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "ChatGPTSession", type("S", (_Sess,), {}))
    monkeypatch.setattr(plan, "list_models", lambda s: calls.append(1) or [{"slug": "gpt-a", "display_name": "A", "x": 1}])
    api = _api(tmp_path)
    assert api.chatgpt_models()["options"] == [{"slug": "gpt-a", "display_name": "A"}]
    api.chatgpt_models()
    assert len(calls) == 1
    api.chatgpt_models(True)
    assert len(calls) == 2
    monkeypatch.setattr(auth, "ChatGPTSession", type("S", (_Sess,), {"logged_in": False}))
    assert "로그인" in _api(tmp_path).chatgpt_models()["error"]


# ---------- Claude (공식 Claude Code 명령만) ----------
def _run_result(stdout, returncode=0):
    return lambda cmd, **kw: SimpleNamespace(stdout=stdout, stderr="", returncode=returncode, cmd=cmd, kw=kw)


def test_claude_status_not_installed(monkeypatch):
    monkeypatch.setattr(cc, "find_claude", lambda: None)
    assert cc.claude_status() == {"installed": False, "logged_in": False, "email": "", "plan": "", "error": ""}


def test_claude_status_reads_auth_status_json(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return SimpleNamespace(stdout=json.dumps({"loggedIn": True, "email": "teacher@example.com",
                                                  "subscriptionType": "pro"}), returncode=0)

    monkeypatch.setattr(cc, "find_claude", lambda: "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-removed")
    r = cc.claude_status(run=run)
    assert r == {"installed": True, "logged_in": True, "email": "teacher@example.com", "plan": "pro", "error": ""}
    assert seen["cmd"] == ["claude", "auth", "status"] and "ANTHROPIC_API_KEY" not in seen["env"]


def test_claude_status_logged_out_and_unreadable_and_timeout(monkeypatch):
    monkeypatch.setattr(cc, "find_claude", lambda: "claude")
    r = cc.claude_status(run=_run_result(json.dumps({"loggedIn": False, "email": "old@example.com"}), returncode=1))
    assert r["installed"] and not r["logged_in"] and r["email"] == "" and r["error"] == ""
    r = cc.claude_status(run=_run_result("unknown command 'auth'", returncode=1))
    assert r["installed"] and not r["logged_in"] and "확인하지 못했습니다" in r["error"]

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    assert "응답하지" in cc.claude_status(run=slow)["error"]


def test_open_claude_login_uses_official_command(monkeypatch):
    calls = []
    monkeypatch.setattr(cc, "_windows", lambda: True)
    monkeypatch.setattr(cc, "find_claude", lambda: "C:\\claude.exe")
    cc.open_claude_login(popen=lambda cmd, **kw: calls.append(cmd))
    assert calls == [["C:\\claude.exe", "auth", "login"]]
    monkeypatch.setattr(cc, "find_claude", lambda: None)
    with pytest.raises(LLMError, match="설치"):
        cc.open_claude_login(popen=lambda cmd, **kw: calls.append(cmd))


def test_open_claude_install_runs_official_installer_in_visible_window(monkeypatch):
    calls = []
    monkeypatch.setattr(cc, "_windows", lambda: True)
    cc.open_claude_install(popen=lambda cmd, **kw: calls.append(cmd))
    assert calls[0][0] == "powershell" and "https://claude.ai/install.ps1" in calls[0][-1]
    assert "-WindowStyle" not in calls[0]                        # 숨기지 않음 — 사용자가 진행을 봄


def test_settings_api_claude_status_cached_until_refresh(tmp_path, env, monkeypatch):
    calls = []
    monkeypatch.setattr(cc, "claude_status", lambda: calls.append(1) or {"installed": True, "logged_in": False,
                                                                         "email": "", "plan": "", "error": ""})
    api = _api(tmp_path)
    api.claude_status()
    api.claude_status()
    assert len(calls) == 1
    api.claude_status(True)
    assert len(calls) == 2


def test_settings_api_claude_login_reports_errors(tmp_path, env, monkeypatch):
    def fail():
        raise LLMError("Claude Code 가 설치돼 있지 않습니다. 먼저 설치하세요.")

    monkeypatch.setattr(cc, "open_claude_login", fail)
    assert "설치" in _api(tmp_path).claude_login()["error"]
    monkeypatch.setattr(cc, "open_claude_login", lambda: None)
    assert _api(tmp_path).claude_login() == {"ok": True, "error": ""}


# ---------- 그 밖 ----------
def test_usage_rows_are_labelled(tmp_path, env):
    rows = [{"provider": "chatgpt_plan", "model": "gpt-5.6-sol", "purpose": "analyze", "calls": 3, "inp": 1200, "outp": None},
            {"provider": "claude_cli", "model": "opus", "purpose": "digest", "calls": 1, "inp": 10, "outp": 5}]
    r = _api(tmp_path, usage_fn=lambda since: rows).usage()
    assert r["since"].endswith("-01")
    assert r["rows"][0] == {"purpose": "분석", "provider": "ChatGPT 구독", "model": "gpt-5.6-sol", "calls": 3, "inp": 1200, "outp": 0}
    assert r["rows"][1]["purpose"] == "브리핑" and r["rows"][1]["provider"] == "Claude 구독"
    assert _api(tmp_path).usage()["rows"] == []


def test_pick_folder_and_close_without_window(tmp_path, env):
    api = _api(tmp_path)
    assert api.pick_folder("C:\\") == {"path": ""} and api.close() == {"ok": True}


def test_only_public_methods_are_exposed(tmp_path, env):
    """pywebview 는 _ 로 시작하지 않는 것을 화면에 노출함 → 화면 계약에 있는 것만 공개돼 있어야 함"""
    public = {n for n in dir(_api(tmp_path)) if not n.startswith("_")}
    assert public == {"load", "save", "chatgpt_status", "chatgpt_login", "chatgpt_logout", "chatgpt_models",
                      "claude_status", "claude_login", "claude_install", "usage", "mail_connect", "mail_disconnect",
                      "notify_test", "pick_folder", "open_test_screen", "close"}


def test_calendar_open_settings_falls_back_to_separate_window(tmp_path, monkeypatch):
    import subprocess as sp
    from app.desktop_calendar.api import CalendarAPI
    from app.storage import Storage
    started = []
    monkeypatch.setattr(sp, "Popen", lambda cmd, **kw: started.append(cmd))
    api = CalendarAPI(Storage(str(tmp_path / "t.db")), tmp_path / "prefs.json")
    assert api.open_settings() == {"ok": True, "inline": False}      # 창(host) 없이 = 따로 띄움
    assert started and started[0][-1] == "settings"
    assert api._usage_summary("2000-01-01") == []

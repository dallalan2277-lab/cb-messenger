"""캘린더 메뉴의 AI 모델 고르기 (models / set_model) — 네트워크·로그인 없이."""
from types import SimpleNamespace

import app.config as config
import app.llm.chatgpt_auth as auth
import app.llm.chatgpt_plan_provider as plan
from app.desktop_calendar.api import CalendarAPI
from app.storage import Storage


def _api(tmp_path):
    return CalendarAPI(Storage(str(tmp_path / "t.db")), tmp_path / "prefs.json")


class _Sess:
    logged_in = True

    def is_logged_in(self):
        return self.logged_in


def test_models_lists_and_caches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "get_settings", lambda reload=False: SimpleNamespace(analyze_model="chatgpt_plan:gpt-b"))
    monkeypatch.setattr(auth, "ChatGPTSession", _Sess)
    monkeypatch.setattr(plan, "list_models", lambda s: calls.append(1) or [{"slug": "gpt-a", "display_name": "A"},
                                                                          {"slug": "gpt-b", "display_name": "B"}])
    api = _api(tmp_path)
    r = api.models()
    assert r["provider"] == "chatgpt_plan" and r["model"] == "gpt-b" and not r["error"]
    assert [m["slug"] for m in r["options"]] == ["gpt-a", "gpt-b"]
    api.models()
    assert len(calls) == 1          # 두 번째는 저장해 둔 목록 사용
    api.models(refresh=True)
    assert len(calls) == 2


def test_models_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda reload=False: SimpleNamespace(analyze_model="chatgpt_plan"))
    monkeypatch.setattr(auth, "ChatGPTSession", type("S", (_Sess,), {"logged_in": False}))
    r = _api(tmp_path).models()
    assert r["options"] == [] and "로그인" in r["error"] and r["model"] == ""


def test_set_model_saves_env(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(config, "save_env", lambda u: saved.append(u))
    api = _api(tmp_path)
    assert api.set_model("gpt-6.1-terra")["spec"] == "chatgpt_plan:gpt-6.1-terra"
    assert api.set_model("")["spec"] == "chatgpt_plan"          # 자동
    assert saved == [{"AI_ANALYZE": "chatgpt_plan:gpt-6.1-terra"}, {"AI_ANALYZE": "chatgpt_plan"}]
    assert api.set_model("bad name; rm")["ok"] is False and len(saved) == 2

"""메모 저장·정리·이미지 입력 테스트 — 네트워크와 실제 Windows OCR 없이."""
from __future__ import annotations

import base64
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.desktop_calendar.api as calendar_api
import app.llm.claude_cli_provider as claude_cli
import app.ocr as ocr
from app.config import Settings
from app.desktop_calendar.api import CalendarAPI
from app.llm.base import LLMError, LLMProvider
from app.llm.chatgpt_plan_provider import ChatGPTPlanProvider
from app.memo import MEMO_KINDS, organize
from app.storage import Storage


def _answer(**changes):
    answer = {
        "title": "가상 연수 안내",
        "kind": "안내문",
        "summary": "가상 연수 일정을 확인합니다.",
        "details": ["날짜: 2026-11-20", "장소: 가상 연수실"],
        "items": [],
        "action_items": ["참석 여부 확인"],
        "events": [{
            "title": "가상 연수",
            "date": "2026-11-20",
            "start_time": "15:00",
            "end_time": "16:00",
            "location": "가상 연수실",
            "note": "가상 일정",
        }],
    }
    answer.update(changes)
    return answer


class StubProvider(LLMProvider):
    name = "stub"

    def __init__(self, answer=None, *, images=True, fail=False):
        super().__init__("stub-model")
        self.answer = answer if answer is not None else _answer()
        self.supports_images = images
        self.fail = fail
        self.text_calls = []
        self.image_calls = []

    def complete(self, system, user, max_tokens=1500):
        if self.fail:
            raise LLMError("가상 AI 실패")
        return json.dumps(self.answer, ensure_ascii=False)

    def complete_json(self, system, user, max_tokens=1500, schema=None):
        self.text_calls.append((system, user, schema))
        if self.fail:
            raise LLMError("가상 AI 실패")
        self.last_usage = {"input_tokens": 12, "output_tokens": 4}
        return dict(self.answer)

    def complete_json_image(self, system, user, image_bytes, mime, schema=None):
        self.image_calls.append((system, user, image_bytes, mime, schema))
        if not self.supports_images:
            return super().complete_json_image(system, user, image_bytes, mime, schema)
        if self.fail:
            raise LLMError("가상 AI 실패")
        self.last_usage = {"input_tokens": 20, "output_tokens": 5}
        return dict(self.answer)


def _png_url() -> str:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _settings() -> Settings:
    settings = Settings()
    settings.analyze_model = "stub"
    settings.fallback_model = ""
    settings.memo_enabled = True
    settings.memo_read_mode = "ocr"
    settings.redact_enabled = True
    settings.redact_names = ["가상학생"]
    settings.user_role = "교사"
    return settings


def _api(tmp_path, monkeypatch, provider=None, settings=None):
    settings = settings or _settings()
    provider = provider or StubProvider()
    data_dir = tmp_path / "user-data"
    monkeypatch.setattr(calendar_api, "DATA_DIR", data_dir)
    monkeypatch.setattr(calendar_api, "get_settings", lambda reload=False: settings)
    monkeypatch.setattr(calendar_api, "create_provider", lambda current, spec: provider)
    api = CalendarAPI(Storage(str(tmp_path / "memo.db")), tmp_path / "prefs.json")
    return api, provider, data_dir


# ---------- 메모 정리 ----------
def test_organize_text_redacts_for_ai_and_filters_invented_numbers():
    provider = StubProvider(_answer(
        kind="틀린 종류",
        details=["날짜: 2026-11-20", "인원: 999명", "대상: 가상학생"],
        events=[_answer()["events"][0], {"title": "잘못된 날", "date": "2026-13-40"}],
    ))
    called = []

    def hide(text):
        called.append(text)
        return text.replace("가상학생", "[이름1]")

    result = organize(
        provider,
        text="가상학생은 2026-11-20에 참석합니다.",
        now=datetime(2026, 11, 10, 9, 30),
        redact_fn=hide,
    )
    sent = json.loads(provider.text_calls[0][1])
    assert called == ["가상학생은 2026-11-20에 참석합니다."]
    assert "가상학생" not in sent["text"] and "[이름1]" in sent["text"]
    assert result.kind == "기타"
    assert result.details == ["날짜: 2026-11-20", "대상: 가상학생"]
    assert len(result.events) == 1 and result.events[0].date == "2026-11-20"


def test_organize_forces_kind_and_uses_image_call():
    provider = StubProvider(_answer(kind="안내문"))
    raw = base64.b64decode(_png_url().split(",", 1)[1])
    result = organize(provider, text="함께 적은 글", image=raw, mime="image/png", kind="회의 기록")
    assert result.kind == "회의 기록" and provider.image_calls
    _system, user, sent_image, sent_mime, schema = provider.image_calls[0]
    assert json.loads(user)["text"] == "함께 적은 글"
    assert sent_image and sent_mime in ("image/png", "image/jpeg") and schema
    assert set(MEMO_KINDS) == {"목록", "안내문", "할 일", "회의 기록", "기타"}


# ---------- 저장소 ----------
def test_storage_memo_crud_and_delete_keeps_event(tmp_path):
    storage = Storage(str(tmp_path / "memos.db"))
    memo_id = storage.add_memo(title="처음", details=["가: 나"], items=["하나"], action_items=[])
    assert storage.update_memo(memo_id, title="바꿈", kind="목록", kind_by_user=True)
    row = storage.get_memo(memo_id)
    assert row["title"] == "바꿈" and row["details"] == ["가: 나"] and row["kind_by_user"] is True
    event_id = storage.add_calendar_event(title="연결 일정", date="2026-11-21", origin="memo", memo_id=memo_id)
    assert storage.list_memos()[0]["event_count"] == 1
    deleted = storage.delete_memo(memo_id)
    assert deleted["id"] == memo_id and storage.get_memo(memo_id) is None
    event = next(e for e in storage.calendar_events("2026-11-21", "2026-11-21") if e["id"] == event_id)
    assert event["memo_id"] is None


def test_old_calendar_table_gains_memo_id_column(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE calendar_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, date TEXT NOT NULL,
        start_time TEXT DEFAULT '', end_time TEXT DEFAULT '', location TEXT DEFAULT '', note TEXT DEFAULT '',
        status TEXT DEFAULT 'confirmed', origin TEXT DEFAULT 'manual', message_id INTEGER,
        created_at TEXT, updated_at TEXT)""")
    conn.commit()
    conn.close()
    storage = Storage(str(db))
    columns = {row["name"] for row in storage.conn.execute("PRAGMA table_info(calendar_events)")}
    assert {"memo_id", "import_batch"} <= columns


# ---------- CalendarAPI ----------
def test_api_text_create_saves_memo_suggestion_and_usage(tmp_path, monkeypatch):
    api, provider, _data_dir = _api(tmp_path, monkeypatch)
    result = api.memo_create({"mode": "text", "text": "가상학생 2026-11-20 연수", "kind": ""})
    assert result["ok"] is True and result["memo"]["status"] == "ok"
    memo = result["memo"]
    assert memo["kind"] == "안내문" and memo["events"][0]["status"] == "suggested"
    assert memo["events"][0]["origin"] == "memo" and memo["events"][0]["memo_id"] == memo["id"]
    sent = json.loads(provider.text_calls[0][1])
    assert "가상학생" not in sent["text"]
    usage = api._storage.usage_summary("2000-01-01")
    assert usage[0]["purpose"] == "memo" and usage[0]["inp"] == 12


def test_api_image_create_saves_original_and_returns_preview(tmp_path, monkeypatch):
    api, provider, data_dir = _api(tmp_path, monkeypatch)
    result = api.memo_create({"mode": "image", "text": "덧붙인 말", "image_data_url": _png_url(), "kind": ""})
    assert result["ok"] is True and provider.image_calls
    row = api._storage.get_memo(result["memo"]["id"])
    saved = data_dir / row["image_path"]
    assert saved.is_file() and saved.parent.parent.name == "메모"
    assert result["memo"]["image_data_url"].startswith("data:image/png;base64,")


def test_api_ai_failure_keeps_failed_memo(tmp_path, monkeypatch):
    api, _provider, _data_dir = _api(tmp_path, monkeypatch, StubProvider(fail=True))
    result = api.memo_create({"mode": "text", "text": "가상 원문", "kind": ""})
    assert result["ok"] is True and "warning" in result
    assert result["memo"]["status"] == "failed" and "AI 정리 실패" in result["memo"]["summary"]
    assert api.memo_list()[0]["status"] == "failed"


def test_api_update_and_reanalyze_force_user_kind(tmp_path, monkeypatch):
    api, provider, _data_dir = _api(tmp_path, monkeypatch)
    created = api.memo_create({"mode": "text", "text": "가상 메모 2026-11-20", "kind": ""})["memo"]
    updated = api.memo_update(created["id"], {"title": "사람이 고친 제목", "kind": "목록"})
    assert updated["ok"] and updated["memo"]["kind_by_user"] is True
    provider.answer = _answer(kind="안내문", title="다시 정리")
    redone = api.memo_reanalyze(created["id"], "회의 기록")
    assert redone["ok"] and redone["memo"]["kind"] == "회의 기록"
    assert json.loads(provider.text_calls[-1][1])["kind_hint"] == "회의 기록"


def test_api_to_event_note_and_delete_image_but_keep_events(tmp_path, monkeypatch):
    api, _provider, data_dir = _api(tmp_path, monkeypatch)
    memo = api.memo_create({"mode": "image", "text": "", "image_data_url": _png_url(), "kind": ""})["memo"]
    row = api._storage.get_memo(memo["id"])
    image_path = data_dir / row["image_path"]
    made = api.memo_to_event(memo["id"], {"date": "2026-12-01", "title": "직접 넣은 일정"})
    assert made["ok"] and made["id"]
    event = next(e for e in api._storage.calendar_events("2026-12-01", "2026-12-01") if e["id"] == made["id"])
    assert memo["summary"] in event["note"] and event["memo_id"] == memo["id"]
    assert api.memo_delete(memo["id"]) == {"ok": True} and not image_path.exists()
    kept = next(e for e in api._storage.calendar_events("2026-12-01", "2026-12-01") if e["id"] == made["id"])
    assert kept["memo_id"] is None


def test_api_rejects_bad_and_oversize_data_urls(tmp_path, monkeypatch):
    api, _provider, _data_dir = _api(tmp_path, monkeypatch)
    bad = api.memo_create({"mode": "image", "image_data_url": "data:text/plain;base64,QQ=="})
    assert bad["ok"] is False and "이미지 형식" in bad["error"]
    monkeypatch.setattr(calendar_api, "IMAGE_LIMIT", 4)
    huge = "data:image/png;base64," + base64.b64encode(b"12345").decode("ascii")
    result = api.memo_create({"mode": "image", "image_data_url": huge})
    assert result["ok"] is False and "10MB" in result["error"]


def test_api_disabled_rejects_create_and_ocr(tmp_path, monkeypatch):
    settings = _settings()
    settings.memo_enabled = False
    api, _provider, _data_dir = _api(tmp_path, monkeypatch, settings=settings)
    assert api.memo_create({"mode": "text", "text": "글"})["error"] == calendar_api.MEMO_DISABLED_ERROR
    assert api.memo_ocr(_png_url())["error"] == calendar_api.MEMO_DISABLED_ERROR
    assert api.memo_status()["enabled"] is False


# ---------- OCR ----------
def test_ocr_non_windows_reports_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "os", SimpleNamespace(name="posix"))
    assert ocr.available() == (False, "Windows 에서만 글자 인식이 됩니다.")
    image = tmp_path / "x.png"
    image.write_bytes(b"not-used")
    with pytest.raises(ocr.OcrError, match="Windows"):
        ocr.recognize_file(image)
    with pytest.raises(ocr.OcrError, match="Windows"):
        ocr.recognize_bytes(b"not-an-image")


# ---------- 이미지 제공자 ----------
class _Session:
    def is_logged_in(self):
        return True

    def access_token(self, force_refresh=False):
        return "token"


def test_chatgpt_image_body_and_schema_400_retry():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(400, text="format unavailable")
        stream = (
            'data: {"type":"response.output_text.delta","delta":"{\\"ok\\":true}"}\n\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":1}}}\n\n'
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ChatGPTPlanProvider("gpt-test", session=_Session(), http=http)
    out = provider.complete_json_image("system", "user", b"image", "image/png", schema={"type": "object"})
    assert out == {"ok": True} and len(bodies) == 2
    content = bodies[1]["input"][0]["content"]
    assert content[0]["type"] == "input_image" and content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "input_text", "text": "user"}
    assert "text" in bodies[0] and "text" not in bodies[1]


def test_claude_image_stream_json_shape_and_result(tmp_path, monkeypatch):
    work = tmp_path / "empty"
    work.mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        result = {"type": "result", "is_error": False, "result": '{"ok": true}',
                  "usage": {"input_tokens": 7, "output_tokens": 2}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(result) + "\n", stderr="")

    monkeypatch.setattr(claude_cli, "find_claude", lambda: "claude")
    monkeypatch.setattr(claude_cli.tempfile, "mkdtemp", lambda prefix: str(work))
    monkeypatch.setattr(claude_cli.subprocess, "run", run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-pass")
    provider = claude_cli.ClaudeCLIProvider("sonnet")
    assert provider.complete_json_image("system", "user", b"image", "image/png") == {"ok": True}
    cmd, kwargs = calls[0]
    payload = json.loads(kwargs["input"])
    assert "--input-format" in cmd and "stream-json" in cmd and "--verbose" in cmd
    assert payload["message"]["content"][0]["type"] == "image"
    assert payload["message"]["content"][1] == {"type": "text", "text": "user"}
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert provider.last_usage == {"input_tokens": 7, "output_tokens": 2}


# ---------- GPT 교차 검토(2026-11-11) 지적 ----------
def test_memo_status_never_builds_provider_or_touches_network(tmp_path, monkeypatch):
    """캘린더 첫 화면을 막지 않게: memo_status 는 AI 를 만들지 않고 설정 이름만 봄"""
    settings = _settings()
    api, _p, _d = _api(tmp_path, monkeypatch, settings=settings)

    def boom(*a, **k):
        raise AssertionError("memo_status 가 AI 를 만들면 안 됨")

    monkeypatch.setattr(calendar_api, "create_provider", boom)
    settings.analyze_model, settings.fallback_model = "chatgpt_plan", ""
    assert api.memo_status()["image_ai"] is True
    settings.analyze_model, settings.fallback_model = "openai:gpt-5", "claude_cli:opus"
    assert api.memo_status()["image_ai"] is True           # 대체 모델이 이미지 가능
    settings.analyze_model, settings.fallback_model = "fake", ""
    st = api.memo_status()
    assert st["image_ai"] is False and st["image_ai_reason"]


def test_to_event_confirms_existing_suggestion_instead_of_false_success(tmp_path, monkeypatch):
    api, _p, _d = _api(tmp_path, monkeypatch)
    memo = api.memo_create({"mode": "text", "text": "가상 연수 2026-11-20 15:00", "kind": ""})["memo"]
    sugg = [e for e in api._storage.calendar_events("2026-11-20", "2026-11-20") if e["status"] == "suggested"]
    assert len(sugg) == 1
    made = api.memo_to_event(memo["id"], {"date": "2026-11-20", "start_time": "15:00", "title": "가상 연수"})
    assert made["ok"] and made["existed"] is True and made["id"] == sugg[0]["id"]
    evs = api._storage.calendar_events("2026-11-20", "2026-11-20")
    assert len(evs) == 1 and evs[0]["status"] == "confirmed"
    api.dismiss(evs[0]["id"])                               # 무시했던 일정과 겹쳐도 다시 확정되어 보임
    again = api.memo_to_event(memo["id"], {"date": "2026-11-20", "start_time": "15:00", "title": "가상 연수"})
    assert again["ok"] and api._storage.calendar_events("2026-11-20", "2026-11-20")[0]["status"] == "confirmed"


def test_reanalyze_uses_memo_creation_time_for_relative_dates(tmp_path, monkeypatch):
    api, provider, _d = _api(tmp_path, monkeypatch)
    memo = api.memo_create({"mode": "text", "text": "내일 가상 회의", "kind": ""})["memo"]
    api._storage.conn.execute("UPDATE memos SET created_at='2026-11-10 09:00:00' WHERE id=?", (memo["id"],))
    api._storage.conn.commit()
    assert api.memo_reanalyze(memo["id"])["ok"]
    sent = json.loads(provider.text_calls[-1][1])
    assert sent["current_time"].startswith("2026-11-10 09:00")

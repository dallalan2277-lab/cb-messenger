"""모델 슬롯·대체 모델·AI 생략·사용량·설정 저장 테스트"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors import message_from_text
from app.config import Settings, save_env
from app.llm.base import LLMProvider, LLMError
from app.llm.factory import FallbackProvider, create_provider, parse_spec
from app.pipeline import build_pipeline


class Boom(LLMProvider):
    name = "boom"
    def complete(self, system, user, max_tokens=1500):
        raise LLMError("subscription_sharing_usage_limit_exceeded")


class Ok(LLMProvider):
    name = "ok"
    def complete(self, system, user, max_tokens=1500):
        self.last_usage = {"input_tokens": 10, "output_tokens": 3}
        return '{"importance": "중요", "summary": "s"}'


def _settings(tmp_path, **kw):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.analyze_model = s.digest_model = "fake"
    s.fallback_model, s.reasoning = "", "default"
    s.skip_ai_keywords, s.skip_ai_min_chars = [], 0
    s.include_senders, s.exclude_senders, s.redact_names = [], [], []
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_parse_spec():
    assert parse_spec("chatgpt_plan:gpt-5.6-luna") == ("chatgpt_plan", "gpt-5.6-luna")
    assert parse_spec("claude_cli") == ("claude_cli", "")
    assert parse_spec(" OpenAI : gpt-5-mini ") == ("openai", "gpt-5-mini")


def test_fallback_switches_and_records_used_model():
    fp = FallbackProvider(Boom("m1"), Ok("m2"))
    assert fp.complete_json("s", "u")["importance"] == "중요"
    assert (fp.name, fp.model, fp.last_usage["input_tokens"]) == ("ok", "m2", 10)


def test_create_provider_slot_model_and_reasoning(tmp_path):
    s = _settings(tmp_path, reasoning="low")
    p = create_provider(s, "fake:my-model")
    assert p.model == "my-model" and p.reasoning == "low"
    s.fallback_model = "fake:other"
    assert isinstance(create_provider(s, "fake:my-model"), FallbackProvider)


def test_primary_unavailable_uses_fallback(tmp_path):
    s = _settings(tmp_path, fallback_model="fake:backup")
    p = create_provider(s, "nonexistent_provider")   # 주 모델 준비 실패
    assert p.name == "fake" and p.model == "backup"


def test_skip_rule_and_usage_and_separate_digest(tmp_path):
    s = _settings(tmp_path, skip_ai_keywords=["식단"], skip_ai_min_chars=5, digest_model="fake:big")
    pipe = build_pipeline(s, notify_levels=())
    a1 = pipe.handle(message_from_text("11월 식단표 게시했습니다", sender="행정실"))
    a2 = pipe.handle(message_from_text("넵", sender="동료"))
    a3 = pipe.handle(message_from_text("내일까지 회의 자료 제출 바랍니다", sender="교무부장"))
    assert (a1.provider, a1.importance) == ("rule", "참고")
    assert a2.provider == "rule"
    assert a3.provider == "fake" and a3.importance == "긴급"
    pipe.analyzer.digest(pipe.storage.recent())
    assert pipe.analyzer.digest_provider.model == "big"      # 브리핑은 다른 모델
    rows = {(r["purpose"], r["model"]): r for r in pipe.storage.usage_summary("2000-01-01")}
    assert rows[("analyze", "rule-based")]["calls"] == 1     # AI 생략 2건은 사용량 0
    assert rows[("digest", "big")]["calls"] == 1


def test_save_env_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# 주석\nAI_PROVIDER=claude\nREDACT_NAMES=김민수\nAI_ANALYZE=old\n", encoding="utf-8")
    save_env({"AI_ANALYZE": "chatgpt_plan:x", "AI_FALLBACK": "claude_cli"}, env)
    txt = env.read_text(encoding="utf-8")
    assert "# 주석" in txt and "REDACT_NAMES=김민수" in txt
    assert "AI_ANALYZE=chatgpt_plan:x" in txt and "AI_ANALYZE=old" not in txt
    assert "AI_FALLBACK=claude_cli" in txt


# ---------- 대체 모델을 처음에 준비하지 못했을 때 (예: Claude Code 미설치) ----------
def test_missing_fallback_is_reported_and_retried(tmp_path, monkeypatch):
    import app.llm.factory as factory
    real_build = factory._build
    ready = {"ok": False}

    def build(settings, name, model):
        if name == "claude_cli" and not ready["ok"]:
            raise LLMError("Claude Code 가 설치돼 있지 않습니다")
        if name == "claude_cli":
            return Ok("opus")
        if name == "boom":
            return Boom("m1")
        return real_build(settings, name, model)

    monkeypatch.setattr(factory, "_build", build)
    s = _settings(tmp_path, fallback_model="claude_cli:opus")
    p = create_provider(s, "boom")
    assert isinstance(p, FallbackProvider) and "설치" in p.fallback_error

    # 주 모델 실패 + 대체 모델 없음 → 두 이유를 모두 알려 줌
    try:
        p.complete_json("s", "u")
        raise AssertionError("실패해야 함")
    except LLMError as e:
        assert "usage_limit" in str(e) and "대체 모델도 쓸 수 없음" in str(e)

    # 나중에 설치되면 다시 켜지 않아도 대체 모델로 넘어감
    ready["ok"] = True
    assert p.complete_json("s", "u")["importance"] == "중요"
    assert p.fallback_error == "" and (p.name, p.model) == ("ok", "opus")


def test_runner_shows_fallback_warning(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.runner import Runner
    prov = FallbackProvider(Ok("m"), None, build_fallback=lambda: (_ for _ in ()).throw(LLMError("미설치")),
                            fallback_error="미설치")
    pipe = SimpleNamespace(analyzer=SimpleNamespace(provider=prov), storage=None, archiver=None)
    s = _settings(tmp_path, fallback_model="claude_cli:opus")
    r = Runner(s, pipeline=pipe)
    assert "claude_cli:opus" in r.status["ai_fallback_error"] and "미설치" in r.status["ai_fallback_error"]
    prov.fallback, prov.fallback_error = Ok("opus"), ""
    r._refresh_fallback_status()
    assert r.status["ai_fallback_error"] == ""


def test_find_claude_checks_official_install_folder(tmp_path, monkeypatch):
    import app.llm.claude_cli_provider as cc
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    assert cc.find_claude() is None
    exe = tmp_path / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert cc.find_claude() == str(exe)

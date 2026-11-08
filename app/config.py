"""설정 불러오기 (.env 파일 + 환경변수) 와 저장."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=True)
    except ImportError:  # python-dotenv 없이도 동작
        pass


_load_env()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _bool(key: str, default: bool) -> bool:
    v = _env(key, "")
    return default if v == "" else v.strip().lower() in ("1", "true", "yes", "y", "on", "켜기")


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _f(key: str, default: str = ""):
    return field(default_factory=lambda: _env(key, default))


REASONING_LEVELS = ("default", "low", "medium", "high")


@dataclass
class Settings:
    # ---- 용도별 모델 슬롯: "제공자" 또는 "제공자:모델" ----
    #   제공자: chatgpt_plan | claude_cli | openai | claude | fake
    #   예) chatgpt_plan:gpt-5.6-luna  /  claude_cli:haiku  /  openai:gpt-5-mini
    provider: str = _f("AI_PROVIDER", "chatgpt_plan")          # 이전 버전 호환용 기본값
    analyze_model: str = field(default_factory=lambda: _env("AI_ANALYZE") or _env("AI_PROVIDER", "chatgpt_plan"))
    digest_model: str = field(default_factory=lambda: _env("AI_DIGEST") or _env("AI_ANALYZE") or _env("AI_PROVIDER", "chatgpt_plan"))
    fallback_model: str = _f("AI_FALLBACK", "")                # 비우면 대체 없음
    reasoning: str = _f("AI_REASONING", "default")             # default|low|medium|high (GPT 계열에만 적용)

    # ---- 토큰 절약: AI 를 부르지 않고 '참고'로 처리 ----
    skip_ai_keywords: list[str] = field(default_factory=lambda: _list(_env("SKIP_AI_KEYWORDS", "")))
    skip_ai_min_chars: int = field(default_factory=lambda: int(_env("SKIP_AI_MIN_CHARS", "0") or 0))

    # ---- 제공자별 기본 모델 (슬롯에 모델을 안 적었을 때) ----
    chatgpt_plan_model: str = _f("CHATGPT_PLAN_MODEL", "")
    claude_cli_model: str = _f("CLAUDE_CLI_MODEL", "")
    openai_api_key: str = _f("OPENAI_API_KEY", "")
    openai_model: str = _f("OPENAI_MODEL", "gpt-5-mini")
    anthropic_api_key: str = _f("ANTHROPIC_API_KEY", "")
    claude_model: str = _f("CLAUDE_MODEL", "claude-haiku-5-5")

    # ---- 저장 위치 ----
    db_path: str = field(default_factory=lambda: _env(
        "DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "messages.db")))

    # ---- 개인정보 가리기 ----
    redact_enabled: bool = field(default_factory=lambda: _env("REDACT_ENABLED", "true").lower() == "true")
    redact_names: list[str] = field(default_factory=lambda: _list(_env("REDACT_NAMES", "")))

    # ---- 요약 대상 필터 (비어 있으면 전체) ----
    include_senders: list[str] = field(default_factory=lambda: _list(_env("INCLUDE_SENDERS", "")))
    exclude_senders: list[str] = field(default_factory=lambda: _list(_env("EXCLUDE_SENDERS", "")))

    # ---- 원문·요약 보관 (이 PC / 구글 드라이브 따로) ----
    archive_local_enabled: bool = field(default_factory=lambda: _bool("ARCHIVE_LOCAL", True))
    archive_local_dir: str = field(default_factory=lambda: _env("ARCHIVE_LOCAL_DIR") or str(
        Path(__file__).resolve().parent.parent / "data" / "쪽지기록"))
    archive_gdrive_enabled: bool = field(default_factory=lambda: _bool("ARCHIVE_GDRIVE", False))
    archive_gdrive_dir: str = _f("ARCHIVE_GDRIVE_DIR", "")            # 비우면 자동으로 찾음
    archive_format: str = _f("ARCHIVE_FORMAT", "txt")                 # txt | md
    archive_gdrive_redact: bool = field(default_factory=lambda: _bool("ARCHIVE_GDRIVE_REDACT", True))
    archive_include_summary: bool = field(default_factory=lambda: _bool("ARCHIVE_SUMMARY", True))
    # 어떤 수집 경로의 메시지를 보관할지 (테스트 입력 manual 은 기본 제외)
    archive_sources: list[str] = field(default_factory=lambda: _list(_env("ARCHIVE_SOURCES", "eztalk,notification")))

    # ---- 바탕화면 캘린더 ----
    calendar_auto_suggest: bool = field(default_factory=lambda: _bool("CALENDAR_AUTO_SUGGEST", True))  # AI 일정 → 캘린더 '제안'
    calendar_auto_confirm: bool = field(default_factory=lambda: _bool("CALENDAR_AUTO_CONFIRM", False))  # 제안 없이 바로 확정

    # ---- 학교 정보 (AI 의 학교 업무 판단에 참고, 모두 선택) ----
    school_name: str = _f("SCHOOL_NAME", "")
    school_duties: str = _f("SCHOOL_DUTIES", "")          # 담당 업무 (예: 3학년 2반 담임, 정보부 정보업무)
    school_senders: str = _f("SCHOOL_SENDERS", "")        # 참고 발신처 (예: 충북교육청, 교무부, @cbe.go.kr)

    # ---- 알림 (쪽지·메일 공통) ----
    notify_enabled: bool = field(default_factory=lambda: _bool("NOTIFY_ENABLED", True))
    notify_scope: str = _f("NOTIFY_SCOPE", "school")          # school | important | all
    notify_high_only: bool = field(default_factory=lambda: _bool("NOTIFY_HIGH_ONLY", False))
    notify_sound: bool = field(default_factory=lambda: _bool("NOTIFY_SOUND", False))
    notify_quiet: bool = field(default_factory=lambda: _bool("NOTIFY_QUIET", True))
    notify_quiet_start: str = _f("NOTIFY_QUIET_START", "22:00")
    notify_quiet_end: str = _f("NOTIFY_QUIET_END", "08:00")
    notify_preview: bool = field(default_factory=lambda: _bool("NOTIFY_PREVIEW", False))
    notify_sources: list[str] = field(default_factory=lambda: _list(_env("NOTIFY_SOURCES", "eztalk,mail")))

    # ---- 네이버 메일 ----
    mail_enabled: bool = field(default_factory=lambda: _bool("MAIL_ENABLED", False))
    mail_account: str = _f("MAIL_ACCOUNT", "")                # ...@naver.com (비밀번호는 Windows 자격 증명 관리자에)
    mail_interval_min: float = field(default_factory=lambda: float(_env("MAIL_INTERVAL_MIN", "2") or 2))

    # ---- 메신저 수집기 ----
    messenger_collector: str = _f("MESSENGER_COLLECTOR", "eztalk")    # eztalk | notification
    messenger_process: str = _f("MESSENGER_PROCESS", "eztalk.exe")
    messenger_quiet_sec: float = field(default_factory=lambda: float(_env("MESSENGER_QUIET_SEC", "5")))
    messenger_app_keywords: list[str] = field(
        default_factory=lambda: _list(_env("MESSENGER_APP_KEYWORDS", "소통메신저,충북")))
    poll_interval_sec: float = field(default_factory=lambda: float(_env("POLL_INTERVAL_SEC", "0.5")))

    # ---- 사용자 정보 (중요도 판단에 참고) ----
    user_role: str = _f("USER_ROLE", "초등학교 교사")


def get_settings(reload: bool = False) -> Settings:
    if reload:
        _load_env()
    return Settings()


def save_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    """.env 의 해당 키만 바꾸고 나머지 줄(주석 포함)은 그대로 둡니다."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = set()
    out = []
    for line in lines:
        s = line.strip()
        key = s.split("=", 1)[0].strip() if ("=" in s and not s.startswith("#")) else ""
        if key in updates:
            if key not in done:          # 같은 키가 여러 줄이면 첫 줄만 남김
                out.append(f"{key}={updates[key]}")
                done.add(key)
            continue
        out.append(line)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = v

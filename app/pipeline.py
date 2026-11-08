"""수집 → 필터 → 저장 → 개인정보 가리기 → AI 분석 → 알림 흐름."""
from __future__ import annotations

import logging
import re

from .analyzer import Analyzer
from .config import Settings
from .llm.factory import create_provider, parse_spec
from .models import Analysis, Message
from .notifier import notify
from .redact import redact
from .storage import Storage

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, storage: Storage, analyzer: Analyzer,
                 notify_levels: tuple[str, ...] | None = None, archiver=None):
        """notify_levels=() 이면 알림 끔(테스트용). 그 밖에는 설정의 알림 규칙(notify_policy)을 따름"""
        from .notify_policy import NotifyPrefs
        self.s = settings
        self.storage = storage
        self.analyzer = analyzer
        self.notify_prefs = None if notify_levels == () else NotifyPrefs.from_settings(settings)
        self.notified: list[tuple] = []   # 최근 알림 (확인용)
        self.archiver = archiver   # extensions.archive.Archiver (없으면 보관 안 함)

    def _archive_on(self, m: Message) -> bool:
        return bool(self.archiver and self.archiver.enabled and m.source in self.s.archive_sources)

    def _allowed(self, m: Message) -> bool:
        if self.s.include_senders and not any(k in m.sender for k in self.s.include_senders):
            return False
        if any(k in m.sender for k in self.s.exclude_senders):
            return False
        return True

    def skip_ai(self, m: Message) -> bool:
        """토큰 절약: 너무 짧거나 생략 키워드가 있는 메시지는 AI 없이 '참고' 처리."""
        body = m.body.strip()
        if self.s.skip_ai_min_chars and len(body) < self.s.skip_ai_min_chars:
            return True
        return any(k in body for k in self.s.skip_ai_keywords)

    def text_for_ai(self, m: Message) -> str:
        return redact(m.body, self.s.redact_names) if self.s.redact_enabled else m.body

    def handle(self, m: Message) -> Analysis | None:
        """새 메시지 1건 처리. 중복이거나 필터에 걸리면 None."""
        if not self._allowed(m):
            log.info("필터로 제외: %s", m.sender)
            return None
        if self.storage.add_message(m) is None:
            return None  # 이미 처리한 메시지
        if self._archive_on(m):
            self.archiver.save_message(m)   # 분석이 실패해도 원문은 먼저 남김 (AI 호출 없음)
        return self.analyze_saved(m)

    def analyze_saved(self, m: Message) -> Analysis | None:
        if self.skip_ai(m):
            a = Analysis(message_id=m.id, importance="참고", category="기타",
                         summary=m.body.strip().splitlines()[0][:80] if m.body.strip() else "",
                         provider="rule", model="AI 생략")
            self.storage.add_analysis(a)
            if self._archive_on(m):
                self.archiver.save_analysis(m, a)
            return a
        try:
            a = self.analyzer.analyze(m, text_for_ai=self.text_for_ai(m))
        except Exception as e:  # 네트워크 오류 등 → 나중에 재분석 가능 (unanalyzed_messages)
            log.error("AI 분석 실패 (message_id=%s): %s", m.id, e)
            return None
        self.storage.add_analysis(a)
        if self._archive_on(m):
            self.archiver.save_analysis(m, a)
        self._suggest_events(m, a)
        self._maybe_notify(m, a)
        return a

    def _maybe_notify(self, m: Message, a: Analysis) -> bool:
        from .notify_policy import decide, toast_text
        if not self.notify_prefs:
            return False
        ok, why = decide(self.notify_prefs, m, a, done=bool(m.id and self.storage.is_done(m.id)))
        if ok:
            title, body = toast_text(self.notify_prefs, m, a)
            notify(title, body, sound=self.notify_prefs.sound)
            self.notified.append((m.id, title))
            del self.notified[:-50]
        return ok

    def _suggest_events(self, m: Message, a: Analysis) -> int:
        """AI 가 뽑은 일정을 바탕화면 캘린더에 '제안'(또는 바로 확정)으로 올림"""
        if not self.s.calendar_auto_suggest:
            return 0
        n = 0
        status = "confirmed" if self.s.calendar_auto_confirm else "suggested"
        for e in a.events:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.date or ""):
                continue
            note = e.note or a.summary
            if self.storage.add_calendar_event(title=e.title, date=e.date, start_time=e.start_time,
                                               end_time=e.end_time, location=e.location, note=note,
                                               status=status, origin="ai", message_id=m.id):
                n += 1
        return n

    def retry_failed(self) -> int:
        n = 0
        for m in self.storage.unanalyzed_messages():
            if self.analyze_saved(m):
                n += 1
        return n


def build_pipeline(settings: Settings, storage: Storage | None = None,
                   notify_levels: tuple[str, ...] | None = None) -> Pipeline:
    """설정의 모델 슬롯(분석·브리핑·대체·사고 수준)대로 파이프라인을 조립합니다."""
    storage = storage or Storage(settings.db_path)

    def on_usage(purpose, p):
        u = p.last_usage or {}
        storage.add_usage(purpose, p.name, p.model, u.get("input_tokens", 0), u.get("output_tokens", 0))

    analyze_p = create_provider(settings, settings.analyze_model)
    same = parse_spec(settings.digest_model) == parse_spec(settings.analyze_model)
    from .analyzer import school_profile_dict
    analyzer = Analyzer(
        analyze_p, role=settings.user_role, on_usage=on_usage, school_profile=school_profile_dict(settings),
        digest_provider=analyze_p if same else None,
        digest_factory=None if same else (lambda: create_provider(settings, settings.digest_model)),
    )
    from .extensions.archive import build_archiver
    archiver = build_archiver(settings, redact_fn=lambda t: redact(t, settings.redact_names))
    return Pipeline(settings, storage, analyzer, notify_levels=notify_levels, archiver=archiver)

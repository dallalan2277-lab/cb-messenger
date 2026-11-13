"""수집 → 필터 → 저장 → 개인정보 가리기 → AI 분석 → 알림 흐름."""
from __future__ import annotations

import logging
import re

from .analyzer import Analyzer
from .event_match import classify
from .config import Settings
from .llm.factory import create_provider, parse_spec
from .models import Analysis, Message
from .notifier import notify
from .redact import redact
from .storage import Storage

log = logging.getLogger(__name__)

# 날짜·시각처럼 '일정이 들어 있을 수 있는' 표시. 이게 있으면 생략 키워드가 있어도 AI 에 보냄.
# (예전 기본 키워드 '감사합니다' 때문에 '11. 17.(화) 08:30 회의 … 감사합니다' 같은 공지가 AI 없이 '참고'로 넘어갔음)
_SCHEDULE_HINT = re.compile(
    r"\d{1,2}\s*월\s*\d{1,2}\s*일"                                    # 11월 17일
    r"|\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}"                     # 2026. 11. 17 / 2026-11-17
    r"|\d{1,2}\s*[./]\s*\d{1,2}\s*\.?\s*\(\s*[월화수목금토일]\s*\)"   # 11. 17.(화) / 11/17(화)
    r"|\d{1,2}\s*일\s*\(\s*[월화수목금토일]\s*\)"                      # 17일(화)
    r"|(?<![\d./])\d{1,2}\s*/\s*\d{1,2}(?![\d/])"                     # 11/17 (요일 없어도)
    r"|(?<![\d.])\d{1,2}\s*\.\s*\d{1,2}\s*\.(?!\d)"                   # 11. 17. / 11.17.
    r"|(?<![\d.])\d{1,2}\s*\.\s*\d{1,2}\s*(?:까지|부터|일|\()"        # 11.17까지
    r"|(?<!\d)\d{1,2}\s*일\s*(?:까지|부터|오전|오후|\d{1,2}\s*시)"      # 17일까지 / 17일 15시
    r"|(?<!\d)\d{1,2}\s*:\s*\d{2}(?!\d)"                              # 08:30
    r"|(?:오전|오후)\s*\d{1,2}\s*시"                                   # 오후 3시
    r"|(?<!\d)\d{1,2}\s*시\s*\d{1,2}\s*분"                             # 3시 30분
    r"|(?<!\d)\d{1,2}\s*시\s*(?:까지|부터|에|정각|~)"                  # 15시까지 ('1시간' 은 아님)
)


def has_schedule_hint(text: str) -> bool:
    """본문에 날짜·시각이 보이면 True (AI 생략 키워드보다 우선)."""
    return bool(_SCHEDULE_HINT.search(text or ""))


class Pipeline:
    def __init__(self, settings: Settings, storage: Storage, analyzer: Analyzer,
                 notify_levels: tuple[str, ...] | None = None, archiver=None):
        """notify_levels=() 이면 알림 끔(테스트용). 그 밖에는 설정의 알림 규칙(notify_policy)을 따름"""
        from .notify_policy import NotifyPrefs
        self.s = settings
        self.storage = storage
        self.analyzer = analyzer
        self.last_ai_error = ""
        self.notify_prefs = None if notify_levels == () else NotifyPrefs.from_settings(settings)
        self.notified: list[tuple] = []   # 최근 알림 (확인용)
        self.archiver = archiver   # extensions.archive.Archiver (없으면 보관 안 함)

    def _archive_on(self, m: Message) -> bool:
        return bool(self.archiver and self.archiver.enabled and m.source in self.s.archive_sources)

    @staticmethod
    def _name_key(name: str) -> str:
        """이름 비교용: 공백과 끝의 '선생님'·'님' 을 없앰 ('홍길동 선생님' = '홍길동')"""
        k = re.sub(r"\s+", "", name or "")
        return re.sub(r"(선생님|님)$", "", k)

    def is_from_me(self, m: Message) -> bool:
        """내가 보낸 쪽지인지 (설정의 '내 이름' 과 보낸 사람이 정확히 같을 때만 — 이름 일부만 같은 다른 선생님은 그대로)"""
        if m.source != "eztalk" or not getattr(self.s, "my_names", None):
            return False
        me = {self._name_key(n) for n in self.s.my_names if self._name_key(n)}
        return self._name_key(m.sender) in me

    def _allowed(self, m: Message) -> bool:
        if self.s.include_senders and not any(k in m.sender for k in self.s.include_senders):
            return False
        if any(k in m.sender for k in self.s.exclude_senders):
            return False
        return True

    def skip_ai(self, m: Message) -> bool:
        """토큰 절약: 너무 짧거나 생략 키워드가 있는 메시지는 AI 없이 '참고' 처리.
        단, 날짜·시각이 보이는 메시지는 키워드가 있어도 AI 에 보냄 (일정을 놓치지 않게)."""
        body = m.body.strip()
        if self.s.skip_ai_min_chars and len(body) < self.s.skip_ai_min_chars:
            return True
        if not any(k in body for k in self.s.skip_ai_keywords):
            return False
        return not has_schedule_hint(body)

    def text_for_ai(self, m: Message) -> str:
        return redact(m.body, self.s.redact_names) if self.s.redact_enabled else m.body

    def _seen_before(self, m: Message) -> bool:
        """쪽지는 다시 열어 보기만 해도 창이 떠서 수집기가 새 쪽지로 읽음 → 이미 저장된 것과 같으면 건너뜀.
        (처음엔 알림 창의 잘린 본문만 저장됐다면 더 긴 본문으로 보충만 하고 AI 는 다시 부르지 않음)"""
        if m.source != "eztalk":
            return False
        old = self.storage.find_similar_message(m.source, m.sender, m.body)
        if not old:
            return False
        if len(self.storage.body_key(m.body)) > len(self.storage.body_key(old["body"])):
            self.storage.update_message_body(old["id"], m.body)
        log.info("예전에 받은 쪽지라 건너뜀 (message_id=%s)", old["id"])
        return True

    def handle(self, m: Message) -> Analysis | None:
        """새 메시지 1건 처리. 중복이거나 필터에 걸리면 None."""
        if not self._allowed(m):
            log.info("필터로 제외: %s", m.sender)
            return None
        if self.is_from_me(m):
            log.info("내가 보낸 쪽지라 건너뜀")
            return None
        if self._seen_before(m):
            return None  # 예전에 받은 쪽지를 메신저에서 다시 열어 본 것
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
            self._check_cancellation(m)
            return a
        try:
            a = self.analyzer.analyze(m, text_for_ai=self.text_for_ai(m))
        except Exception as e:  # 네트워크 오류 등 → 나중에 재분석 가능 (unanalyzed_messages)
            log.error("AI 분석 실패 (message_id=%s): %s", m.id, e)
            self.last_ai_error = str(e)
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

    def _check_cancellation(self, m: Message) -> dict | None:
        """'○일 ○○ 취소' 쪽지면 그 일정에 '취소된 것 같아요' 묻기 제안 (이 PC 안에서 원문 비교만, AI 안 씀)"""
        if not self.s.calendar_auto_suggest or not m.id:
            return None
        return self.storage.detect_cancellation(message_id=m.id, text=m.body, ref_date=(m.received_at or "")[:10])

    def _suggest_events(self, m: Message, a: Analysis) -> int:
        """AI 가 뽑은 일정을 바탕화면 캘린더에 '제안'(또는 바로 확정)으로 올림.
        취소 안내를 먼저 확인해서, 취소된 그 일정을 AI 가 또 일정으로 뽑았으면 제안하지 않음."""
        if not self.s.calendar_auto_suggest:
            return 0
        cancel = self._check_cancellation(m)
        cancelled = self.storage.get_calendar_event(cancel["dup_of"]) if cancel else None
        n = 0
        status = "confirmed" if self.s.calendar_auto_confirm else "suggested"
        for e in a.events:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.date or ""):
                continue
            if cancelled and classify(vars(e), cancelled)[0] == "same":   # 시각이 다른 오후 일정 등은 그대로 제안
                continue
            note = e.note or a.summary
            result = self.storage.suggest_event(title=e.title, date=e.date, start_time=e.start_time,
                                                end_time=e.end_time, location=e.location, note=note,
                                                status=status, origin="ai", message_id=m.id,
                                                source_text=m.body, source_date=(m.received_at or "")[:10])
            if result["action"] in ("created", "asked"):
                n += 1
        return n

    def reanalyze(self, message_id: int) -> Analysis:
        """저장된 쪽지·메일을 지금 설정의 AI 로 다시 분석 (예전 분석은 새 결과가 나온 뒤에만 지움).
        알림은 다시 보내지 않고, 새로 찾은 일정은 지금처럼 '제안'으로 올림(같은 일정이 있으면 건너뜀)."""
        m = self.storage.message_obj(message_id)
        if m is None:
            raise ValueError("메시지를 찾을 수 없습니다.")
        a = self.analyzer.analyze(m, text_for_ai=self.text_for_ai(m))
        new_id = self.storage.add_analysis(a)
        self.storage.delete_older_analyses(m.id, new_id)
        self._suggest_events(m, a)
        return a

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

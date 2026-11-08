"""API 키 없이 동작을 확인하기 위한 가짜 제공자 (키워드 규칙 기반).

AI_PROVIDER=fake 로 설정하거나 --provider fake 로 실행하면 사용됩니다.
실제 요약 품질과는 무관하며, 화면·저장·알림 흐름 테스트용입니다.
"""
from __future__ import annotations

import json
import re

from .base import LLMProvider


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, model: str = "rule-based"):
        super().__init__(model)

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        self.last_usage = {"input_tokens": len(system + user) // 2, "output_tokens": 50}
        if "브리핑" in system:
            return json.dumps({"headline": "(테스트) 브리핑", "urgent": [], "this_week": [], "fyi": []},
                              ensure_ascii=False)
        try:
            item = json.loads(user)["item"]
            body = (item.get("subject", "") + "\n" + item["body"]).strip()
        except (ValueError, KeyError, TypeError):
            body = user.split("내용:\n", 1)[-1]
        if re.search(r"긴급|오늘까지|즉시|사고|내일까지", body):
            imp = "긴급"
        elif re.search(r"회의|제출|마감|참석|설문|공문|연수", body):
            imp = "중요"
        else:
            imp = "참고"
        lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("[제목]")]
        first = lines[0] if lines else ""
        school = "학교 업무" if re.search(r"학교|교무|학생|학년|담임|회의|교육청|연수|공문", body) else "판단 보류"
        return json.dumps({
            "importance": imp, "category": "기타", "summary": first[:80], "reason": "(테스트) 키워드 규칙",
            "action_items": [], "evidence": "", "deadline": "", "events": [],
            "school_relevance": school, "school_reason": "(테스트)",
            "school_evidence": first[:40] if school == "학교 업무" else "",
        }, ensure_ascii=False)

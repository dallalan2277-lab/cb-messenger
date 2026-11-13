"""AI 생략 규칙: 키워드가 있어도 날짜·시각이 보이면 AI 에 보낸다. (가상 예시만 사용)"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline import Pipeline, has_schedule_hint


def _skip(body, keywords=("식단표", "급식", "생일 축하", "감사합니다"), min_chars=6):
    fake = SimpleNamespace(s=SimpleNamespace(skip_ai_keywords=list(keywords), skip_ai_min_chars=min_chars))
    return Pipeline.skip_ai(fake, SimpleNamespace(body=body))


@pytest.mark.parametrize("body", [
    "[가상 위원회 참석 안내] 아래와 같이 개최합니다. 1. 일시: 2026. 11. 17.(화) 08:30~ 2. 장소: 협의실 감사합니다~^^",
    "다음 주 11월 20일 연수가 있습니다. 감사합니다.",
    "내일 오후 3시 회의실에서 뵙겠습니다. 감사합니다",
    "12/3(수) 학년 협의회 있습니다 감사합니다",
    "3시 30분까지 제출 부탁드립니다. 감사합니다.",
    "급식 의견은 11/17까지 제출해 주세요",
    "급식 만족도 조사 11. 17.까지 부탁드립니다. 감사합니다",
    "급식 신청은 17일 15시까지입니다 감사합니다",
    "17일까지 회신 부탁드립니다. 감사합니다",
    "15시까지 제출 바랍니다 감사합니다",
    "11.17까지 입력 바랍니다 감사합니다",
    "급식 신청 마감: 11.17 15시",
    "급식 신청 15시 마감입니다",
])
def test_keyword_message_with_date_or_time_goes_to_ai(body):
    assert has_schedule_hint(body)
    assert _skip(body) is False


@pytest.mark.parametrize("body", [
    "자료 잘 받았습니다. 감사합니다.",
    "이번 달 식단표 첨부합니다.",
    "생일 축하드립니다! 좋은 하루 보내세요",
    "회의는 1시간 정도 걸립니다. 감사합니다",   # '1시간' 은 시각이 아님
    "만족도 3.5점 나왔습니다. 감사합니다",       # 소수는 날짜가 아님
    "3일간 고생 많으셨습니다. 감사합니다",
    "급식은 1/2인분씩 배식했습니다",
])
def test_keyword_message_without_schedule_is_still_skipped(body):
    assert _skip(body) is True


def test_short_message_is_skipped_even_with_time():
    assert _skip("8:30", min_chars=6) is True


def test_no_keyword_is_not_skipped():
    assert _skip("2학년 협의회 자료 확인 부탁드려요") is False


def test_default_env_example_has_no_thanks_keyword():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("SKIP_AI_KEYWORDS="))
    assert "감사합니다" not in line

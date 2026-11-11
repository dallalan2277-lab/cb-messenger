"""쪽지 보기 창에서 본문 고르기 — 보낸 사람 소속 줄을 본문으로 잘못 고르던 문제 (가상 예시)."""
from app.collectors.eztalk_parse import parse_view

HEAD = ["받은쪽지", "11/20(금) 2학년 체험학습 안내", "보낸사람 :", "홍길동",
        "101(본관교무실) / 070-0000-0000 / 교무부 / 2-3담임",
        "- 101(본관교무실) / 070-0000-0000 / 교무부 / 2-3담임"]
TIME = ["11월18일09시30분", "(11월18일09시30분)"]
TO = ["받는사람 :", "(12)", ":", "김철수,이영희 ...", "10/12", "(", "10/12", ")"]
NOTICE = ["※「정보통신망 이용촉진 및 정보보호 등에 관한 법률」을 준수하여, 소통메신저 이용자는 개인의 사생활 침해, "
          "명예훼손, 상업적 홍보 등의 내용은 자제하여 주시기 바랍니다."]
BODY = "안녕하세요 선생님! 11/20(금) 2학년 체험학습이 있습니다. -장소: 과학관 -준비물: 도시락"
PIECES = ["안녕하세요 선생님!", "11/20(금) 2학년 체험학습이 있습니다.", "-장소:", "과학관", "-준비물:", "도시락"]


def test_full_window_picks_real_body():
    ev = parse_view(HEAD + TIME + TO + NOTICE + [BODY] + PIECES)
    assert ev["ok"] and ev["sender"] == "홍길동" and ev["body"] == BODY
    assert ev["time"] == "11월18일09시30분" and ev["title"].startswith("11/20(금)")


def test_half_loaded_window_does_not_use_profile_line_as_body():
    """창이 막 열려 본문·시각이 아직 없을 때: 예전에는 소속 줄을 본문으로 골랐음 → 이제 '덜 읽힘'(ok False)"""
    ev = parse_view(HEAD)
    assert ev["body"] == "" and ev["ok"] is False
    ev = parse_view(HEAD + TIME + TO + NOTICE)
    assert ev["body"] == "" and ev["ok"] is False


def test_profile_line_is_not_body_even_without_notice():
    ev = parse_view(HEAD + TIME + [BODY] + PIECES)
    assert ev["ok"] and ev["body"] == BODY


def test_short_single_line_body_still_found():
    ev = parse_view(HEAD + TIME + TO + NOTICE + ["넵 확인했습니다"])
    assert ev["ok"] and ev["body"] == "넵 확인했습니다"


def test_body_that_mentions_phone_and_room_is_kept():
    body = "회의실이 101(본관교무실)로 바뀌었습니다. 문의 070-0000-0000"
    ev = parse_view(HEAD + TIME + TO + NOTICE + [body])
    assert ev["ok"] and ev["body"] == body          # 소속 줄과 글자가 똑같지 않으면 본문으로 인정

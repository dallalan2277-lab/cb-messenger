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


ATTACH = ["첨부파일", "2학년 체험학습 동의서.hwp (31.2 KB) ~ 2027년11월20일", "2학년 체험학습 동의서.hwp",
          "(31.2 KB)", "~ 2027년11월20일"]


def test_attachment_row_is_not_body():
    """첨부파일 줄(파일 이름이 한 번 더 나옴)을 본문으로 고르던 문제"""
    ev = parse_view(HEAD + TIME + TO + NOTICE + ATTACH + [BODY] + PIECES)
    assert ev["ok"] and ev["body"] == BODY


LONG_PIECES = ["[체험학습 안내]", "안녕하세요,", "다음 주에", "2학년 체험학습", "을 갑니다. 아이들이 즐겁고 안전하게 다녀올 수 있도록 미리 준비해 주시기 바랍니다.",
               "•", "일시:", "11월 20일(금) 09:00~15:00", "동의서를 작성하여 제출", "해 주시기 바랍니다."]
CUT = "[체험학습 안내] 안녕하세요,  다음 주에 2학년 체험학습을 갑니다. 아이들이 즐겁고 안전하게"


def test_cut_off_body_is_rebuilt_from_pieces():
    """긴 쪽지는 본문 덩어리가 중간에서 잘려 읽힘 → 뒤의 줄 조각을 이어 붙여 날짜·요청까지 살림"""
    ev = parse_view(HEAD + TIME + TO + NOTICE + ATTACH + [CUT] + LONG_PIECES)
    assert ev["ok"]
    assert "11월 20일(금) 09:00~15:00" in ev["body"] and "제출" in ev["body"]
    assert ev["body"].startswith("[체험학습 안내] 안녕하세요,")
    assert "동의서.hwp" not in ev["body"]


def test_body_missing_bullet_list_is_rebuilt_from_pieces():
    """덩어리는 끝까지 읽혔지만 글머리표 목록(일시)이 빠진 경우"""
    whole_but_no_list = ("[체험학습 안내] 안녕하세요,  다음 주에 2학년 체험학습을 갑니다. 아이들이 즐겁고 안전하게 다녀올 수 있도록 "
                         "미리 준비해 주시기 바랍니다. 동의서를 작성하여 제출해 주시기 바랍니다.")
    ev = parse_view(HEAD + TIME + TO + NOTICE + ATTACH + [whole_but_no_list] + LONG_PIECES)
    assert ev["ok"] and "11월 20일(금) 09:00~15:00" in ev["body"] and "제출" in ev["body"]


def test_cut_off_body_when_title_piece_is_missing():
    ev = parse_view(["받은쪽지", "[체험학습 안내]", "보낸사람 :", "홍길동"] + TIME + TO + NOTICE
                    + [CUT] + LONG_PIECES[1:])
    assert ev["ok"] and "09:00~15:00" in ev["body"] and ev["body"].startswith("[체험학습 안내]")


def test_body_that_mentions_phone_and_room_is_kept():
    body = "회의실이 101(본관교무실)로 바뀌었습니다. 문의 070-0000-0000"
    ev = parse_view(HEAD + TIME + TO + NOTICE + [body])
    assert ev["ok"] and ev["body"] == body          # 소속 줄과 글자가 똑같지 않으면 본문으로 인정

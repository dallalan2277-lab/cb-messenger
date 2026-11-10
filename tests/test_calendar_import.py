"""다른 캘린더 파일에서 일정 가져오기 — 가상 예시 파일만 사용 (네트워크·Windows 없이)."""
import zipfile
from datetime import date

import pytest

from app.desktop_calendar.api import CalendarAPI
from app.extensions.calendar_import import (ImportError_, parse_date, parse_file, parse_lines, parse_time,
                                            read_xlsx_rows)
from app.storage import Storage


# ---------- 가상 엑셀 파일 만들기 (openpyxl 없이) ----------
def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(path, rows, sheet_name="xl/worksheets/sheet1.xml"):
    """rows: [[값, ...], ...] — 글자는 공유 문자열, 숫자(int/float)는 숫자 칸"""
    shared, xml_rows = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{_col(c)}{r}"
            if v is None or v == "":
                continue
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                shared.append(str(v).replace("&", "&amp;").replace("<", "&lt;"))
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    target = sheet_name.replace("xl/", "")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets><sheet name="일정" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="x" Target="{target}"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr(sheet_name, f'<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    return path


# ---------- 날짜·시각 ----------
@pytest.mark.parametrize("text,expected", [
    ("2026-11-12", date(2026, 11, 12)), ("2026.11.12.", date(2026, 11, 12)), ("2026. 11. 12.", date(2026, 11, 12)),
    ("2026/11/12", date(2026, 11, 12)), ("2026년 11월 12일 (목)", date(2026, 11, 12)), ("20261112", date(2026, 11, 12)),
    ("11/12", date(2026, 11, 12)), ("11월 12일", date(2026, 11, 12)), ("46338", date(2026, 11, 12)),
    ("2026-02-30", None), ("", None), ("학년 협의회", None),
])
def test_parse_date(text, expected):
    assert parse_date(text, 2026) == expected


@pytest.mark.parametrize("text,expected", [
    ("14:00", "14:00"), ("오후 2시", "14:00"), ("오전 9시 30분", "09:30"), ("2시", "14:00"), ("3시 반", "15:30"),
    ("오전 12시", "00:00"), ("오후 12:10", "12:10"), ("9:05", "09:05"), ("25:00", ""), ("3학년 협의회", ""),
])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


# ---------- .ics ----------
ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0",
    "BEGIN:VEVENT", "DTSTART;TZID=Asia/Seoul:20261112T140000", "DTEND;TZID=Asia/Seoul:20261112T153000",
    "SUMMARY:학년 협의회", "LOCATION:2층 회의실", "DESCRIPTION:자료 지참\\n노트북", "END:VEVENT",
    "BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261120", "DTEND;VALUE=DATE:20261123", "SUMMARY:수학여행", "END:VEVENT",
    "BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261201", "DTEND;VALUE=DATE:20261202",
    "SUMMARY:아주 긴 제목이 다음 줄로", " 접혀 있음", "RRULE:FREQ=WEEKLY", "END:VEVENT",
    "END:VCALENDAR", ""])


def test_ics(tmp_path):
    p = tmp_path / "가상 캘린더.ics"
    p.write_text(ICS, encoding="utf-8")
    r = parse_file(p)
    ev = r["events"]
    assert r["kind"] == "ics" and r["name"] == "가상 캘린더.ics"
    assert ev[0] == {"date": "2026-11-12", "title": "학년 협의회", "start_time": "14:00", "end_time": "15:30",
                     "location": "2층 회의실", "note": "자료 지참\n노트북"}
    assert [e["date"] for e in ev[1:4]] == ["2026-11-20", "2026-11-21", "2026-11-22"]   # 끝 날짜(23일)는 빼고
    assert ev[4]["title"] == "아주 긴 제목이 다음 줄로접혀 있음" and len(ev) == 5
    assert any("반복" in w for w in r["warnings"])


# ---------- 표 (엑셀·csv) ----------
def test_xlsx_table_with_header_and_excel_date_numbers(tmp_path):
    p = make_xlsx(tmp_path / "학사일정.xlsx", [
        ["2026학년도 2학기 학사일정"],
        [],
        ["날짜", "행사명", "시간", "장소", "비고"],
        [46338, "학년 협의회", 0.5833333333, "2층 회의실", ""],
        ["2026.11.13", "학부모 상담", "오후 3시", "", "희망자"],
        ["11/20~11/21", "수학여행", "", "", ""],
        ["날짜 미정", "체육대회 준비", "", "", ""],
        ["", "", "", "", ""],
    ], sheet_name="xl/worksheets/sheet7.xml")     # 시트 파일 이름이 sheet1 이 아니어도 읽음
    r = parse_file(p)
    assert r["kind"] == "table" and r["columns"][:2] == ["날짜", "행사명"]
    assert r["mapping"] == {"date": 0, "title": 1, "start_time": 2, "location": 3, "note": 4}
    ev = r["events"]
    assert (ev[0]["date"], ev[0]["start_time"], ev[0]["location"]) == ("2026-11-12", "14:00", "2층 회의실")
    assert (ev[1]["date"], ev[1]["start_time"], ev[1]["note"]) == ("2026-11-13", "15:00", "희망자")
    assert [e["date"] for e in ev[2:]] == ["2026-11-20", "2026-11-21"]
    assert any("1개" in w for w in r["warnings"])     # '날짜 미정' 줄은 건너뜀


def test_table_mapping_can_be_changed(tmp_path):
    p = tmp_path / "일정.csv"
    p.write_text("구분,일자,할일\n행사,2026-11-12,학년 협의회\n", encoding="utf-8")
    r = parse_file(p)
    assert r["mapping"]["date"] == 1 and r["events"][0]["title"] == "학년 협의회"
    r2 = parse_file(p, {"date": 1, "title": 0})
    assert r2["events"][0]["title"] == "행사"


def test_csv_in_korean_windows_encoding_and_no_header(tmp_path):
    p = tmp_path / "일정.csv"
    p.write_bytes("2026-11-12,학년 협의회\n2026-11-13,교직원 회의\n".encode("cp949"))
    r = parse_file(p)
    assert [e["title"] for e in r["events"]] == ["학년 협의회", "교직원 회의"]


def test_table_without_title_column_asks_for_mapping(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("날짜,시간\n2026-11-12,14:00\n", encoding="utf-8")
    r = parse_file(p)
    assert r["events"] == [] and any("골라" in w for w in r["warnings"])


# ---------- 주간 달력 모양 엑셀 ----------
def test_weekly_calendar_xlsx(tmp_path):
    p = make_xlsx(tmp_path / "주간.xlsx", [
        ["2026.11", "일", "월", "화", "수", "목", "금", "토"],
        ["", "29", "30\n교직원 회의", "1", "2", "3\n2시 학년 협의회\n(2층 회의실)", "4", "5"],
        ["", "6", "7", "8", "9", "10", "11", "12"],
        ["", "27", "28", "29", "30", "31\n종업식", "1\n방학", "2"],
    ])
    r = parse_file(p)
    assert r["kind"] == "weekly"
    got = [(e["date"], e["start_time"], e["title"]) for e in r["events"]]
    assert ("2026-11-30", "", "교직원 회의") in got
    assert ("2026-12-03", "14:00", "2시 학년 협의회 (2층 회의실)") in got     # 다음 줄 '(장소)' 는 붙임
    assert ("2027-01-01", "", "방학") in got                                   # 해 넘김


# ---------- 텍스트 ----------
def test_text_lines_and_date_headings():
    r = parse_lines("2026년 11월 12일 (목) 14:00 학년 협의회\n"
                    "11/13 오후 3시 30분~5시 학부모 상담\n"
                    "11.20~11.22 수학여행\n"
                    "11월 25일(화)\n- 9:00 교직원 회의\n  급식 점검\n"
                    "아무 의미 없는 줄\n")
    got = [(e["date"], e["start_time"], e["end_time"], e["title"]) for e in r["events"]]
    assert got == [("2026-11-12", "14:00", "", "학년 협의회"),
                   ("2026-11-13", "15:30", "17:00", "학부모 상담"),
                   ("2026-11-20", "", "", "수학여행"), ("2026-11-21", "", "", "수학여행"),
                   ("2026-11-22", "", "", "수학여행"),
                   ("2026-11-25", "09:00", "", "교직원 회의"), ("2026-11-25", "", "", "급식 점검")]
    assert r["warnings"] == ["날짜를 찾지 못한 줄 1개는 건너뜁니다."]


def test_unsupported_and_broken_files(tmp_path):
    (tmp_path / "옛날.xls").write_bytes(b"x")
    with pytest.raises(ImportError_, match="xlsx"):
        parse_file(tmp_path / "옛날.xls")
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    with pytest.raises(ImportError_, match="읽을 수 있는"):
        parse_file(tmp_path / "a.pdf")
    (tmp_path / "b.xlsx").write_bytes(b"not a zip")
    with pytest.raises(ImportError_, match="열 수 없습니다"):
        parse_file(tmp_path / "b.xlsx")
    with pytest.raises(ImportError_, match="찾을 수 없습니다"):
        parse_file(tmp_path / "없음.ics")
    (tmp_path / "empty.txt").write_text("아무 일정 없음\n", encoding="utf-8")
    assert parse_file(tmp_path / "empty.txt")["events"] == []


def test_read_xlsx_rows_keeps_column_positions(tmp_path):
    p = make_xlsx(tmp_path / "x.xlsx", [["가", "", "다"], [], ["", "나"]])
    assert read_xlsx_rows(p) == [["가", "", "다"], [], ["", "나"]]


# ---------- 캘린더에 넣기 · 되돌리기 ----------
def _api(tmp_path):
    return CalendarAPI(Storage(str(tmp_path / "t.db")), tmp_path / "prefs.json")


def _txt(tmp_path, text, name="일정.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_preview_marks_existing_and_repeated_events(tmp_path):
    api = _api(tmp_path)
    api.add({"title": "학년 협의회", "date": "2026-11-12", "start_time": "14:00"})
    r = api.import_preview(_txt(tmp_path, "2026-11-12 14:00 학년 협의회\n2026-11-13 교직원 회의\n"
                                          "2026-11-13 교직원 회의\n"))
    assert r["ok"] and r["total"] == 3 and r["dup_count"] == 2
    assert [e["dup"] for e in r["events"]] == [True, False, True]
    assert r["field_labels"]["date"] == "날짜"


def test_apply_selected_then_undo(tmp_path):
    api = _api(tmp_path)
    refreshed = []
    api._host = type("H", (), {"refresh": lambda self: refreshed.append(1)})()
    r = api.import_preview(_txt(tmp_path, "2026-11-12 학년 협의회\n2026-11-13 교직원 회의\n2026-11-14 체육대회\n"))
    out = api.import_apply(r["token"], [0, 2])
    assert out["ok"] and out["added"] == 2 and out["skipped"] == 0 and out["batch"]
    month = api.month(2026, 11)
    titles = {e["title"]: e for w in month["weeks"] for d in w for e in d["events"]}
    assert set(titles) == {"학년 협의회", "체육대회"}
    assert titles["체육대회"]["status"] == "confirmed" and titles["체육대회"]["origin"] == "import"
    last = api.get_prefs()["last_import"]
    assert last["count"] == 2 and last["name"] == "일정.txt" and refreshed

    api.add({"title": "직접 넣은 일정", "date": "2026-11-12"})
    assert api.import_undo() == {"ok": True, "removed": 2}
    left = [e["title"] for w in api.month(2026, 11)["weeks"] for d in w for e in d["events"]]
    assert left == ["직접 넣은 일정"]                       # 직접 넣은 일정은 그대로
    assert api.get_prefs()["last_import"] is None
    assert api.import_undo()["ok"] is False


def test_same_file_twice_does_not_duplicate(tmp_path):
    api = _api(tmp_path)
    path = _txt(tmp_path, "2026-11-12 학년 협의회\n")
    r1 = api.import_preview(path)
    assert api.import_apply(r1["token"])["added"] == 1
    r2 = api.import_preview(path)
    assert r2["dup_count"] == 1
    out = api.import_apply(r2["token"])                     # 일부러 다시 넣어도
    assert out["added"] == 0 and out["skipped"] == 1 and out["batch"] == ""


def test_apply_needs_fresh_preview_and_selection(tmp_path):
    api = _api(tmp_path)
    r = api.import_preview(_txt(tmp_path, "2026-11-12 학년 협의회\n"))
    assert "넣을 일정" in api.import_apply(r["token"], [])["error"]
    assert "다시 골라" in api.import_apply("엉뚱한값", None)["error"]
    assert api.import_apply(r["token"], ["0", 5, -1, "x"])["added"] == 1     # 이상한 번호는 무시
    assert "다시 골라" in api.import_apply(r["token"])["error"]               # 한 번 쓴 미리보기는 끝


def test_preview_reports_errors_in_korean(tmp_path):
    api = _api(tmp_path)
    assert "찾을 수 없습니다" in api.import_preview(str(tmp_path / "없음.ics"))["error"]
    (tmp_path / "a.xls").write_bytes(b"x")
    assert ".xlsx" in api.import_preview(str(tmp_path / "a.xls"))["error"]


def test_import_pick_without_window(tmp_path):
    assert _api(tmp_path).import_pick() == {"path": ""}


def test_old_database_gets_import_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE calendar_events (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "date TEXT NOT NULL, start_time TEXT DEFAULT '', end_time TEXT DEFAULT '', location TEXT DEFAULT '', "
                "note TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'confirmed', origin TEXT DEFAULT 'manual', "
                "message_id INTEGER, created_at TEXT, updated_at TEXT)")
    con.execute("INSERT INTO calendar_events(title, date) VALUES ('예전 일정', '2026-11-01')")
    con.commit()
    con.close()
    s = Storage(str(db))
    assert s.import_calendar_events([{"date": "2026-11-02", "title": "새 일정"}], "b1") == (1, 0)
    assert s.delete_import_batch("b1") == 1
    assert s.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 1   # 예전 일정은 그대로


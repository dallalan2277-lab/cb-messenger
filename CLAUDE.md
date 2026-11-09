# CLAUDE.md — 충북메신저 AI 비서 (cb-messenger-ai)

Claude Code 가 이 저장소에서 작업할 때 먼저 읽는 문서입니다. 더 자세한 진행 기록은 `HANDOFF.md`, 사용자용 안내는 `README.md`.
Claude Code 는 이 폴더(`cb-messenger-ai/`, 저장소 맨 위)에서 열 것.

## 여러 컴퓨터에서 작업 (GitHub)
- 작업 시작 전 `git pull`, 끝나면 `git add .` → `git commit` → `git push`.
- 컴퓨터마다 따로 해야 하는 것 (GitHub 로 안 옮겨짐): Python 3.10+ 설치 (3.10.6 에서 확인됨, 3.11 전용 문법 쓰지 말 것), `1_설치하기.bat`(.venv), `.env` 값, ChatGPT 로그인, 네이버 메일 연결, `data/`(DB·쪽지 기록).
- 진행 상황·결정은 이 문서와 `HANDOFF.md` 에 적어 둘 것 (Claude Code 의 컴퓨터별 기억은 다른 PC 로 안 옮겨짐).

## 사용자
- 충청북도교육청 소속 교사. **비개발자**. 대화와 설명은 **한국어로, 쉬운 말로**. 전문 용어는 풀어서 설명.
- PC: Windows 11, 두 모니터. 이 PC 에 소통메신저(`eztalk.exe`), Project Monday(lite), MailDesk 0.3.0 이 설치돼 있음.
- 결과물은 더블클릭으로 쓸 수 있게 (`*.bat`). 명령 프롬프트 작업을 요구할 때는 단계별로 안내.
- ChatGPT Plus 구독 사용 (OpenClaw 도 사용). API 키 대신 **구독으로 AI 를 쓰는 것을 선호**.

## 목표
충북소통메신저 쪽지 + 네이버 메일을 실시간으로 받아 AI 가 중요도별로 요약·분석하고, 일정은 **바탕화면 캘린더**에 넣는 Windows 상주 프로그램.
AI 는 **GPT 와 Claude 둘 다** 쓸 수 있어야 함 (구독 또는 API 키).

## 현재 상태 (v0.7.2, Python 약 5,900줄, 테스트 53개 통과)
| 기능 | 상태 |
|---|---|
| 소통메신저 쪽지 수집 (eztalk.exe 창 UI 자동화, Project Monday 방식) | 코드 완료 · **Windows 실사용 미확인** |
| 네이버 메일 수집 (IMAP 읽기 전용, MailDesk 방식) | 가짜 IMAP 서버로 테스트 · **실제 계정 미확인** |
| 쪽지·메일 공통 AI 분석 (중요도·분류·학교 업무 관련성·원문 근거 확인·일정 추출) | 완료 |
| AI 연결: ChatGPT 구독 / Claude 구독(claude -p) / OpenAI·Anthropic API 키 / fake | 완료 · **구독 로그인 실계정 미확인** |
| 용도별 모델(분석·브리핑·대체), 사고 수준, AI 생략 규칙, 토큰 사용량 기록 | 완료 |
| 알림 규칙 (학교 업무만·긴급만·방해금지·미리보기 숨김·소리) | 완료 |
| 원문·요약 보관 (이 PC / 구글 드라이브 동기화 폴더 따로, txt·md) | 완료 |
| 바탕화면 캘린더 (월간·입력·AI 제안 확정/무시·받은 소식 탭·공휴일·크기 조절·두 모니터·트레이) | 화면은 브라우저로 확인 · **Windows 위젯 창 미확인** |
| 설정 화면 (tkinter: 모델·저장·메일·알림·학교 정보 탭) | 코드 완료 · **화면 미확인** |
| 캘린더 ⚙ 메뉴에서 AI 모델 바로 고르기 (v0.7.0) | 완료 · 미리보기로 확인 |
| 자동 업데이트 (GitHub Releases, SHA-256 확인, `packaging\release.bat` 로 배포) (v0.7.0) | 코드 완료 · **실제 업데이트 한 바퀴 미확인** |

**가장 큰 빈칸: 사용자 PC(Windows)에서 아직 한 번도 실행해 보지 않았음.** 다음 작업은 대부분 실제 환경 확인과 수정.

## 구조
```
main.py                     명령줄 진입점 (calendar, watch, mail-login, chatgpt-login, test, gui ...)
gui.py                      설정·테스트 화면 (tkinter)
app/
  config.py                 .env → Settings (dataclass, 필드마다 default_factory 로 env 읽음), save_env()
  models.py                 Message / Analysis / Event
  analyzer.py               ★ 공통 프롬프트·JSON 스키마·원문 근거 확인(quote_found)·브리핑
  pipeline.py               필터 → 저장 → 원문 보관 → 가림 → AI 분석 → 일정 제안 → 알림. build_pipeline()
  runner.py                 ★ 쪽지·메일 수집 스레드 → queue → AI 한 줄 처리 (캘린더와 watch 가 사용)
  notify_policy.py          알림 결정(decide)·문구(toast_text)·방해금지
  notifier.py               win11toast 알림
  redact.py                 개인정보 가리기 (AI·드라이브로 보내기 전)
  storage.py                SQLite (messages, analyses, calendar_events, mail_cursor, usage) + 자동 마이그레이션
  secrets_store.py          메일 앱 비밀번호 → keyring (Windows 자격 증명 관리자)
  llm/                      AI 제공자: base(LLMProvider), openai, claude, chatgpt_auth+chatgpt_plan, claude_cli, fake, factory
  collectors/               수집기: eztalk_parse(순수 파싱)·eztalk_collector, naver_mail·mail_content, manual, windows_notification(예비)
  desktop_calendar/         calendar.html(화면, MockAPI 내장) · api.py(CalendarAPI) · widget.py(pywebview 창) · holidays_kr.json
  extensions/               archive.py(원문 보관), ics_export.py
tests/                      pytest. 네트워크·API 키·Windows 없이 돌아감
1_설치하기.bat … 8_메일연결.bat   사용자용 더블클릭 파일 (CRLF, UTF-8, chcp 65001)
packaging/                  설치 파일 만들기: build.bat → cbm.spec(PyInstaller onedir, CBMessengerAI.exe 창 없음 + cbm.exe 콘솔) → installer.iss(Inno Setup, 관리자 권한 불필요, %LOCALAPPDATA%\Programs\CBMessengerAI)
app/paths.py                실행 위치·자료 위치. 개발: 저장소 폴더 / 설치판: 사용자 자료는 %LOCALAPPDATA%\cb-messenger-ai\ (.env, data\). CBM_HOME 으로 바꿀 수 있음
```
데이터: `data/messages.db`, `data/calendar_prefs.json`, `data/쪽지기록/`, `data/calendar.log` (설치판은 `%LOCALAPPDATA%\cb-messenger-ai\data\`). ChatGPT 토큰: `%LOCALAPPDATA%\cb-messenger-ai\`.

## 주요 결정 (바꾸기 전에 사용자와 상의)
1. **쪽지 수집 = Project Monday 방식**: `eztalk.exe` 의 `TFrmAlarm`(알림 창)·`TFrmViewNote`(보기 창)을 EnumWindows 로 찾고 `uiautomation` 으로 글자만 읽음. 소통메신저는 Windows 알림 센터를 쓰지 않음. 파싱 규칙은 Monday 원본을 옮겨 왔고, 본문 줄 조각의 '보안' 단어를 보안 쪽지로 오판하던 버그는 고쳤음.
2. **메일 = 네이버만, MailDesk 방식**: imap.naver.com:993, `select(readonly=True)` + `BODY.PEEK`, UIDVALIDITY/UID 커서, 처음 연결한 날 0시 이후만, 50통·2MB 제한, 비밀번호 틀리면 멈춤. **비밀번호는 keyring 에만 저장** (파일 금지). MailDesk 코드는 exe 에서 구조·프롬프트만 참고했고 복사하지 않음.
3. **메일 화면은 간단하게**: 별도 메일 앱 없이 캘린더 옆 '받은 소식' 탭 (목록·상세·처리 완료).
4. **AI 구독 사용**: ChatGPT 는 OpenAI 공식 *Sign in with ChatGPT* (오픈소스·로컬 앱용, PKCE+루프백, `/v1/responses`, `store:false`, `stream:true`, `instructions`, temperature·max_output_tokens 금지). Claude 구독은 공식 Claude Code 를 `claude -p` 로 실행 (구독 OAuth 토큰을 꺼내 직접 호출하는 것은 Anthropic 약관 위반 → **절대 구현 금지**). 오픈소스 조건 때문에 GitHub 공개 저장소로 올리는 것을 권장해 둠.
5. **분석 안전장치 (MailDesk 에서 가져옴)**: 받은 글 안의 지시는 무시, 할 일·학교 업무 판단은 원문 인용 필수 → 인용이 원문에 없으면 할 일 비우고 '판단 보류'. 메일엔 피싱 경고 추가.
6. **알림 기본값**: 학교 업무만, 미리보기 숨김, 소리 끔, 22:00~08:00 방해금지. 막힌 알림은 몰아서 보내지 않음.
7. **캘린더 = 바탕화면 위젯 방식**: 진짜 배경(WorkerW) 안에 넣으면 입력이 안 돼서, 테두리 없는 창을 `HWND_BOTTOM` 에 고정 + `WS_EX_TOOLWINDOW`. 기본 크기는 모니터 작업 영역의 2/3(오른쪽), 끌어서 이동·크기 조절 후 기억. AI 일정은 바로 넣지 않고 **'제안' → 사용자 확정**.
8. **저장**: 이 PC(원문 그대로)와 구글 드라이브(데스크톱 동기화 폴더에 파일 쓰기, 기본 가림 적용)를 따로 켜고 끔. Monday 의 `먼데이_쪽지원문` 폴더와 겹치지 않게 `충북메신저AI_쪽지기록`.

## 지켜야 할 규칙
- 수집기는 **읽기만**: 메신저 창 클릭·입력·전송 금지, 메일 서버 쓰기(읽음 표시·삭제·이동·보내기) 금지.
- 보안 쪽지는 내용을 저장·전송하지 않음. AI 로 보내는 글은 반드시 `Pipeline.text_for_ai()`(가림) 를 거침.
- 비밀번호·토큰은 로그·파일·커밋에 남기지 않음 (`.env`, `data/` 는 .gitignore).
- AI 호출은 `create_provider(settings, spec)` / `build_pipeline(settings)` 경유. spec 형식 `제공자[:모델]` (예: `chatgpt_plan:gpt-5.6-luna`, `claude_cli:haiku`). 분석 로직에 특정 SDK 코드 넣지 않기.
- 새 수집기는 `Collector` 를 상속해 `Message(source=..., external_id=...)` 를 내면 나머지 흐름이 그대로 동작.
- 분석 결과 형식을 바꾸면 `analyzer.SCHEMA`, `_normalize`, `models.Analysis`, `storage`(칸+마이그레이션), `calendar.html` 을 함께 수정.
- DB 칸 추가는 `Storage._NEW_COLUMNS` 에 넣어 옛 DB 가 자동으로 업그레이드되게.
- 사용자가 쓰는 파일(.env, data/ 아래 모든 것) 위치는 반드시 `app.paths`(ENV_PATH, DATA_DIR) 로. `Path(__file__)` 기준으로 쓰기 금지 (설치판에서는 프로그램 폴더에 쓰면 안 됨). 다시 띄울 때는 `self_command()`.
- 함수 안에서만 import 하는 새 패키지·DLL 이 생기면 `packaging/cbm.spec` 의 collect 목록에 추가하고 설치판에서 확인.
- 테스트는 네트워크·API 키·Windows 없이 돌아가야 함 (StubProvider, FakeProvider, FakeIMAP, httpx.MockTransport 사용). 실제 메시지·메일 내용을 테스트에 넣지 않음 (가상 예시만).
- Windows 전용 코드(ctypes.windll, uiautomation, pywebview, winsdk)는 함수 안에서 import 해 리눅스에서도 import 오류가 나지 않게.
- `.bat` 파일은 CRLF + UTF-8 + `chcp 65001`. zip 으로 묶을 때 한글 파일 이름은 UTF-8 플래그로.
- 공휴일(`holidays_kr.json`)은 기억으로 넣지 말고 월력요항 등 출처를 확인해서 추가.

## Codex(GPT) 위임 기준 (이 프로젝트)
공통 규칙(Claude 총괄, 범위 분명한 구현·테스트·버그 조사는 `codex:codex-rescue` 위임, 결과 직접 검토 후 통합, 큰 변경 후 `/codex:review`)은
사용자 설정 `~/.claude/CLAUDE.md` 에 있음. 그 파일은 GitHub 로 안 옮겨지므로, 다른 PC 에서는 아래만으로도 판단할 수 있게 적어 둠.
새 PC 에서는 `docs/다른PC_설정.md` 를 보고 전역 CLAUDE.md 와 Codex 기본값(`~/.codex/config.toml`: 모델·ultra)을 맞출 것. 그 PC 의 config 에 ultra 가 없으면 Codex 가 low 로 돎 → 사용자에게 알릴 것.
- **gpt-5.6-sol — 상시 검증 (기본)**: 코드 리뷰, 테스트 작성·실행, 회귀 버그 탐지, 사양이 분명한 일반 구현 (캘린더 화면 `calendar.html`·`api.py`, `ics_export.py`, 테스트 추가 등). `--model gpt-5.6-sol`.
- **gpt-6.1-sol — 어려운 문제만 재검증**: 5.6 Sol 로 안 풀리는 오류, 복잡한 원인 분석, 중요 변경 검토. 이 프로젝트에서 중요 변경 = 로그인·토큰(`llm/chatgpt_auth.py`·`chatgpt_plan_provider.py`), IMAP·디코딩(`collectors/naver_mail.py`·`mail_content.py`), `redact.py`, `secrets_store.py`, `analyzer.py` 의 근거 확인·글 속 지시 무시, `storage.py` 마이그레이션. `--model gpt-6.1-sol`. `/codex:review` 는 모델을 못 골라 config 기본값(6.1 Sol)으로 돌므로 중요 변경 검토에 씀.
- terra·luna 는 쓰지 않음 (사용자가 직접 지정할 때만).
- 사고 수준: `--effort` 를 붙이지 않음. Codex 설정(`~/.codex/config.toml`) 기본값이 `ultra` 이고, 플러그인은 `--effort` 로 xhigh 까지만 받아서 붙이면 오히려 낮아짐.
- 학교 PC 계정은 Codex 에서 `gpt-6.1-*` 를 못 씀 (2026-11-09) → 그 PC 에서는 6.1 Sol 대신 `gpt-6-astra`(ultra 지원). 5.6 Sol 은 그대로.
- **위임하지 않음 (Claude 가 직접 + 사용자 PC 확인)**: `eztalk_collector.py`·`eztalk_parse.py`(실제 메신저 창 구조는 사용자 PC 에서만 확인 가능), `desktop_calendar/widget.py`(Win32 창 동작), tkinter 화면 모양, 설계 규칙·분석 스키마 변경 결정.
- **Codex 에 넘기지 않을 것**: 실제 쪽지·메일 원문, `.env`, `data/`(DB·쪽지기록), `messenger-check --raw` 출력, 토큰·비밀번호. 넘기면 OpenAI 서버로 전송됨 → 재현이 필요하면 가상 예시로 바꿔서.
- 위임 지시에는 이 문서의 "지켜야 할 규칙"(읽기 전용 수집, 테스트는 네트워크·키 없이, Python 3.10 호환, Windows 전용 import 는 함수 안) 을 함께 적을 것.

## 자주 쓰는 명령
```bat
python -m pytest -q                         :: 전체 테스트
python main.py --provider fake test         :: 키 없이 샘플 분석
python main.py calendar --watch             :: 캘린더 + 쪽지 + 메일 (6_바탕화면캘린더.bat)
python main.py messenger-check --raw        :: 열린 쪽지 창 읽기 확인 (저장·AI 없음)
python main.py mail-login / mail-check      :: 네이버 메일 연결 / 한 번 확인
python main.py chatgpt-login / chatgpt-models
python main.py models / usage               :: 모델 설정 / 토큰 사용량
python main.py gui                          :: 설정·테스트 화면
```
화면만 고칠 때: `app/desktop_calendar/calendar.html` 을 브라우저로 열면 예시 데이터(MockAPI)로 동작.

## 남은 작업 (우선순위 순)
1. **사용자 PC 에서 실제 확인·수정** (가장 먼저)
   - ~~설치 오류 여부~~ → 2026-11-08 확인: Python 3.10.6 에서 requirements 설치 OK, Windows 라이브러리 import OK, 테스트 52 통과·1 건너뜀(유닉스용 가짜 claude), `--provider fake test` 정상
   - ChatGPT 구독 로그인: 첫 콜백의 `client_id`, ID 토큰 검증(OIDC discovery 주소 미확인, 대체 jwks 주소 사용 중), 토큰 갱신, `text.format json_schema` 허용 여부, `reasoning` 허용 여부
   - 쪽지: `messenger-check --raw` 로 보낸 사람·본문 파싱, 알림 창→보기 창 합치기
   - 메일: 로그인, 한글 제목·본문 디코딩, 첫 연결 시 오늘 메일만 오는지
   - 캘린더 위젯: 투명 배경(`transparent=True`), 배율 125·150% 모니터에서 끌기·크기 조절 좌표, Win+D 뒤 다시 보이는지, 두 번째 모니터 이동, `api.win(action, *args)` 가변 인자 전달
   - tkinter 설정 화면 모양
2. **망분리 확인**: 학교 업무 PC 에서 외부 AI·네이버 IMAP 접속 가능한지. 안 되면 구조 재검토 필요.
3. **정보보안 확인**: 학교/교육청 담당자에게 외부 AI 전송 가능 여부 (README 에 안내해 둠).
4. **설치 파일(.exe)**: v0.6.1 에서 만듦 (`packaging\build.bat`). 2026-11-08 이 PC 에서 PyInstaller 빌드·cbm.exe 명령·캘린더/설정 창 실행 확인. 남은 것: 다른 PC 에서 설치·제거, 실제 ChatGPT 로그인을 설치판으로, 코드 서명 없음(SmartScreen 경고 → '추가 정보 → 실행').
5. **캘린더 동기화 확장**: 확정 일정 → Google Calendar API 또는 Microsoft Graph. 등록 전 사용자 확인, 중복 방지 칸 추가.
0. **다음 작업 (2026-11-09 사용자 요청, 다음 날 하기로)**
   - 설정 창·캘린더에 **Claude 로그인 버튼** (ChatGPT 로그인 버튼처럼). Claude 구독은 공식 `claude` CLI 로그인만 허용 → 버튼은 `claude` 를 새 콘솔 창으로 띄워 사용자가 직접 로그인하게 하고, `claude -p` 시험 호출로 상태(설치 안 됨/로그인 안 됨/정상) 표시. 미설치면 설치 안내(설치는 `irm https://claude.ai/install.ps1 | iex`, PATH 에 `%USERPROFILE%\.local\bin` 추가 필요했음). 토큰 파일 직접 읽기 금지.
   - **설정 창(tkinter) 디자인 개선**: 투박함 → 캘린더(calendar.html)와 색·글꼴·모서리 맞추기. tkinter 유지(ttk 테마) vs 설정 화면도 pywebview HTML 로 옮기기 중 결정 필요.
   - **밝은 테마에서 배경 투명도가 안 먹음** (calendar.html, 어두운 테마는 됨) 버그.
6. 다듬기: 오늘 브리핑 자동 알림(정해진 시각), 할 일 목록, 주간 통계, Lively Wallpaper 로 진짜 배경 표시(HTTP 어댑터), Claude 경로의 사고 수준.
7. Project Monday 원본에도 '보안' 오판 버그 수정을 알려 주기 (사용자 선택).

## 참고 자료
- Sign in with ChatGPT (오픈소스): https://developers.openai.com/siwc/token-sharing-open-source/sign-in · models-and-inference · preview-limitations · profiles-and-sessions
- Anthropic 구독 인증 정책: Claude Code 공식 바이너리를 본인 구독으로 쓰는 것은 허용, 토큰을 다른 도구로 쓰는 것은 금지
- 네이버 IMAP: 메일 환경설정 → POP3/IMAP → IMAP/SMTP 사용함, 2단계 인증 후 애플리케이션 비밀번호
- 참고한 사용자 프로그램: `project-monday(lite)\app\messenger_watch.py`, `MailDesk-Windows-0.3.0\` (exe + 테스트-시작하기.txt) — 원래 바탕화면에 있었으나 2026-11-08 기준 바탕화면에는 없음. 필요하면 사용자에게 위치를 물어볼 것.

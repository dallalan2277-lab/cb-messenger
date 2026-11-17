# CLAUDE.md — 충북메신저 AI 비서 (cb-messenger-ai)

Claude Code 가 이 저장소에서 작업할 때 자동으로 읽는 문서입니다. **오래 지켜야 할 규칙만** 둡니다 (매 세션 읽히므로 짧게).
Claude Code 는 이 폴더(`cb-messenger-ai/`, 저장소 맨 위)에서 열 것.

## 문서 역할 (세션을 새로 열어도 이어지게)
- `CLAUDE.md`(이 문서): 규칙·구조·주요 결정. 진행 상황은 적지 않음.
- `HANDOFF.md`: **지금 상태와 다음 할 일만.** 새 세션은 이것부터 읽고 시작. 작업을 마치면 맨 위 '▶ 다음에 이어서 할 일'을 고쳐 씀.
- `docs/HANDOFF_지난기록.md`: 끝난 작업의 자세한 기록(원인·고친 방법). 평소엔 읽지 않고, 필요한 절(`2-N`)만 검색. 새 기록은 맨 끝에 `2-N` 절로 추가.
- 한 세션에는 한 가지 작업만. 끝나면 HANDOFF 를 갱신하고 새 세션으로 (긴 세션은 사용량이 빨리 닳음).
- Claude Code 의 컴퓨터별 자동 기억은 다른 PC 로 안 옮겨짐 → 이 프로젝트에 필요한 것은 위 문서에 적을 것.

## 여러 컴퓨터에서 작업 (GitHub)
- 작업 시작 전 `git pull`, 끝나면 커밋·push (커밋·push 는 사용자가 요청할 때).
- 컴퓨터마다 따로 해야 하는 것 (GitHub 로 안 옮겨짐): Python 3.10+ 설치 (3.10.6 에서 확인됨, 3.11 전용 문법 쓰지 말 것), `1_설치하기.bat`(.venv), `.env` 값, ChatGPT 로그인, 네이버 메일 연결, `data/`(DB·쪽지 기록).
- 새 기능은 feature 브랜치 + GitHub pre-release(시험판)로 먼저. main 머지·정식 latest 배포는 사용자 승인 후. 설치 파일·배포는 사용자가 말할 때만.

## 사용자
- 충청북도교육청 소속 교사. **비개발자**. 대화와 설명은 **한국어로, 쉬운 말로**. 전문 용어는 풀어서 설명.
- PC: Windows 11, 두 모니터. 이 PC 에 소통메신저(`eztalk.exe`), Project Monday(lite), MailDesk 0.3.0 이 설치돼 있음.
- 결과물은 더블클릭으로 쓸 수 있게 (`*.bat`). 명령 프롬프트 작업을 요구할 때는 단계별로 안내.
- ChatGPT Plus 구독 사용 (OpenClaw 도 사용). API 키 대신 **구독으로 AI 를 쓰는 것을 선호**.

## 목표
충북소통메신저 쪽지 + 네이버 메일을 실시간으로 받아 AI 가 중요도별로 요약·분석하고, 일정은 **바탕화면 캘린더**에 넣는 Windows 상주 프로그램.
AI 는 **GPT 와 Claude 둘 다** 쓸 수 있어야 함 (구독 또는 API 키).
지금 상태·다음 할 일은 `HANDOFF.md`.

## 구조
```
main.py                     명령줄 진입점 (calendar, watch, mail-login, chatgpt-login, test, gui ...)
gui.py                      테스트 화면 (tkinter). 안의 옛 설정 대화상자(SettingsDialog)는 남아 있지만 캘린더는 새 설정 화면을 씀
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
                            settings.html(설정 화면, MockAPI 내장) · settings_api.py(SettingsAPI: 읽기·저장·로그인·메일 연결) · settings_window.py(창 띄우기)
  memo.py · ocr.py          메모 정리(MEMO_PROMPT·MEMO_SCHEMA·organize) · Windows 한국어 글자 인식(함수 안에서 winrt import)
  extensions/               archive.py(원문 보관), ics_export.py, calendar_import.py(일정 파일 읽기: ics·xlsx(zip XML 직접)·csv·txt·주간 달력)
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
- 설정 항목을 추가하면 `config.Settings`, `settings_api.py`(load·save), `settings.html`(화면 + MockAPI), `tests/test_settings_api.py` 를 함께 수정. 화면에 공개되는 메서드는 `test_only_public_methods_are_exposed` 목록에도 추가 (pywebview 는 `_` 로 시작하지 않는 것을 모두 화면에 노출).
- Claude 구독 상태·로그인은 `claude_cli_provider.claude_status()` / `open_claude_login()` 처럼 **공식 명령 실행만**. `~/.claude` 아래 로그인 파일을 읽는 코드 금지.
- 분석 결과 형식을 바꾸면 `analyzer.SCHEMA`, `_normalize`, `models.Analysis`, `storage`(칸+마이그레이션), `calendar.html` 을 함께 수정.
- DB 칸 추가는 `Storage._NEW_COLUMNS` 에 넣어 옛 DB 가 자동으로 업그레이드되게.
- 사용자가 쓰는 파일(.env, data/ 아래 모든 것) 위치는 반드시 `app.paths`(ENV_PATH, DATA_DIR) 로. `Path(__file__)` 기준으로 쓰기 금지 (설치판에서는 프로그램 폴더에 쓰면 안 됨). 다시 띄울 때는 `self_command()`.
- 함수 안에서만 import 하는 새 패키지·DLL 이 생기면 `packaging/cbm.spec` 의 collect 목록에 추가하고 설치판에서 확인.
- 테스트는 네트워크·API 키·Windows 없이 돌아가야 함 (StubProvider, FakeProvider, FakeIMAP, httpx.MockTransport 사용). 실제 메시지·메일 내용을 테스트에 넣지 않음 (가상 예시만).
- Windows 전용 코드(ctypes.windll, uiautomation, pywebview, winsdk)는 함수 안에서 import 해 리눅스에서도 import 오류가 나지 않게.
- `.bat` 파일은 CRLF + UTF-8 + `chcp 65001`. zip 으로 묶을 때 한글 파일 이름은 UTF-8 플래그로.
- 공휴일(`holidays_kr.json`)은 기억으로 넣지 말고 월력요항 등 출처를 확인해서 추가.

## Codex(GPT) 위임 기준 (이 프로젝트)
공통 규칙(Claude 총괄, 기본 6.2 Sol + max·어려운 문제 재검증 6.2 Astra + xhigh 등)은 `~/.claude/CLAUDE.md` 의 pc-settings 구역에 있음.
- 원본은 저장소 `pc-settings/claude-global.md`(Claude 용)·`codex-global.md`(Codex 용). `pc-settings/sync.ps1`(= `9_PC설정맞추기.bat`, Claude Code 시작 때 SessionStart hook 으로 자동 실행)이 `~/.claude/CLAUDE.md`·`~/.codex/AGENTS.md` 의 표시 구역과 `~/.codex/config.toml` 맨 위(모델·사고 수준)를 맞춤. 자세한 것은 `docs/다른PC_설정.md`.
- 규칙을 바꿀 때는 원본을 고치고 sync 실행 후 커밋 (`~/.claude/CLAUDE.md` 를 직접 고치면 다음 동기화 때 덮어써짐). `[pc-settings]` 로 시작하는 줄이 보이면 바뀐 내용을 사용자에게 쉬운 말로 알릴 것.
- 저장소 맨 위 `AGENTS.md` 는 Codex 용 짧은 안내. 이 문서의 규칙을 크게 바꾸면 그 요약도 맞출 것.
- 이 프로젝트의 **중요 변경(→ 6.2 Sol 검토, 필요하면 6.2 Astra xhigh 로 재검증)**: 로그인·토큰(`llm/chatgpt_auth.py`·`chatgpt_plan_provider.py`), IMAP·디코딩(`collectors/naver_mail.py`·`mail_content.py`), `redact.py`, `secrets_store.py`, `analyzer.py` 의 근거 확인·글 속 지시 무시, `storage.py` 마이그레이션. 그 밖의 분명한 구현·테스트(`calendar.html`·`api.py`·`ics_export.py` 등)도 6.2 Sol.
- 6.2 는 최신 Codex CLI 필요 (0.169.1 확인, 옛 0.154 에서는 "not supported" 오류 → PowerShell 에서 `codex update`).
- Codex 플러그인 팁: 긴 한국어 지시문은 파일로 쓰고 `task --prompt-file <파일>` 로 (직접 넘기면 따옴표 때문에 셸 오류). 작업 두 개를 같은 순간에 시작하면 브로커가 충돌 → 몇 초 띄워서. `review --model <모델>` 로 검토 모델 지정 가능.
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
python main.py settings                     :: 설정 창 (캘린더 ⚙ → 설정 열기 와 같은 화면)
python main.py gui                          :: 테스트 화면 (메시지 붙여 넣어 분석해 보기)
python main.py ocr-check 그림.png            :: 이 PC 글자 인식으로 이미지 읽어 보기 (저장·AI 없음)
```
화면만 고칠 때: `app/desktop_calendar/calendar.html` · `settings.html` 을 브라우저로 열면 예시 데이터(MockAPI)로 동작 (settings.html 은 `?theme=light`, `?claude=none`, `?claude=logout` 으로 상태를 바꿔 볼 수 있음).

## 참고 자료
- Sign in with ChatGPT (오픈소스): https://developers.openai.com/siwc/token-sharing-open-source/sign-in · models-and-inference · preview-limitations · profiles-and-sessions
- Anthropic 구독 인증 정책: Claude Code 공식 바이너리를 본인 구독으로 쓰는 것은 허용, 토큰을 다른 도구로 쓰는 것은 금지
- 네이버 IMAP: 메일 환경설정 → POP3/IMAP → IMAP/SMTP 사용함, 2단계 인증 후 애플리케이션 비밀번호
- 참고한 사용자 프로그램: `project-monday(lite)\app\messenger_watch.py`, `MailDesk-Windows-0.3.0\` (exe + 테스트-시작하기.txt) — 원래 바탕화면에 있었으나 2026-11-08 기준 바탕화면에는 없음. 필요하면 사용자에게 위치를 물어볼 것.

# HANDOFF – 이어서 개발하는 AI/개발자를 위한 문서

> GPT, Claude 등 다른 AI에게 이 프로젝트를 넘길 때 **이 파일과 README.md 를 먼저 보여주세요.**

## 0. 프로젝트 목표

충청북도교육청 업무 메신저 **"충북소통메신저"**(Windows PC 프로그램, 충북교육정보원 운영)로 받은 메시지를
실시간으로 수집해 AI가 중요도별로 요약해 알려주고, 이후 분석 · 캘린더 자동 등록까지 확장하는 Windows 11 프로그램.

- 사용자: 충북 교사 (비개발자). 설명은 한국어로, 쉬운 말로.
- 실행 환경: Windows 11, Python 3.10+ (2026-11-08 사용자 PC Python 3.10.6 에서 설치·테스트 통과 확인)
- AI: **OpenAI(GPT)와 Anthropic(Claude) 둘 다 지원해야 함** (`app/llm/` 의 공통 인터페이스 유지)
- 사용자는 **API 키 대신 구독(ChatGPT Plus / Claude Pro)으로 쓰기를 원함** → v0.2 에서 추가
- v0.6: 사용자의 MailDesk 0.3.0(네이버 메일 + ChatGPT 중요도 분석 + 알림, PyInstaller exe)을 분석해 기능을 통합.
  결정: 네이버만 / 비밀번호는 Windows 자격 증명 관리자 저장 / 메일 화면은 캘린더 옆 간단한 목록
- 메신저에는 공개 API가 없음. 메신저의 내부 파일·통신을 해킹하거나 우회하지 말 것.
  사용자 PC 화면에 이미 표시되는 정보(알림, 창 내용)만 읽는 방식으로 수집.

## 1. 현재 상태 (v0.6)

| 기능 | 상태 | 파일 |
|---|---|---|
| GPT / Claude 선택 호출, JSON 응답 파싱 | ✅ 완료 | `app/llm/*` |
| 중요도(긴급/중요/참고) 분류 · 요약 · 할 일 · 마감 · 일정 추출 | ✅ 완료 | `app/analyzer.py` |
| 하루 브리핑(digest) | ✅ 완료 | `Analyzer.digest` |
| 개인정보 가리기 (정규식 + 이름 목록) | ✅ 완료 | `app/redact.py` |
| SQLite 저장, 중복 방지, 실패 재시도 | ✅ 완료 | `app/storage.py`, `pipeline.retry_failed` |
| 보낸 사람 포함/제외 필터 | ✅ 완료 | `pipeline._allowed` |
| Windows 토스트 알림 (긴급·중요만) | ✅ 완료 (win11toast) | `app/notifier.py` |
| 일정 .ics 내보내기 | ✅ 완료 | `app/extensions/ics_export.py` |
| 테스트 GUI (tkinter) | ✅ 완료 | `gui.py` |
| 핵심 단위 테스트 (API 키 불필요) | ✅ 통과 | `tests/test_core.py` |
| ChatGPT 구독 연결 (Sign in with ChatGPT, PKCE·루프백·토큰 자동 갱신·Responses 스트리밍) | ⚠ 공식 문서대로 구현, 가짜 서버 테스트 통과, **실제 계정 미검증** | `app/llm/chatgpt_auth.py`, `chatgpt_plan_provider.py` |
| Claude 구독 연결 (`claude -p --output-format json`) | ⚠ 가짜 claude 로 테스트 통과, **실제 미검증** | `app/llm/claude_cli_provider.py` |
| 용도별 모델 슬롯 (분석/브리핑/대체), 사고 수준, AI 생략 규칙, 모델별 토큰 사용량 기록 | ✅ 완료 | `config.py`, `llm/factory.py`, `pipeline.build_pipeline`, `storage.usage` |
| 모델 설정 화면 (OpenClaw 설정 화면 참고) | ✅ 코드 완료, 화면 렌더링은 Windows 에서 확인 필요 | `gui.py SettingsDialog` |
| 테스트 53개 (API 키·네트워크·Windows 불필요) | ✅ 통과 (v0.6 기준) | `tests/` |
| **소통메신저 수집기 (eztalk.exe 창 읽기)** | ✅ Project Monday `messenger_watch.py` 에서 이식 (원본은 실제 메신저로 검증됨). 이 프로젝트 안에서는 **Windows 실행 미확인** | `collectors/eztalk_parse.py`, `eztalk_collector.py` |
| 원문·요약 보관: 이 PC / 구글 드라이브 따로, txt·md, 드라이브용 개인정보 가리기, 실패 시 대기 폴더 | ✅ 완료 (테스트) | `extensions/archive.py` |
| **바탕화면 캘린더** (월간 보기, 입력·수정·삭제, AI 제안→확정/무시, 원문 쪽지 보기, 공휴일, 이동·크기 조절·기억, 두 모니터, 트레이, 자동 실행) | ✅ 화면·데이터는 브라우저로 끝까지 확인. **Windows 위젯 창(widget.py)은 실행 미확인** | `app/desktop_calendar/` |
| 네이버 메일 수집 (IMAP EXAMINE + BODY.PEEK, UIDVALIDITY/UID 커서, 시작일 이후, 50통·2MB 제한, 틀린 비번이면 멈춤) | ✅ 가짜 IMAP 서버로 테스트. **실제 네이버 계정 미확인** | `collectors/naver_mail.py`, `mail_content.py` |
| 쪽지·메일 공통 AI 분석 (학교 업무 관련성, 원문 인용 근거 확인, 글 속 지시 무시, JSON 스키마 강제) | ✅ | `analyzer.py` (SCHEMA, quote_found) |
| 알림 규칙 (범위·긴급만·출처·소리·방해금지·미리보기 숨김·처리 완료 제외) | ✅ | `notify_policy.py` |
| 한 프로그램 실행기 (쪽지·메일 스레드 → 큐 → AI 한 줄 처리, 10분마다 실패분 재시도) | ✅ | `runner.py` |
| 캘린더 옆 '받은 소식' 탭 (중요도순, 거르기, 상세·원문·근거, 처리 완료, 상태 표시, 지금 확인) | ✅ 브라우저로 끝까지 확인 | `desktop_calendar/` |
| 설정 화면 메일·알림·학교 정보 탭 | ✅ 코드 완료, **tkinter 화면은 Windows 에서 확인 필요** | `gui.py` |
| 옛 DB 자동 업그레이드 (칸 추가) | ✅ | `storage._migrate` |
| Windows 알림 센터 수집기 | 예비용 (소통메신저는 알림 센터 대신 자체 창을 띄우므로 기본은 eztalk) | `collectors/windows_notification.py` |

## 2. 다음 할 일 (우선순위 순)

### 2-0. 구독 연결 실제 검증 ← 메신저 없이도 바로 가능
- `python main.py chatgpt-login` → `chatgpt-models` → `python main.py --provider chatgpt_plan test`
  - 확인할 것: 첫 로그인 콜백에 `client_id`(oaiapp_...)가 오는지, ID 토큰 검증
    (`OIDC_DISCOVERY_URL` 응답 여부 — 실패 시 대체 jwks 주소 사용 중, 실제 값 확인 필요), 토큰 갱신
  - 공식 문서: https://developers.openai.com/siwc/token-sharing-open-source (sign-in, models-and-inference,
    preview-limitations, profiles-and-sessions, errors-and-recovery)
  - 이 경로 제약: `/v1/responses` 만, `store:false`, `stream:true`, system 메시지 대신 `instructions`,
    `temperature`·`max_output_tokens` 금지. 한도 초과 시 `subscription_sharing_usage_limit_exceeded`.
  - UI 가이드(https://developers.openai.com/siwc/ui-ux-guidelines)에 맞춰 "Using ChatGPT plan" 표시와
    사용량 관리 링크를 화면에 넣을 것.
  - 대상은 "오픈소스·로컬 실행 앱". 소스를 GitHub 공개 저장소로 올려 두는 것을 권장.
- Claude: `claude` 로그인 후 `python main.py --provider claude_cli test`
  - Windows 에서 `claude` 가 `claude.cmd`/`claude.exe` 로 잡히는지, `--system-prompt`·`--max-turns` 옵션이
    현재 버전에서 그대로 동작하는지 확인. 환경변수 ANTHROPIC_API_KEY 는 자동 제거함(구독 과금 유지).
  - 구독 로그인 토큰을 꺼내 직접 API 를 호출하는 방식은 Anthropic 약관 위반 → 절대 구현 금지.

### 2-0b. 모델 설정 확인
- ChatGPT 구독 경로에서 `reasoning: {"effort": ...}` 가 허용되는지 확인 (preview-limitations 의 금지 목록에는 없음).
  거부되면 `chatgpt_plan_provider.py` 에서 해당 필드를 빼고 화면 문구 수정.
- Claude 경로(API 키·claude_cli)에는 사고 수준이 아직 적용되지 않음 (필요 시 추가).
- 분석 결과 저장 시 `analyses.provider/model` 에 **실제로 답한 모델**(대체 모델 포함)이 기록됨.

### 2-1. 메신저 수집기 실사용 확인 (v0.4 에서 방식 확정)
- 방식: Project Monday 와 동일. `eztalk.exe` 의 `TFrmAlarm`(알림)·`TFrmViewNote`(보기) 창을
  EnumWindows 로 찾고 `uiautomation` 으로 Text/Custom/Edit 컨트롤 글자를 깊이 우선으로 읽음.
- 확인 순서: `python main.py messenger-check --raw` (쪽지 창 열어 둔 상태) → `python main.py watch`
- 원본 대비 바뀐 점: (1) Collector 인터페이스(collect_once 한 바퀴 = Monday `_loop` 한 바퀴 + Batch),
  (2) 본문 줄별 조각에 '보안' 단어가 있으면 보안 쪽지로 잘못 판단하던 문제 수정 (`parse_view`),
  (3) 이미 처리한 쪽지 기록은 messenger_seen.json 대신 SQLite (source='eztalk', external_id=key) 로.
- 처음 보는 메신저 창 종류는 `data/messenger_unknown.log` 에 이름만 기록됨 (내용 없음).

#### (참고) 이전 계획: 수집 방식 확정 ← 가장 먼저
메신저를 설치한 뒤 아래를 확인:
1. `python main.py list-apps` 에 메신저 알림이 보이는가?
   - **보인다** → 알림의 `texts` 배열 구조(제목=보낸 사람? 본문 잘림 여부?)를 확인해
     `WindowsNotificationCollector.collect_once` 의 sender/body 분리 로직 수정.
   - **안 보인다** (자체 팝업) → `app/collectors/uia_collector.py` 신규 작성:
     `pywinauto` (backend="uia") 로 메신저 창/팝업의 텍스트 컨트롤을 읽는 `Collector` 구현.
     Inspect.exe / `pywinauto` 의 `print_control_identifiers()` 로 컨트롤 구조 파악.
   - 둘 다 어렵다 → 최후 수단으로 메신저 창 영역 캡처 + OCR(Windows.Media.Ocr, 한국어) 수집기.
2. 알림에는 본문 앞부분만 나오는 경우가 많음 → 필요하면 알림 수집 + UIA로 전체 본문 보강.
3. 새 수집기도 `Collector` 를 상속하고 `Message(source=..., external_id=...)` 를 넘기면
   나머지 파이프라인은 수정 없이 동작함.

### 2-1c. 메일 실제 확인 (v0.6)
- `8_메일연결.bat` → `python main.py mail-check` 로 실제 네이버 계정에서: 로그인, EXAMINE, SEARCH SINCE, BODY.PEEK 동작,
  한글 제목/본문(EUC-KR·UTF-8) 디코딩, 첫 연결 시 오늘 메일만 오는지 확인.
- keyring 이 Windows 자격 증명 관리자에 저장되는지 (`제어판 > 자격 증명 관리자 > Windows 자격 증명 > cb-messenger-ai:naver-mail`).
- ChatGPT 구독 경로에서 `text.format: json_schema(strict)` 가 받아들여지는지 (MailDesk 는 같은 방식 사용). 400 이면 자동으로 형식 없이 다시 요청함.
- 출처: MailDesk 코드는 참고만 했고 그대로 복사하지 않음 (exe 에서 구조·프롬프트·설정값만 확인).

### 2-1b. 바탕화면 캘린더 Windows 확인 (v0.5)
- 방식: pywebview(WebView2) 테두리 없는 창 + Win32 로 HWND_BOTTOM 고정, WS_EX_TOOLWINDOW(작업 표시줄 숨김).
  진짜 배경 속(WorkerW)에 넣으면 마우스·키보드 입력이 안 들어가서 일부러 이 방식을 씀.
- 확인할 것: (1) `transparent=True` 가 이 pywebview 버전에서 Windows 에서 먹는지 (안 되면 배경이 불투명할 뿐 동작은 정상),
  (2) 배율 125%/150% 모니터에서 끌기·크기 조절 좌표 (JS 화면 좌표 × devicePixelRatio 로 실제 픽셀 변환),
  (3) Win+D 뒤 다시 나타나는지 (`Host._keep_on_desktop`, 1초 주기), (4) 두 번째 모니터로 옮기기,
  (5) `api.win(action, ...args)` 가변 인자가 pywebview js_api 로 제대로 넘어오는지.
- 화면만 고칠 때: `calendar.html` 을 브라우저로 열면 MockAPI(예시 데이터)로 동작.
- Lively Wallpaper(GPL-3) 로 진짜 배경에 띄우려면: calendar.html 을 로컬 HTTP 서버로 서빙하고
  window.pywebview.api 대신 fetch 로 CalendarAPI 를 부르는 얇은 어댑터를 추가하면 됨 (키보드 입력은 Lively 에서 확인 필요).
- 공휴일: `holidays_kr.json` 에 해마다 추가 (2026·2027 은 월력요항 기준으로 넣어 둠).

### 2-2. 상시 실행 프로그램으로 만들기
- 시스템 트레이 아이콘 (`pystray`): 감시 시작/중지, 오늘 브리핑, 설정 열기, 종료
- 감시는 백그라운드 스레드에서 `Collector.run_forever` 실행
- Windows 시작 시 자동 실행 (시작프로그램 폴더 바로가기 또는 작업 스케줄러)
- 설정 화면 GUI (API 키, 제공자, 필터, 가릴 이름) → `.env` 저장
- 정해진 시각(예: 08:30, 15:30) 자동 브리핑 알림

### 2-3. 설치 파일(.exe) 만들기 — v0.6.1 에서 1차 완료
- `packaging\build.bat`: PyInstaller(`cbm.spec`, onedir, CBMessengerAI.exe 창 없음 + cbm.exe 콘솔) → Inno Setup(`installer.iss`, 사용자 권한 설치)
- 설치판의 사용자 자료는 `app/paths.py` 가 `%LOCALAPPDATA%\cb-messenger-ai\` 로 보냄 (첫 실행 때 .env.example 복사)
- 남은 것: 코드 서명(SmartScreen 경고), 다른 PC 설치 확인
- **자동 업데이트 (v0.7.0)**: `app/updater.py` (GitHub Releases latest → `CBMessengerAI_Setup_*.exe` 자산, SHA-256 = 자산 `digest` 또는 `.sha256` 자산, 없으면 거부) → `CalendarAPI.update_check/update_install` → ⚙ 메뉴. 설치는 `/SILENT /CLOSEAPPLICATIONS` 로 띄우고 앱은 `Host.quit()` 로 종료, 설치가 끝나면 `installer.iss` 의 `Check: WizardSilent` 줄이 캘린더를 다시 켬. 배포: `packaging\release.bat`. 저장소는 2026-11-09 공개로 전환함.
  - 학교망(Somansa 보안 장비)이 GitHub HTTPS 를 중간 검사 → certifi 로는 연결 거부. updater 는 `truststore`(Windows 인증서 목록) 사용 (v0.7.1). ChatGPT·OpenAI 주소는 검사 대상이 아니었음 (2026-11-09 확인)
  - 미확인: 실제 Release 로 설치판 → 새 버전 업데이트 한 바퀴 (Windows 에서 확인 필요)
- API 키는 `.env` 대신 Windows 자격 증명 관리자(`keyring`)에 저장 권장

### 2-4. 캘린더 자동 등록 (확장)
- 현재: 바탕화면 캘린더(로컬 DB) + `.ics` 내보내기. 다음 단계로 확정된 일정을 외부 캘린더로 동기화:
  - `google_calendar.py` (Google Calendar API, OAuth 데스크톱 앱)
  - `outlook_calendar.py` (Microsoft Graph)
- 자동 등록 전 **사용자 확인 단계** 필수 (AI가 날짜를 잘못 계산할 수 있음)
- 이미 등록한 일정은 DB에 기록해 중복 등록 방지 (analyses 테이블에 `calendar_synced` 컬럼 추가 등)

### 2-5. 분석 기능 확장
- 주간/월간 통계 (보낸 사람별·분류별 건수, 마감 지난 할 일)
- 할 일 완료 체크 (todos 테이블)
- 여러 메시지를 모아 한 번에 분석해 API 비용 절감 (batch)

### 2-6. 대체 모델(Claude) 점검 — 2026-11-09
- 학교 PC: GPT 한도 도달 시 Claude 로 안 넘어갔음. 원인: `claude` 명령 없음(데스크톱 앱 안의 claude.exe 는 로그인 안 됨·버전별 경로라 쓰지 않음). 대체 모델 준비 실패가 로그 경고로만 남고 화면엔 안 보였음.
- 수정: FallbackProvider 가 대체 모델을 나중에 다시 준비(설치 후 재시작 불필요), 캘린더 상태줄에 `ai_fallback_error` 경고, 분석 실패 이유 표시, `find_claude()` 가 `~/.local/bin/claude.exe`·npm 위치도 찾음, claude 실행 시 창 숨김(CREATE_NO_WINDOW), '로그인 안 됨' 안내 문구.
- 남은 것: 사용자가 Claude Code 설치(`irm https://claude.ai/install.ps1 | iex`)·로그인 후 실제로 넘어가는지 확인.

### 2-7. 설정 화면 새로 만들기 · Claude 로그인 버튼 · 밝은 테마 투명도 — 2026-11-10
- **설정 화면**: tkinter 대화상자 대신 `app/desktop_calendar/settings.html`(캘린더와 같은 CSS 변수·버튼 모양, MockAPI 내장) + `settings_api.py`(SettingsAPI) + `settings_window.py`(pywebview 창).
  - 캘린더 ⚙ → 설정 열기: 같은 프로그램 안에서 창을 하나 더 띄움(`webview.create_window` 를 시작 뒤 다른 스레드에서 불러도 됨 — 이 PC 에서 확인). 저장하면 `restart_watch()` 로 바로 적용. 이미 열려 있으면 그 창을 앞으로.
  - 따로 열기: `python main.py settings` / 시작 메뉴 "설정" (이때는 저장 뒤 '감시 다시 시작' 필요). 옛 tkinter 화면은 `gui`(테스트 화면)로 남김.
  - 창 제목 줄은 DWM 속성(20)으로 테마에 맞춤. 배율 125% 모니터에서 900x760 논리 크기로 정상 표시 확인.
  - .env 에 쓰는 값은 모두 한 줄로 정리(줄바꿈으로 다른 설정 줄을 끼워 넣지 못하게), 모델 spec 은 제공자 목록·글자 종류 검사.
  - 화면 파일은 Codex(gpt-5.6-sol)가 `settings_spec.tmp.md` 지시서대로 작성 → Claude 가 검토·수정(로그인 뒤 안내 문구 지우기, 실제 창에서 가짜 값 안 보이게, 여백·스크롤바).
    Codex 플러그인에 긴 한국어 지시문을 직접 넘기면 따옴표 때문에 셸 오류가 남 → **지시문을 파일로 쓰고 "그 파일을 읽어라"고 짧게 호출**할 것.
- **Claude 로그인**: [AI 연결] 탭. `claude auth status`(JSON: loggedIn, email, subscriptionType)로 상태, `claude auth login` 을 새 콘솔로 띄워 로그인, 미설치면 공식 설치 명령을 보이는 PowerShell 창으로 실행. 로그인 파일은 읽지 않음.
- **밝은 테마 투명도**: `applyOpacity` 가 `--bg` 를 html 에 넣었는데 `body.light` 가 `--bg` 를 다시 정해 가려졌음 → body 에 넣도록 수정. 밝은 테마의 메뉴·대화상자는 불투명 배경 고정.
- Codex 교차 검토(gpt-6-astra) 지적 4건 반영: 모델 목록 동시 조회 잠금, 저장 뒤 감시 재시작 실패 이유를 화면에 표시(`restart_error`), 저장 기준은 '보낸 값', 폴더 경로의 연속 공백 보존.
- Codex 플러그인 팁: `review --model <모델>` 로 검토 모델을 고를 수 있음(도움말엔 없음) — 학교 PC 는 `--model gpt-6-astra`. `task --prompt-file <파일>` 로 긴 지시문을 파일로 넘길 수 있음.
  학교 PC 의 `~/.codex/config.toml` 기본 모델이 `gpt-6.1-sol` 이면 모델을 안 붙인 호출이 전부 실패함(계정 미지원).
- 남은 것: 설치판(v0.8.0)으로 빌드해 settings.html 이 포함되는지·시작 메뉴 "설정"이 새 화면을 여는지 확인, 실제 Claude 로그아웃 상태에서 [Claude 로그인] 버튼 한 바퀴.

### 2-8. 예전 쪽지를 다시 열면 새 쪽지로 또 분석되던 문제 — 2026-11-10
- 원인: 쪽지 중복 판단이 (1) 수집기 Batch 의 30분 기억, (2) DB 의 external_id(보낸 사람+본문 앞 40자 해시)뿐이었음. 다시 열면 본문 줄바꿈·잘림 차이로 해시가 달라지거나, 감시를 켜기 전에 받은 쪽지라 DB 에 없어서 새 쪽지로 처리됨.
- 고침 1 (`Pipeline._seen_before` + `Storage.find_similar_message`): 쪽지는 저장 전에 같은 보낸 사람의 저장된 쪽지와 본문을 비교(공백·[제목] 줄·끝의 '…' 무시, 한쪽이 다른 쪽의 앞부분이면 같음). 같으면 저장·분석 안 함. 더 긴 본문이면 본문만 보충. 15자 미만 짧은 말("확인했습니다")은 진짜로 또 올 수 있어 비교하지 않음.
- 고침 2 (`eztalk_parse.sent_time` / `is_reopened_old`): **보기 창**에 적힌 보낸 시각이 감시를 켠 때보다 10분 넘게 이르면 다시 열어 본 옛 쪽지로 보고 건너뜀. 알림 창은 언제나 새 쪽지. 시각을 못 읽으면 새 쪽지로 처리.
- 사용자 PC 확인 필요: 실제 보기 창의 보낸 시각 글자 형식(`messenger-check --raw` 로 볼 수 있음)이 `sent_time` 이 읽는 형식인지. 보기 창의 첫 시각 모양 글자를 보낸 시각으로 쓰므로, 머리글보다 본문이 먼저 읽히는 창이면 오판 가능.

### 2-9. 다른 캘린더 파일에서 일정 가져오기 — 2026-11-10
- `app/extensions/calendar_import.py`: `parse_file(path, mapping)` → {kind: ics|table|weekly|text, events, warnings, columns, mapping}. 엑셀은 openpyxl 없이 zip 안 XML 을 직접 읽음(설치판 부품 추가 없음). 옛 .xls 는 안내만.
  - 표: 머리글 글자(날짜·행사명·시간·장소·비고 등)로 칸을 짐작, 머리글이 없으면 날짜가 가장 많은 칸·가장 긴 글 칸. 화면에서 칸을 바꾸면 mapping 으로 다시 읽음.
  - 주간 달력: 어제(11-09) 일회용 `import_calendar.py`(git 제외)의 규칙을 옮김. 시각에 오전·오후가 없고 1~6시면 오후로 봄(학교 일정).
  - 기간 일정('11.20~11.22', ics 종일 여러 날)은 하루씩 나눔(31일 넘으면 첫날만 + 메모). ics 반복 일정은 첫 번째만 + 경고.
- 저장: `calendar_events.import_batch` 칸 추가(_NEW_COLUMNS), origin='import', status='confirmed'. 같은 날짜·제목·시작 시각이 있으면 건너뜀. 한 묶음은 한 트랜잭션.
- API(CalendarAPI): `import_pick`(파일 고르기) → `import_preview(path, mapping)`(token·dup 표시) → `import_apply(token, indexes)` → `import_undo(batch)`. 마지막 묶음은 prefs.last_import.
- 화면(calendar.html): Codex 에 맡겼으나 ChatGPT 사용 한도로 중단(2026-11-10 12:41 까지) → Claude 가 직접 작성. 미리보기·480x360 창·실제 pywebview 창에서 가져오기→넣기→되돌리기 확인.
  **Codex 교차 검토는 아직 안 받음** — 한도가 풀리면 `review --scope working-tree --model gpt-6-astra`(학교 PC) 또는 커밋 범위로 받을 것.
- 남은 것: 실제 학교 엑셀·구글 캘린더 .ics 로 한 번 넣어 보기.

### 2-10. AI '자세히' 정리 · 다시 분석 · 일정↔원문 — 2026-11-10
- 사용자: '요약이 너무 간략하다'. summary(1~2문장)는 목록·알림용으로 그대로 두고 **details**(문자열 배열 3~6줄) 를 분석 형식에 추가.
  - 함께 바꾼 곳: `analyzer.SYSTEM_PROMPT`([자세히 details] 절)·`SCHEMA`(required 포함)·`_normalize`, `models.Analysis.details`, `storage`(analyses.details 칸 + _NEW_COLUMNS, add_analysis, get_message), `api.item`, `calendar.html`.
  - 지어낸 숫자 거르기: `clean_details()` — 원문이나 AI 가 이미 바꿔 적은 마감·일정 날짜에 없는 숫자가 든 줄은 뺌(인용 확인처럼 엄격하지 않은 가벼운 안전장치).
  - 실제 AI(claude_cli:opus, 가상 쪽지)로 확인: 요청·기간·장소·제출·양식·문의 6줄이 원문대로 나옴.
- 받은 소식 상세 화면: '자세히' 목록, [다시 분석](`CalendarAPI.reanalyze` → `Pipeline.reanalyze`: 새 결과가 나온 뒤에만 예전 분석을 지움, 알림 없음, 같은 일정은 다시 제안 안 함. 감시와 다른 DB 연결로 실행), 분석 모델 표시, 캘린더 일정 누르면 그 달로 가서 일정 창 열기.
- 일정 창: AI 가 만든 일정이면 원래 쪽지 위에 [원문 자세히 보기 ›] → 받은 소식 상세 화면.
- 예전 분석에는 details 가 없음 → 화면에 '[다시 분석]으로 만들 수 있다' 안내. 한꺼번에 다시 분석하는 기능은 만들지 않음(AI 사용량).

### 2-11. 내가 보낸 쪽지 빼기 — 2026-11-10
- 사용자가 보낸 쪽지(보낸 뒤·보낸 쪽지함에서 연 보기 창)도 보낸 사람이 본인으로 읽혀 새 쪽지로 분석됨.
- `MY_NAMES`(설정 화면 학교 정보 탭 '메신저에 표시되는 내 이름', 쉼표 여러 개) + `Pipeline.is_from_me`: 쪽지(eztalk)만, 공백·끝의 '선생님/님' 을 뺀 이름이 **정확히** 같을 때만 저장·분석·알림 안 함. EXCLUDE_SENDERS(일부 일치)와 별개.
- 창의 '보낸 쪽지' 표시로 거르는 방법은 실제 창 글자를 확인하지 못해 넣지 않음 (받은 쪽지를 잘못 거를 위험). 필요하면 보낸 쪽지를 연 채로 `messenger-check --raw` 로 확인 뒤 추가.

### 2-12. 메모 기능 (브랜치 feature/memo) — 2026-11-10
- 동료 제안: 캡처를 캘린더에 붙여 넣으면 AI 가 정리한 메모가 되고, 그 메모를 일정으로. 사용자 결정: 캘린더 옆 '메모' 탭 / 읽는 방식 선택(이 PC 글자 인식 = 기본·권장, 캡처를 AI 에 그대로) / 종류는 AI 가 고르고 사람이 수정 / 캡처 이미지 보관 / 되돌리기 대비.
- 되돌리기 대비: 브랜치에서 작업, 새 표(memos)·새 칸(calendar_events.memo_id)·새 폴더(data/메모)만 추가, 설정 MEMO_ENABLED 로 끄면 탭이 사라짐, v0.8.3 태그·설치 파일 그대로.
- 확인된 사실 (2026-11-10, 학교 PC): Windows.Media.Ocr 한국어 인식기가 기본으로 있음(winrt 패키지는 win11toast 와 함께 이미 설치). ChatGPT 구독(/v1/responses 의 input_image)·Claude 구독(claude -p --input-format stream-json 의 image 블록) 모두 이미지 입력 동작.
- 구현: `app/ocr.py`, `app/memo.py`(MEMO_PROMPT·MEMO_SCHEMA·organize), `LLMProvider.complete_json_image`(chatgpt_plan·claude_cli·Fallback), storage memos CRUD, CalendarAPI memo_* (계약은 memo_spec_common.tmp.md — git 제외), calendar.html 메모 탭, 설정 화면 메모 켜기·기본 읽는 방식.
  GPT(Codex gpt-5.6-sol) 두 개가 뒷단·화면을 동시에 작성하다 ChatGPT 사용 한도(18:44 까지)로 마무리 직전 멈춤 → Claude 가 검토·테스트·실제 창 확인 후 다듬음. 두 작업을 같은 순간에 시작하면 Codex 플러그인 브로커가 충돌함 → 몇 초 띄워서 시작할 것.
- `clean_details`: '오후 3시'→'15:00' 같은 24시간 표기와 ':00'·':30' 분을 지어낸 숫자로 보지 않게 고침.
- 남은 것: GPT 교차 검토(한도 풀린 뒤), 설치판으로 시험(시험판), 사용자 확인 후 main 에 합치기.

## 3. 지켜야 할 설계 규칙

0. **모델 선택은 항상 `create_provider(settings, spec)` / `build_pipeline(settings)` 경유**. spec 형식 `제공자[:모델]`.
1. **AI 제공자 중립**: 분석 로직은 `LLMProvider.complete / complete_json` 만 사용. 특정 SDK 코드를
   `analyzer.py` 에 넣지 말 것. 새 제공자는 `app/llm/` 에 클래스 추가 + `factory.py` 등록.
2. **AI 응답 형식**: `analyzer.SYSTEM_PROMPT` 의 JSON 스키마. 바꾸면 `_normalize` 와
   `models.Analysis` 도 함께 수정하고 테스트 갱신.
3. **개인정보**: AI로 보내는 텍스트는 반드시 `Pipeline.text_for_ai()` 를 거칠 것.
   DB에는 원문 저장, AI에는 가린 텍스트 전송.
4. **수집기 분리**: 수집 방식이 바뀌어도 `pipeline.py` 이하는 그대로 유지.
5. 테스트는 API 키 없이 돌아가야 함 (`StubProvider` / `FakeProvider` / httpx.MockTransport 사용).
6. 메신저 수집기는 **읽기만** 할 것 (클릭·입력·전송 금지). 보안 쪽지는 내용을 저장·전송하지 않음.
7. 구독 자격 증명(토큰)은 로그에 남기거나 커밋하지 말 것. `%LOCALAPPDATA%\cb-messenger-ai\` 에만 저장.

## 4. 모델 이름

- 구독(chatgpt_plan): `CHATGPT_PLAN_MODEL` 비우면 `/v1/models` 의 첫 모델(visibility=list) 자동 사용.
- 구독(claude_cli): `CLAUDE_CLI_MODEL` 비우면 Claude Code 기본 모델.

`.env` 의 `OPENAI_MODEL`, `CLAUDE_MODEL` 로 바꿀 수 있음. 기본값(`gpt-5-mini`, `claude-haiku-5-5`)은
요약용으로 저렴한 모델을 고른 것이며, 사용 시점에 각 사의 최신 모델 목록을 확인해 바꿀 것.
OpenAI 최신 모델 중 일부는 `temperature` 를 지원하지 않으므로 넣지 않았음.

## 5. 확인 필요 사항 (사용자에게 물어볼 것)

- 메신저를 쓰는 PC가 인터넷(외부 AI API)에 접속 가능한지 (학교 업무망 망분리 여부)
- 학교/교육청 정보보안 담당자의 외부 AI 사용 허용 여부
- 사용 캘린더 (구글 / 아웃룩 / 네이버 등)

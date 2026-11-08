# 충북소통메신저 AI 비서 (v0.6 – 캘린더 + 쪽지 + 네이버 메일)

충북소통메신저로 받은 메시지를 **중요도(긴급/중요/참고)**로 분류하고, AI가 **요약 · 할 일 · 마감 · 일정**을 뽑아 주는 Windows 11용 프로그램의 핵심 소스코드입니다.
**GPT와 Claude 둘 다** 지원하며, **API 키 없이 ChatGPT·Claude 구독으로도** 쓸 수 있습니다.

| 제공자 | 쓰는 것 | 추가 비용 |
|---|---|---|
| `chatgpt_plan` (기본) | ChatGPT Plus/Pro 구독 — OpenAI 공식 *Sign in with ChatGPT* | 없음 (구독 사용량에서 차감) |
| `claude_cli` | Claude Pro/Max 구독 — 공식 Claude Code(`claude -p`) 실행 | 없음 (구독 사용량에서 차감) |
| `openai` | OpenAI API 키 | 사용량 결제 |
| `claude` | Anthropic API 키 | 사용량 결제 |
| `fake` | 키 없이 화면·흐름 테스트 | 없음 |

> **소통메신저 쪽지 감시 → 원문 보관(이 PC / 구글 드라이브) → AI 분석 → 알림 → 일정(.ics)** 까지 동작합니다.
> 쪽지 읽기 방식은 실제 메신저로 검증된 **Project Monday** 의 `messenger_watch.py` 를 옮겨 왔습니다.

## 모델 · 토큰 관리 (OpenClaw 방식)

테스트 화면의 **[모델 설정]** 또는 `.env` 에서 용도별로 모델을 따로 고릅니다.

| 설정 | `.env` 키 | 역할 | 권장 |
|---|---|---|---|
| 분석 모델 | `AI_ANALYZE` | 메시지마다 분류·요약 (호출 가장 많음) | 가볍고 빠른 모델 |
| 브리핑 모델 | `AI_DIGEST` | 하루 정리 (하루 몇 번) | 좋은 모델 |
| 대체 모델 | `AI_FALLBACK` | 위 모델이 한도 초과·로그인 만료로 실패하면 자동 전환 | 다른 회사 구독 (예: `claude_cli`) |
| 사고 수준 | `AI_REASONING` | default / low / medium / high (GPT 계열만) | 요약은 `low` 로 충분 |
| AI 생략 | `SKIP_AI_KEYWORDS`, `SKIP_AI_MIN_CHARS` | 급식표·인사 등은 AI 없이 '참고' 처리 | |

형식은 `제공자` 또는 `제공자:모델` 입니다. 예: `AI_ANALYZE=chatgpt_plan:gpt-5.6-luna`, `AI_FALLBACK=claude_cli:haiku`
- ChatGPT 구독 모델 이름은 `python main.py chatgpt-models` 또는 [모델 설정] → [목록 불러오기]로 확인
- 사용량 확인: 테스트 화면 **[사용량]** 또는 `python main.py usage` (모델별 호출 수·입출력 토큰). 분석 결과마다 어떤 모델이 답했는지도 표시됩니다.

## 구조

```
[메시지 수집기] → [필터] → [내 PC 저장(SQLite)] → [개인정보 가리기] → [AI: GPT 또는 Claude]
                                                                       ↓
                                       [Windows 알림]  ←  [중요도·요약·할 일·일정]  →  [.ics 내보내기]
```

```
cb-messenger-ai/
├─ main.py                      명령줄 실행 (analyze / test / digest / watch / gui ...)
├─ gui.py                       테스트 화면 (tkinter)
├─ app/
│  ├─ config.py                 .env 설정 읽기
│  ├─ models.py                 Message / Analysis / Event 데이터 구조
│  ├─ analyzer.py               ★ 프롬프트 · 중요도 분류 · 요약 · 브리핑
│  ├─ pipeline.py               수집→저장→가리기→분석→알림 흐름
│  ├─ redact.py                 개인정보 가리기 (전화번호·주민번호·이메일·학번·등록한 이름)
│  ├─ storage.py                SQLite 저장소
│  ├─ notifier.py               Windows 알림
│  ├─ notify_policy.py          ★ 알림 규칙 (범위·방해금지·미리보기)
│  ├─ runner.py                 ★ 쪽지·메일 수집 + AI 처리 줄 (한 프로그램)
│  ├─ secrets_store.py          메일 비밀번호 → Windows 자격 증명 관리자
│  ├─ llm/                      ★ AI 제공자 (교체 가능)
│  │  ├─ base.py                공통 인터페이스 + JSON 파싱
│  │  ├─ openai_provider.py     GPT
│  │  ├─ claude_provider.py     Claude (API 키)
│  │  ├─ chatgpt_auth.py        ChatGPT 구독 로그인·토큰 갱신
│  │  ├─ chatgpt_plan_provider.py ChatGPT 구독으로 호출
│  │  ├─ claude_cli_provider.py Claude 구독으로 호출 (claude -p)
│  │  ├─ fake_provider.py       키 없이 테스트용
│  │  └─ factory.py
│  ├─ collectors/               ★ 메시지 수집기 (교체 가능)
│  │  ├─ base.py                공통 인터페이스
│  │  ├─ manual.py              붙여넣기 / JSON 파일
│  │  ├─ eztalk_parse.py        ★ 소통메신저 창 글자 → 보낸 사람·본문 (Project Monday 규칙)
│  │  ├─ eztalk_collector.py    ★ 소통메신저(eztalk.exe) 창 감시 수집기
│  │  ├─ naver_mail.py          ★ 네이버 메일 (IMAP 읽기 전용)
│  │  ├─ mail_content.py        메일 본문 → 글자 (HTML 안전 변환)
│  │  └─ windows_notification.py (예비) Windows 알림 센터 읽기
│  ├─ desktop_calendar/         ★ 바탕화면 캘린더
│  │  ├─ calendar.html          화면 (월간 보기·입력·AI 제안·받은 소식·크기 조절)
│  │  ├─ api.py                 화면 ↔ 데이터 연결
│  │  ├─ widget.py              Windows 위젯 창 (맨 아래 고정·모니터·트레이)
│  │  └─ holidays_kr.json       공휴일 (해마다 추가)
│  └─ extensions/
│     ├─ archive.py             ★ 원문·요약 월별 파일 보관 (이 PC / 구글 드라이브)
│     └─ ics_export.py          일정 → .ics (구글·아웃룩·네이버 캘린더 가져오기)
├─ samples/sample_messages.json 테스트용 가상 메시지
├─ tests/                       API 키·네트워크 없이 돌아가는 테스트 (53개)
├─ HANDOFF.md                   ★ 이어서 개발할 때 읽을 문서 (다음 할 일 목록)
├─ .env.example                 설정 예시
└─ 1_설치하기.bat ~ 8_메일연결.bat   더블클릭 실행 파일
```

## 설치 (Windows 11)

1. [Python 3.10 이상](https://www.python.org/downloads/) 설치 — 설치 화면에서 **"Add python.exe to PATH" 체크**
2. 이 폴더에서 `1_설치하기.bat` 더블클릭
3. 아래 셋 중 하나로 AI 연결
4. `3_샘플테스트.bat` 또는 `4_테스트화면열기.bat` 더블클릭

### A. ChatGPT 구독으로 쓰기 (추천)
1. `2_ChatGPT로그인.bat` 더블클릭 (또는 테스트 화면의 **[ChatGPT 로그인]** 버튼)
2. 브라우저에서 ChatGPT 계정으로 로그인 → 권한 허용 → "로그인 완료" 확인
3. `.env` 에서 `AI_PROVIDER=chatgpt_plan` (기본값)
- Plus 또는 Pro 플랜이 필요합니다. 사용량은 ChatGPT 플랜의 사용 한도에서 차감되며, ChatGPT 설정에서 앱별 사용량을 관리할 수 있습니다.
- 로그인 정보는 `%LOCALAPPDATA%\cb-messenger-ai\` 에만 저장됩니다. 로그아웃: `python main.py chatgpt-logout`
- OpenAI는 이 기능을 **오픈소스·로컬 실행 앱**에 열어 두었습니다. 조건을 확실히 맞추려면 이 소스코드를 GitHub 에 공개 저장소로 올려 두세요 (`.env` 와 `data` 폴더는 `.gitignore` 로 제외됨).

### B. Claude 구독으로 쓰기
1. PowerShell 에서 Claude Code 설치: `irm https://claude.ai/install.ps1 | iex` (자세한 설치 안내: https://docs.claude.com 의 Claude Code 설정 문서)
2. 명령 프롬프트에서 `claude` 한 번 실행 → Pro/Max 구독 계정으로 로그인 → `/exit`
3. `.env` 에서 `AI_PROVIDER=claude_cli`
- 정식 Claude Code 프로그램을 본인 PC에서 본인 구독으로 실행하는 방식입니다. 로그인 정보를 다른 사람과 공유하지 마세요.
- 한 건에 몇 초~십수 초 걸립니다.

### C. API 키로 쓰기 (사용량 결제)
`.env` 에 키 입력 후 `AI_PROVIDER=openai` 또는 `claude`
- Claude: https://platform.claude.com → Settings → API keys
- GPT: https://platform.openai.com → API keys

## 명령줄 사용법

```bat
.venv\Scripts\activate

python main.py chatgpt-login                        :: ChatGPT 구독 로그인
python main.py chatgpt-models                       :: 구독으로 쓸 수 있는 모델
python main.py models                               :: 현재 용도별 모델 설정
python main.py usage                                :: 이번 달 모델별 토큰 사용량
python main.py --provider fake test                 :: 키 없이 샘플로 흐름 확인
python main.py --provider claude_cli:haiku test     :: 이번 실행만 Claude 구독(haiku)으로
python main.py test                                 :: 샘플 메시지를 실제 AI로 분석
python main.py --provider openai analyze "내일까지 ○○ 제출 바랍니다" --sender 교무부장
python main.py digest                               :: 오늘 받은 메시지 브리핑
python main.py export-ics                           :: 추출된 일정 → data\events.ics
python main.py retry                                :: 네트워크 오류로 분석 못 한 메시지 재시도

:: 메신저 설치 후 (실험적)
python main.py messenger-check                      :: 열린 쪽지 창이 제대로 읽히는지 확인
python main.py watch                                :: 소통메신저 실시간 감시 + 원문 보관
python main.py archive-sync                         :: 드라이브에 못 올린 기록 옮기기
python main.py calendar --watch                     :: 바탕화면 캘린더 + 쪽지 감시 + 메일 확인
python main.py mail-login                           :: 네이버 메일 연결 (앱 비밀번호 저장)
python main.py mail-check                           :: 메일 지금 한 번 확인·분석
python main.py mail-logout                          :: 메일 연결 끊기 (저장된 비밀번호 삭제)
python main.py startup                              :: 로그인 시 자동 실행 켜기 (--off 해제)
```

`--provider` 는 명령 이름 **앞에** 씁니다.

## 한눈에

**바탕화면 캘린더 + 소통메신저 쪽지 + 네이버 메일**을 하나의 프로그램이 AI 로 처리합니다.

```
💬 쪽지 감시 ─┐                                       ┌→ 🔔 알림 (학교 업무만·방해금지·미리보기 숨김)
              ├→ 원문 보관 → AI 분석(차례로 한 건씩) ─┼→ 📅 캘린더에 일정 '제안'
✉ 메일 확인 ─┘  (이 PC/드라이브)  중요도·학교 업무·근거 └→ 📋 캘린더 옆 '받은 소식' 목록
```

`6_바탕화면캘린더.bat` 하나로 전부 켜집니다. 메일은 `8_메일연결.bat` 으로 한 번 연결해 두세요.

## 네이버 메일 (MailDesk 방식)

- **연결**: `8_메일연결.bat` 또는 설정 화면 **[메일]** 탭 → 네이버 주소 + **애플리케이션 비밀번호** → [연결 확인 후 비밀번호 저장]
  - 네이버 준비: 메일 환경설정 → POP3/IMAP 설정 → **IMAP/SMTP 사용함** 저장 → 2단계 인증 후 **애플리케이션 비밀번호** 발급
  - 비밀번호는 **Windows 자격 증명 관리자**에만 저장 (파일에 쓰지 않음). 그래서 로그인 시 자동 실행도 됩니다.
- 받은편지함을 **읽기 전용**으로만 봅니다. 읽음 표시·삭제·이동·보내기를 하지 않습니다.
- **처음 연결한 날 0시 이후** 도착한 메일부터, 1·2·5·10분 간격(기본 2분)으로 새 메일만 가져옵니다. 2MB 넘는 메일은 제목·보낸 사람만.
- 비밀번호가 틀리면 계정 잠김을 막기 위해 다시 연결할 때까지 확인을 멈춥니다.
- 학교 업무망 PC 에서는 메일 서버(imap.naver.com:993) 접속이 막혀 있을 수 있습니다.
- MailDesk 와 동시에 켜 두면 같은 메일을 두 번 분석합니다(ChatGPT 사용량 2배). 이 프로그램으로 옮겼다면 MailDesk 는 꺼 주세요.

## AI 분석 (쪽지·메일 공통)

MailDesk 의 분석 방식을 합쳤습니다.
- **중요도** 긴급/중요/참고 · **분류** 처리 필요/일정·마감/참고/광고·소식/기타 · **학교 업무 관련성** 학교 업무/일반/판단 보류
- **원문 근거 확인**: 할 일이나 '학교 업무' 판단에는 AI 가 원문 문장을 그대로 인용해야 하고, 프로그램이 원문에 정말 있는지 확인합니다.
  없으면 할 일은 비우고 학교 업무는 '판단 보류'로 낮춥니다 (AI 가 지어낸 판단 차단).
- **글 속 지시 무시**: "긴급으로 분류해라" 같은 문장은 따르지 않습니다. 메일에는 피싱·사칭 주의를 더 강하게 적용합니다.
- 설정 화면 **[학교 정보]** 탭에 소속 학교·담당 업무·참고 발신처를 적으면 학교 업무 판단이 정확해집니다.
- AI 에 보내는 것: 보낸 사람·제목·받은 시각·본문 앞 12,000자(개인정보 가림 적용)·역할·학교 정보. 첨부파일은 보내지 않습니다.

## 알림 (쪽지·메일 공통, 설정 화면 [알림] 탭)

| 항목 | 기본 | 설명 |
|---|---|---|
| 범위 | **학교 업무만** | 학교 업무만 / 긴급·중요만 / 모든 것 |
| 긴급만 | 끔 | 켜면 범위 안에서 긴급만 |
| 미리보기 | **숨김** | 끄면 "새 메일"처럼만 표시 (옆 사람이 볼 수 있는 화면 대비) |
| 소리 | 끔 | |
| 방해금지 | 22:00~08:00 | 막힌 알림은 나중에 몰아서 보내지 않음 |

알림을 꺼도 수집·분석은 계속되고, **처리 완료**한 건은 알리지 않습니다.

## 바탕화면 캘린더

AI 가 쪽지에서 찾은 일정이 **바탕화면에 붙어 있는 캘린더**에 올라갑니다. 직접 입력·수정도 됩니다.

- 실행: `6_바탕화면캘린더.bat` (캘린더 + 소통메신저 감시를 한 번에). 로그인할 때 자동으로 켜려면 `7_자동실행켜기.bat`
- **항상 모든 창의 맨 아래**에 붙어 있어 바탕화면의 일부처럼 보이고, 클릭·입력은 보통 창처럼 됩니다.
  작업 표시줄에는 나타나지 않고 **트레이 아이콘**(시계 옆)에서 보이기/숨기기/끝내기.
- 처음에는 주 모니터의 **오른쪽 2/3** 크기로 뜹니다.
  - **이동**: 맨 위 막대를 끌기 · **크기**: 가장자리나 오른쪽 아래 모서리를 끌기 → 놓으면 위치·크기를 기억
  - ⚙ 메뉴: 오른쪽/왼쪽/가운데 2/3, 화면 가득, **다른 모니터로 옮기기**, 배경 투명도, 주 시작 요일, 밝은/어두운 테마, .ics 내보내기
- **AI 제안**: 쪽지에서 찾은 일정은 **점선(제안)** 으로 먼저 올라옵니다. 눌러서 원래 쪽지를 확인하고 **[확정]/[무시]**,
  필요하면 날짜·시간을 고쳐서 확정. 오른쪽 목록에서 한 번에 확정할 수도 있습니다. 같은 일정이 다시 와도 중복으로 올리지 않습니다.
- 오른쪽 **[받은 소식]** 탭: 쪽지·메일을 중요도순으로. 눌러서 요약·할 일·원문 근거·원문 보기, **[✓ 처리 완료]**.
  거르기: 처리 안 한 것 / 학교 업무 / 전체(30일), 💬 쪽지 / ✉ 메일. 아래에 쪽지 감시·메일 확인 상태와 [지금 확인].
- ⚙ → **설정 열기** (메일·알림·학교 정보·모델) → 저장 후 **감시 다시 시작**
- 날짜 칸의 **+** 또는 **두 번 클릭**으로 새 일정, 일정을 누르면 수정·삭제. 공휴일(2026·2027, 대체공휴일·선거일 포함) 표시.
- 화면 미리보기: `app\desktop_calendar\calendar.html` 을 브라우저로 열면 예시 데이터로 볼 수 있습니다.

## 소통메신저 실시간 감시

**동작 방식** (Project Monday 와 같음): 소통메신저(`eztalk.exe`)의 창만 0.5초마다 살펴서
새 쪽지 **알림 창(TFrmAlarm)** 이나 **쪽지 보기 창(TFrmViewNote)** 이 뜨면 창 안의 글자를 UI 자동화로 **읽기만** 합니다
(클릭·입력·전송 없음). 같은 쪽지가 알림 창과 보기 창에서 두 번 읽히면 한 건으로 합치고 더 긴 본문을 씁니다.
보안 쪽지는 내용을 기록하지 않고 "메신저에서 확인 필요" 알림만 띄웁니다.

1. 소통메신저에 로그인해 둡니다. (메신저 설정에서 새 쪽지 알림 창이 뜨도록)
2. 확인: 쪽지 하나를 열어 둔 채 `python main.py messenger-check` → 보낸 사람·본문이 제대로 나오는지 확인
   (`--raw` 를 붙이면 창에서 읽은 글 전체를 화면에 보여 줌. 저장하지 않음)
3. 시작: `5_메신저감시.bat` 더블클릭, 또는 테스트 화면의 **[▶ 메신저 감시 시작]**

## 쪽지 원문·요약 보관 (이 PC / 구글 드라이브)

테스트 화면 **[모델 설정] → [저장] 탭** 또는 `.env` 에서 두 곳을 **따로** 켜고 끕니다.

| 저장 위치 | `.env` | 기본 | 내용 |
|---|---|---|---|
| 이 PC | `ARCHIVE_LOCAL`, `ARCHIVE_LOCAL_DIR` | 켜짐, `data\쪽지기록` | 원문 그대로 |
| 구글 드라이브 | `ARCHIVE_GDRIVE`, `ARCHIVE_GDRIVE_DIR` | 꺼짐, 자동 찾기 | 개인정보 가린 원문 (`ARCHIVE_GDRIVE_REDACT`) |

- 파일: `쪽지원문_2026-11.txt` (원문, 시간순 덧붙임) / `쪽지요약_2026-11.txt` (중요도·요약·할 일·마감·일정)
- 형식: `ARCHIVE_FORMAT=txt` 또는 `md`
- 구글 드라이브는 API가 아니라 PC에 설치된 **Google Drive 데스크톱**의 동기화 폴더(`G:\내 드라이브` 등)에 파일을 쓰는 방식입니다.
  폴더를 비워 두면 자동으로 찾아 그 아래 `충북메신저AI_쪽지기록` 폴더를 씁니다.
- 드라이브에 못 쓰면(동기화 꺼짐 등) 이 PC의 `드라이브_대기` 폴더에 쌓았다가, 다음 감시 시작 때나 `python main.py archive-sync` 로 옮깁니다.
- AI 분석이 실패해도 원문은 먼저 저장됩니다 (원문 저장에는 AI를 쓰지 않음).
- Project Monday 와 같이 쓰는 경우, 저장 폴더가 겹치지 않으니 둘 다 켜 두어도 됩니다.

## ⚠ 개인정보 · 보안 주의

- 메시지 내용이 OpenAI 또는 Anthropic 서버로 전송됩니다. 학생 개인정보가 포함될 수 있으므로 **학교 정보보안 담당자에게 사용 가능 여부를 먼저 확인**하세요.
- 기본으로 전화번호·주민번호·이메일·계좌·학번(○학년 ○반 ○번)을 가립니다. **학생 이름은 자동으로 못 가리므로** `.env` 의 `REDACT_NAMES` 에 등록하세요.
- 원문과 분석 결과는 내 PC의 `data/messages.db` 에만 저장됩니다.
- 학교 업무망(망분리) PC에서는 외부 AI 접속이 막혀 동작하지 않을 수 있습니다.

## 테스트

```bat
python -m pytest -q
```

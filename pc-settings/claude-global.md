## 역할 분담 (모든 프로젝트 공통)
- 너(Claude)는 총괄이다: 설계, 작업 분해, 최종 통합과 판단을 맡는다.
- 범위가 명확한 구현·테스트 작성·버그 조사는 Codex에 위임한다.
  **파일을 고치는 일(구현·버그 수정·그림 생성)은 CLI로** (2026-11-20 사용자 결정, ai-bridge COLLABORATION.md 7절):
  `codex exec -m gpt-6.2-sol -c model_reasoning_effort=max -s workspace-write -C <저장소> --add-dir <브릿지 작업 폴더> -o <스크래치패드>/codex-T번호-last.md - < 지시문파일`을
  백그라운드로 돌리고, 끝나면 `git status`·`git diff`로 허용한 파일만 바뀌었는지 확인한다
  (플러그인은 Codex가 임시 폴더에만 쓸 수 있어 저장소를 못 고친 일이 있었음).
  읽기 전용 리뷰·조사는 codex:codex-rescue 플러그인이나 `-s read-only` CLI 어느 쪽이든 된다.
- 위임할 때는 목표, 수정할 파일, 완료 기준을 구체적으로 적는다.
- Codex 결과는 그대로 쓰지 말고 직접 검토한 뒤 통합한다.
- 큰 변경 후에는 /codex:review로 교차 리뷰를 받는다.

### AI별 맡기는 일 (2026-11-19 사용자 방침)
| AI·모델 | 맡기는 일 |
|---|---|
| Claude Opus 5.5 (너) | 계획·설계, 통합, 사용자와 대화 |
| Codex gpt-6.2-sol | 기본값. 명확한 지시의 구현(가성비), **그림 생성** |
| Codex gpt-6.2-astra | 아주 어려운 구현 일부, 중요한 의견 교환, 어려운 재검증 |
| 제미나이 하이드로젠 | 명확한 지시의 빠른 구현(가성비) |
| 제미나이 네온 | 버그 리뷰, Opus 토큰이 부족할 때 계획·설계 지원 |
| 제미나이 아르곤 | 창의적 발상, 글쓰기 |
- 제미나이는 Antigravity 앱에서 사람이 보낸다(ai-bridge, 아래 절). 의뢰서에 `제미나이 모델:` 줄로 모델을 적는다.

## Codex 위임 시 모델·사고 수준 (2026-11-18 개정: 6.2 출시)
사용자 방침(2026-11-18): **항상 6.2 Sol + 사고 수준 max가 기본.** 아주 어려운 구현 일부·중요한 의견 교환·어려운 재검증은 6.2 Astra + xhigh.
- **gpt-6.2-sol (max) — 기본값, 모든 위임 작업**: 구현, 그림 생성, 코드 리뷰, 테스트 작성·실행, 회귀 버그 탐지, 버그 수정, 중요 변경 검토
  (인증·토큰, 보안·개인정보, 데이터 마이그레이션, 여러 파일 구조 변경 포함).
  파일을 고치는 일은 위 CLI 명령(`-m gpt-6.2-sol -c model_reasoning_effort=max`)으로.
  플러그인(codex:codex-rescue)으로 읽기 전용 리뷰를 맡길 때는 `--model gpt-6.2-sol`만 붙이고 **`--effort`는 붙이지 않는다** (`~/.codex/config.toml` 기본값 max로 돈다). `/codex:review`·`/codex:adversarial-review`는 모델을 고를 수 없고
  config 기본값으로 도는데, config 기본 모델이 **gpt-6.2-sol + max**(sync.ps1이 맞춤)이므로 이 명령들도 6.2 Sol max 검토다.
  결과는 엣지 케이스(음수·빈 값, 날짜 경계, 경로 인코딩, 한글 경로 등)를 따로 확인한다.
- **gpt-6.2-astra (xhigh) — 일부만**: 아주 어려운 구현 일부, 중요한 의견 교환(설계 2차 의견 등), 6.2 Sol로 두 번 이상 풀리지 않는 오류,
  원인이 불분명한 문제, Sol 결과를 한 번 더 확인해야 하는 중요 변경. CLI는 `-m gpt-6.2-astra -c model_reasoning_effort=xhigh`,
  플러그인(읽기 전용)은 `--model gpt-6.2-astra --effort xhigh` (Astra는 max가 아니라 xhigh. 플러그인은 `--effort`로 xhigh까지 받는다).
- 사고 수준: `max`/`ultra`를 `--effort`로 넘기면 플러그인이 오류를 낸다 → 넘기지 말 것. ultra는 실측에서 생각을 거의 안 해서 쓰지 않는다(2026-11-14).
  플러그인 없이 한 번만 다른 노력도로 돌릴 때는 `codex exec -c model_reasoning_effort=<값>`.
- 6.2는 **최신 Codex CLI**가 필요하다 (0.167·0.169.1은 됨, 0.154에서는 "not supported when using Codex with a ChatGPT account" 오류, 2026-11-18 확인).
  이 오류가 나면 먼저 `codex --version` 확인 → 옛 버전이면 사용자에게 묻고 `codex update` (Git Bash가 아니라 PowerShell에서).
- 그 밖의 모델(gpt-6.1-*, gpt-6-astra, gpt-5.6-*)은 사용자가 지정할 때만. 6.2를 정말 못 쓰는 계정이면 사용자에게 알리고 어떤 모델로 할지 묻는다.
- **gpt-6-luna — 사용 금지.** 사용자가 이름을 콕 집어 요청할 때만 쓴다.
- 프로젝트 폴더의 CLAUDE.md에 다른 기준이 있으면 그쪽을 우선한다.
참고: C:\Users\JWK\projects\test\codex-bench\RESULTS.md (2026-11-08, sol이 견고성·검증 점수 최고, luna는 가장 느리고 회귀 테스트 격리가 약했음)

## AI 브릿지 작업 (어느 폴더 세션이든 공통, 2026-11-15)
- 제미나이(Antigravity)·Codex에게 작업을 나눌 때는 비공개 저장소 `ai-bridge`(보통 `~/projects/ai-bridge`)를 쓴다. 프로젝트 저장소에는 협업 파일을 만들지 않는다.
- 작업 명세·보고는 `ai-bridge/projects/<프로젝트>/tasks/T번호/`. 새 작업을 만들면 `ai-bridge/CURRENT_TASK.txt`를 갱신한다.
- 제미나이에게 보낼 작업을 만든 **즉시** 백그라운드로 `python tools/claude/wait_argon.py projects/<프로젝트>/tasks/T번호`(ai-bridge에서)를 띄우고, READY면 바로 검토한다.
- 파일 이름의 `argon`(`report-argon.md`, `wait_argon.py`, `수정 담당: argon`)은 제미나이 담당 자리라는 뜻이다(아르곤 모델만이 아님, 이름은 바꾸지 않음).
- 사용자가 "브릿지 T번호 검토해 줘"라고 하면 그 폴더의 보고서를 읽고 검토한다.
- 브릿지 작업을 시작할 때만 `ai-bridge/COLLABORATION.md`(특히 1·8절)를 읽는다. 평소에는 읽지 않는다.

## 업무현황판 (구글 드라이브, 모든 PC 공통, 2026-11-20)
- 위치: 구글 드라이브 PC 앱의 `내 드라이브\업무현황\` (이 PC는 `G:`. 다르면 `Get-PSDrive`에서 설명이 Google Drive인 드라이브). 사용법은 그 폴더 README.md.
- 구조: `BOARD.md`(전체 한 줄 요약) + 업무마다 폴더 `<업무이름>\STATUS.md`(폴더 이름은 저장소·브릿지 프로젝트 이름과 같게). 메모는 그 폴더에 추가, 끝난 업무는 `_끝난업무\`로.
- **업무를 시작할 때** `BOARD.md`와 해당 업무 폴더의 `STATUS.md`를 읽고, "지난번엔 여기까지 / 못 정한 것"을 사용자에게 먼저 짧게 알린다. 해당 폴더가 없으면 만들자고 제안한다.
- **업무를 마칠 때(또는 큰 결정 뒤)** `STATUS.md`의 네 칸(지금 상태 / 정해진 것 / 논쟁점·열린 질문 / 작업 기록)과 BOARD.md의 그 줄을 고친다. 작업 기록은 `날짜 · PC: 한 일` 한 줄, 최근 것이 위로.
- 고치기 직전에 파일을 다시 읽고 필요한 부분만 고친다(다른 PC가 바꾼 내용을 덮어쓰지 않게). `(1)` 같은 사본이 있으면 합치자고 알린다.
- 학생 개인 정보·비밀번호·토큰은 적지 않는다. 드라이브 폴더가 안 보이면 사용자에게 Drive 앱 로그인을 확인해 달라고 한다.

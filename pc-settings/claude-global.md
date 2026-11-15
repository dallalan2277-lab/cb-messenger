## 역할 분담 (모든 프로젝트 공통)
- 너(Claude)는 총괄이다: 설계, 작업 분해, 최종 통합과 판단을 맡는다.
- 범위가 명확한 구현·테스트 작성·버그 조사는 codex:codex-rescue에 위임한다.
- 위임할 때는 목표, 수정할 파일, 완료 기준을 구체적으로 적는다.
- Codex 결과는 그대로 쓰지 말고 직접 검토한 뒤 통합한다.
- 큰 변경 후에는 /codex:review로 교차 리뷰를 받는다.

## Codex 위임 시 모델·사고 수준 (2026-11-09 개정: 성능 우선)
사용자 방침(2026-11-10): 평소에는 6.1 Terra를 적극적으로, 어려운 문제는 6.1 Sol. 사고 수준은 max(2026-11-14 개정: ultra는 실측에서 생각 토큰이 거의 안 쓰여 제대로 작동하지 않는다고 판단).
- **gpt-6.1-terra — 상시 작업·검증 (기본값, 적극 사용)**: 사양이 분명한 구현, 코드 리뷰, 테스트 작성·실행, 회귀 버그 탐지,
  증상이 분명한 버그 수정. 망설여지면 Terra로 먼저 맡긴다.
  codex:codex-rescue에 `--model gpt-6.1-terra`를 붙인다. 평소 리뷰도 rescue에 "읽기 전용 리뷰"로 맡긴다.
  Terra 결과는 엣지 케이스(음수·빈 값, 날짜 경계, 경로 인코딩, 한글 경로 등)를 따로 확인한다.
- **gpt-6.1-sol — 어려운 문제만 재검증**: Terra로 해결되지 않는 오류, 복잡한 원인 분석, 중요 변경 검토
  (인증·토큰, 보안·개인정보, 데이터 마이그레이션, 여러 파일에 걸친 구조 변경 등).
  rescue에 `--model gpt-6.1-sol`을 붙인다. `/codex:review`·`/codex:adversarial-review`는 모델을 고를 수 없고
  config 기본값으로 돈다. 2026-11-15부터 config 기본 모델은 **모든 PC에서 Terra**(sync.ps1이 맞춤)이므로
  이 명령들은 Terra 검토다. 중요 변경을 Sol로 검토할 때는 rescue에 `--model gpt-6.1-sol`을 붙여 "읽기 전용 리뷰"로 맡긴다.
- Terra 결과가 두 번 이상 실패하거나 원인이 불분명하면 6.1 Sol로 올린다.
- gpt-5.6-sol / gpt-5.6-terra — 사용자가 지정할 때만.
- **gpt-6-luna — 사용 금지.** 사용자가 이름을 콕 집어 요청할 때만 쓴다. "보조 리뷰", "가벼운 작업", "병렬로 여러 개" 같은 이유로 고르지 않는다.
- 사고 수준(effort): **`--effort`를 붙이지 않는다.** `~/.codex/config.toml`의 기본값이 `max`라서, 비워 두면 max로 돈다.
  codex 플러그인(codex-companion)은 `--effort`로 xhigh까지만 받으므로, `--effort xhigh`·`high` 등을 붙이면 오히려 max보다 낮아진다.
  `max`/`ultra`를 `--effort`로 넘기면 플러그인이 오류를 낸다 → 넘기지 말 것.
  플러그인 없이 한 번만 다른 노력도로 돌릴 때는 `codex exec -c model_reasoning_effort=<값>` (2026-11-14 비교: T010 검토 ultra 88초·생각 549토큰·4건, max 408초·13,850토큰·6건).
- gpt-6.1-*를 못 쓰는 계정(예: 학교 PC)에서는 6.1 Sol 대신 `gpt-6-astra`, 6.1 Terra 대신 `gpt-5.6-terra` (config 기본 모델은 `gpt-5.6-terra`, 사고 수준은 config 기본값 max).
- 프로젝트 폴더의 CLAUDE.md에 다른 기준이 있으면 그쪽을 우선한다.
참고: C:\Users\JWK\projects\test\codex-bench\RESULTS.md (2026-11-08, sol이 견고성·검증 점수 최고, luna는 가장 느리고 회귀 테스트 격리가 약했음)

## AI 브릿지 작업 (어느 폴더 세션이든 공통, 2026-11-15)
- 아르곤(Antigravity)·Codex에게 작업을 나눌 때는 비공개 저장소 `ai-bridge`(보통 `~/projects/ai-bridge`)를 쓴다. 프로젝트 저장소에는 협업 파일을 만들지 않는다.
- 작업 명세·보고는 `ai-bridge/projects/<프로젝트>/tasks/T번호/`. 새 작업을 만들면 `ai-bridge/CURRENT_TASK.txt`를 갱신한다.
- 아르곤에게 보낼 작업을 만든 **즉시** 백그라운드로 `python tools/claude/wait_argon.py projects/<프로젝트>/tasks/T번호`(ai-bridge에서)를 띄우고, READY면 바로 검토한다.
- 사용자가 "브릿지 T번호 검토해 줘"라고 하면 그 폴더의 보고서를 읽고 검토한다.
- 브릿지 작업을 시작할 때만 `ai-bridge/COLLABORATION.md`(특히 1·8절)를 읽는다. 평소에는 읽지 않는다.

# 다른 PC 에서 Claude Code · Codex 설정 맞추기

아래 두 파일은 GitHub 로 옮겨지지 않습니다. 새 PC 에서 Claude Code 를 처음 쓸 때 한 번만 해 주세요.
(마지막으로 맞춘 날: 2026-11-09)

## 1. Codex 기본값 (사고 수준 ultra)

파일: `C:\Users\<사용자이름>\.codex\config.toml`
(Codex 를 한 번 실행하면 생깁니다. 메모장으로 열어 맨 위에 아래 두 줄이 있는지 확인, 없으면 추가·수정)

```toml
model = "gpt-6.1-sol"
model_reasoning_effort = "ultra"
```

- **학교 PC** 처럼 `gpt-6.1-*` 를 못 쓰는 계정이면 첫 줄을 `model = "gpt-6-astra"` 로.
- 이 두 줄이 없으면 Claude 가 Codex 를 부를 때 사고 수준이 가장 낮은 `low` 로 돌아갑니다
  (Claude 는 일부러 사고 수준을 지정하지 않고 이 기본값을 쓰게 되어 있음).
- Codex 앱의 설정 화면에서 모델·사고 수준을 같은 값으로 골라도 됩니다.

## 2. Claude Code 전역 규칙

파일: `C:\Users\<사용자이름>\.claude\CLAUDE.md`
(없으면 새로 만들기. 있으면 아래 내용으로 바꾸거나 이어 붙이기. 저장은 UTF-8)

```markdown
## 역할 분담 (모든 프로젝트 공통)
- 너(Claude)는 총괄이다: 설계, 작업 분해, 최종 통합과 판단을 맡는다.
- 범위가 명확한 구현·테스트 작성·버그 조사는 codex:codex-rescue에 위임한다.
- 위임할 때는 목표, 수정할 파일, 완료 기준을 구체적으로 적는다.
- Codex 결과는 그대로 쓰지 말고 직접 검토한 뒤 통합한다.
- 큰 변경 후에는 /codex:review로 교차 리뷰를 받는다.

## Codex 위임 시 모델·사고 수준 (2026-11-09 개정: 성능 우선)
사용자 방침: Sol 계열만 쓴다. 평소에는 5.6 Sol, 어려운 문제는 6.1 Sol. 사고 수준은 항상 ultra.
- **gpt-5.6-sol — 상시 검증 (기본값)**: 코드 리뷰, 테스트 작성·실행, 회귀 버그 탐지. 사양이 분명한 일반 구현도 여기.
  codex:codex-rescue에 `--model gpt-5.6-sol`을 붙인다. 평소 리뷰도 rescue에 "읽기 전용 리뷰"로 맡긴다.
- **gpt-6.1-sol — 어려운 문제만 재검증**: 5.6 Sol로 해결되지 않는 오류, 복잡한 원인 분석, 중요 변경 검토
  (인증·토큰, 보안·개인정보, 데이터 마이그레이션, 여러 파일에 걸친 구조 변경 등).
  rescue에 `--model gpt-6.1-sol`을 붙인다. `/codex:review`·`/codex:adversarial-review`는 모델을 고를 수 없고
  config 기본값(gpt-6.1-sol)으로 돌기 때문에, 중요 변경을 검토할 때 쓴다.
- 5.6 Sol 결과가 두 번 이상 실패하거나 원인이 불분명하면 6.1 Sol로 올린다.
- gpt-6.1-terra / gpt-5.6-terra — 사용자가 지정할 때만.
- **gpt-6-luna — 사용 금지.** 사용자가 이름을 콕 집어 요청할 때만 쓴다. "보조 리뷰", "가벼운 작업", "병렬로 여러 개" 같은 이유로 고르지 않는다.
- 사고 수준(effort): **`--effort`를 붙이지 않는다.** `~/.codex/config.toml`의 기본값이 `ultra`라서, 비워 두면 ultra로 돈다.
  codex 플러그인(codex-companion)은 `--effort`로 xhigh까지만 받으므로, `--effort xhigh`·`high` 등을 붙이면 오히려 ultra보다 낮아진다.
  `max`/`ultra`를 `--effort`로 넘기면 플러그인이 오류를 낸다 → 넘기지 말 것.
- gpt-6.1-*를 못 쓰는 계정(예: 학교 PC)에서는 6.1 Sol 대신 `gpt-6-astra`(ultra 지원). 5.6 Sol은 그대로.
- 프로젝트 폴더의 CLAUDE.md에 다른 기준이 있으면 그쪽을 우선한다.
```

## 3. Codex 플러그인

Claude Code 에서 `codex` 플러그인(openai-codex)을 설치하고 `/codex:setup` 으로 Codex 로그인 상태를 확인하세요.

## 이 문서 고치기

위 규칙을 바꾸면 원본(`~/.claude/CLAUDE.md`, `~/.codex/config.toml`)과 이 문서를 같이 고쳐서 커밋할 것.

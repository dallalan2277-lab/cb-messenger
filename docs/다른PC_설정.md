# 다른 PC 에서 Claude Code · Codex 설정 맞추기

Claude Code 의 공통 규칙(`~/.claude/CLAUDE.md`)과 Codex 기본값(`~/.codex/config.toml`)은 PC 마다 따로 있는 파일이라
GitHub 로 직접 옮겨지지 않습니다. 그래서 **원본을 이 저장소의 `pc-settings/` 폴더에 두고**, PC 에 반영하는 방식으로 맞춥니다.

## 하는 방법

`git pull` 로 받은 뒤 **`9_PC설정맞추기.bat` 더블클릭.** 끝.

- 아무 줄도 안 나오면 이미 맞춰져 있는 상태입니다.
- 바뀐 것이 있으면 Claude 앱과 Codex 를 껐다 켜 주세요.
- Codex 를 그 PC 에서 한 번도 안 켰으면 Codex 설정은 건너뜁니다 → Codex 를 한 번 켠 뒤 다시 실행.

## 무엇이 바뀌나

| PC 의 파일 | 내용 | 원본 |
|---|---|---|
| `C:\Users\(이름)\.claude\CLAUDE.md` | Codex 위임 규칙 (5.6 Sol 상시 검증, 6.1 Sol 어려운 문제, luna 금지, `--effort` 안 붙이기) | `pc-settings/claude-global.md` |
| `C:\Users\(이름)\.codex\config.toml` | 맨 위 `model = "gpt-6.1-sol"`, `model_reasoning_effort = "ultra"` | `pc-settings/sync.ps1` |

- CLAUDE.md 는 표시 줄(`cb-messenger-ai pc-settings BEGIN` ~ `END`) 사이만 바꿉니다. 그 밖에 직접 적은 내용은 그대로 둡니다.
- config.toml 은 위 두 줄만 바꾸고 나머지 설정은 그대로 둡니다.
- **학교 PC** 처럼 `gpt-6.1-*` 를 못 쓰는 계정이면 자동으로 `gpt-6-astra` 로 넣습니다 (Codex 의 모델 목록에 6.1 Sol 이 없을 때).
- 처음 바꿀 때 원래 파일을 `.bak` 으로 한 번 남겨 둡니다.
- config.toml 에 ultra 가 없으면 Claude 가 Codex 를 부를 때 사고 수준이 가장 낮은 `low` 로 돕니다. 꼭 맞춰 두세요.

## 규칙을 바꾸고 싶을 때

Claude 에게 "Codex 규칙 바꿔줘" 라고 하면 `pc-settings/claude-global.md` 를 고치고 이 PC 에 반영한 뒤 커밋·push 합니다.
다른 PC 에서는 `git pull` 후 `9_PC설정맞추기.bat` 을 다시 실행하면 같아집니다.
(`~/.claude/CLAUDE.md` 를 직접 고치면 다음 동기화 때 원본 내용으로 덮어써집니다.)

## Codex 플러그인

Claude Code 에서 `codex` 플러그인(openai-codex)을 설치하고 `/codex:setup` 으로 Codex 로그인 상태를 확인하세요.

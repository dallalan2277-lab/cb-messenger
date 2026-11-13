---
name: pr-gemini-review
description: PR을 열면서 "Gemini 검토 요청" 섹션을 채우거나, PR에 달린 Gemini·Codex 리뷰를 읽고 반영할 때 사용. "PR 올려줘", "PR 만들어줘", "Gemini 검토 요청해줘", "Gemini 리뷰 반영해줘", "PR #N 리뷰 확인해줘" 같은 요청에 쓴다.
---

# PR + Gemini 검토 요청

이 저장소는 세 AI가 역할을 나눈다.
- **Claude Code(나):** 개발, PR 작성, 리뷰 반영
- **Codex:** PR 자동 리뷰 (봇 계정으로 코멘트가 달림)
- **Gemini(Antigravity):** 사용자가 지시할 때만 PR을 읽고 `[Gemini]` 코멘트로 답함

## 절대 하지 말 것
- `gh pr merge` 또는 main 브랜치에 직접 push. **머지는 항상 사용자가 한다.**
- PR 본문 임시 파일을 저장소 안에 만들어 커밋하는 것

---

## A. PR 열기

### 1. 브랜치와 커밋 정리
```bash
git status
git branch --show-current
```
- 기본 브랜치 이름을 확인한다: `BASE=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)` (아래 `main`은 모두 `$BASE`로 읽는다)
- 현재 브랜치가 기본 브랜치면 새 브랜치를 만들어 옮긴다. 이름은 프로젝트의 CLAUDE.md·AGENTS.md에 브랜치 규칙(예: `feature/...`)이 있으면 그것을 따르고, 없으면 `claude/<짧은-영문-설명>`.
- 커밋되지 않은 변경이 있으면 의미 단위로 커밋한다.
- `git push -u origin HEAD`

이미 이 브랜치의 PR이 있으면(`gh pr view --json number` 로 확인) 새로 만들지 말고 C단계(본문 갱신)로 간다.

### 2. 정보 수집
```bash
git fetch origin
git rev-parse HEAD                          # 기준 커밋
git merge-base origin/$BASE HEAD             # base 커밋
git diff --stat origin/$BASE...HEAD
git log --oneline origin/$BASE..HEAD
```

### 3. Gemini 검토가 필요한지 판단
아래 중 하나라도 해당하면 **필요**, 아니면 **해당 없음**.
- 설계 방향을 고른 경우(구조, 라이브러리, 데이터 모델 등)
- 인증, 권한, 결제, 개인정보, 데이터 삭제·마이그레이션을 건드린 경우
- 버그 원인을 확신하지 못한 채 고친 경우
- 내가 확신이 낮은 부분이 있는 경우
- 사용자가 Gemini 검토를 요청한 경우

오타 수정, 문서, 단순 리팩터링은 **해당 없음**으로 둔다.

### 4. 본문 작성
`.github/pull_request_template.md`를 읽어 그 틀대로 채운다. 저장소 밖 임시 파일에 쓴다.
```bash
BODY=$(mktemp)   # 여기에 본문 작성
```

채울 때 지킬 점:
- **기준 커밋**에 2단계에서 얻은 실제 해시를 넣는다.
- **배경**은 Gemini가 이 대화를 모른다는 전제로 쓴다.
- **꼭 봐야 할 코드**는 실제 경로와 함수명/줄 범위로 적는다.
- **확신이 낮은 부분**을 숨기지 말고 솔직하게 적는다.
- **질문**은 2~4개, 답이 분명하게 나오도록 구체적으로 쓴다.
  - 나쁜 예: "이 코드 검토해 주세요"
  - 좋은 예: "`saveOrder()`에서 재고 차감과 주문 저장 사이에 동시 요청이 들어오면 재고가 음수가 될 수 있는가?"
  - 좋은 예: "A안(캐시 무효화)과 B안(TTL 단축) 중 데이터 정합성 면에서 어느 쪽이 안전한가?"
- 검토가 **해당 없음**이면 Gemini 섹션은 `**검토 필요:** 해당 없음` 한 줄만 남긴다.

### 5. PR 생성
```bash
gh pr create --base "$BASE" --title "<한국어 제목>" --body-file "$BODY"
```

### 6. 사용자에게 보고
- PR 번호와 링크
- Gemini 검토가 **필요**하면, Antigravity에 보낼 문장을 그대로 복사할 수 있게 코드 블록으로 준다:
  ```
  PR #<번호> 검토해줘
  ```
- **해당 없음**이면 그 이유를 한 줄로.

---

## B. 리뷰 반영 ("Gemini 리뷰 반영해줘", "PR #N 리뷰 확인해줘")

### 1. 코멘트 읽기
```bash
gh pr view <N> --comments
gh api repos/{owner}/{repo}/pulls/<N>/comments    # 줄 단위(인라인) 리뷰 코멘트
```
- `[Gemini]`로 시작하는 코멘트 = Gemini 리뷰
- Codex 봇 계정이 단 코멘트 = Codex 리뷰
- Gemini 코멘트의 기준 커밋이 현재 PR head와 다르면, 그 이후 바뀐 부분이 있다고 사용자에게 알린다.

### 2. 사용자에게 먼저 정리해서 보여주기
바로 고치지 말고 아래 표로 정리한다.

| 출처 | 지적 내용 | 내 판단(동의/부분 동의/반대) | 조치 계획 |
|---|---|---|---|

- **Gemini와 Codex 의견이 서로 다르면** 내가 혼자 결정하지 말고, 양쪽 근거를 나란히 적고 사용자에게 선택을 묻는다.
- 반대하는 지적은 근거를 한두 줄로 적는다.

### 3. 사용자가 승인하면 반영
- 같은 브랜치에서 수정, 커밋, push
- PR에 결과 코멘트를 남긴다:
```bash
gh pr comment <N> --body "[Claude] 리뷰 반영: <반영한 것 요약> / 반영하지 않음: <항목과 이유>"
```
- 사용자에게 "머지 전 확인할 점"을 1~3줄로 알려주고 끝낸다. 머지는 하지 않는다.

---

## C. 기존 PR 본문 갱신
작업이 더 진행돼서 Gemini에게 다시 물어야 하면, A-2~A-4를 다시 해서 본문을 만들고:
```bash
gh pr edit <N> --body-file "$BODY"
```
기준 커밋을 반드시 새 해시로 바꾼다.

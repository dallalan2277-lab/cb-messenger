@echo off
chcp 65001 > nul
cd /d %~dp0
echo ===== 이 PC 의 Claude Code / Codex 설정 맞추기 =====
echo 저장소의 pc-settings 폴더 내용을 이 PC 에 반영합니다.
echo  - C:\Users\%USERNAME%\.claude\CLAUDE.md  (Codex 위임 규칙)
echo  - C:\Users\%USERNAME%\.codex\config.toml (Codex 모델, 사고 수준 ultra)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pc-settings\sync.ps1"
echo.
echo 아무 줄도 안 나왔다면 이미 맞춰져 있는 상태입니다.
echo 바뀐 것이 있으면 Claude 앱과 Codex 를 껐다 켜 주세요.
pause

@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (echo 먼저 "1_설치하기.bat" 을 실행하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
echo 쪽지 감시 + 네이버 메일 확인을 시작합니다. (메신저에 로그인되어 있어야 합니다)
echo 이 창을 닫으면 감시가 멈춥니다.
echo.
python main.py watch
pause

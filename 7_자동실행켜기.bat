@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (echo 먼저 "1_설치하기.bat" 을 실행하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
python main.py startup
echo.
echo 다음에 Windows 에 로그인하면 바탕화면 캘린더와 메신저 감시가 자동으로 켜집니다.
echo 끄려면 명령 프롬프트에서:  python main.py startup --off
pause

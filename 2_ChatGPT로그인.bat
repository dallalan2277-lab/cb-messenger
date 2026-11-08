@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (echo 먼저 "1_설치하기.bat" 을 실행하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
echo 잠시 후 브라우저가 열립니다. ChatGPT 계정으로 로그인하고 [허용]을 누르세요.
echo.
python main.py chatgpt-login
echo.
echo 로그인 완료가 보이면 "3_샘플테스트.bat" 을 더블클릭하세요.
pause

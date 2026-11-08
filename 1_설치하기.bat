@echo off
chcp 65001 > nul
cd /d %~dp0
echo [1/3] 가상환경 만드는 중...
python -m venv .venv || (echo. & echo Python 이 설치되지 않았습니다. README 의 설치 1번을 먼저 하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
echo [2/3] 필요한 프로그램 설치 중... (몇 분 걸릴 수 있습니다)
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
echo [3/3] 설정 파일 만드는 중...
if not exist .env copy .env.example .env > nul
echo.
echo ===== 설치 완료! 다음으로 "2_ChatGPT로그인.bat" 을 더블클릭하세요. =====
pause

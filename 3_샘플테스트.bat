@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (echo 먼저 "1_설치하기.bat" 을 실행하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
echo 샘플 메시지 5건을 ChatGPT 구독으로 분석합니다...
echo.
python main.py test
echo.
echo 위에 긴급/중요/참고 와 요약이 보이면 성공입니다.
echo 오류가 보이면 이 창을 캡처해서 보내주세요.
pause

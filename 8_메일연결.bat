@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (echo 먼저 "1_설치하기.bat" 을 실행하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
echo ===== 네이버 메일 연결 =====
echo 준비: 네이버 메일 환경설정 ^> POP3/IMAP 설정 ^> IMAP/SMTP 사용함 저장
echo       2단계 인증 후 '애플리케이션 비밀번호' 발급 (네이버 로그인 비밀번호가 아님)
echo.
python main.py mail-login
echo.
echo 연결됐다면 바탕화면 캘린더를 다시 켜거나, 캘린더 톱니바퀴 메뉴에서 '감시 다시 시작'을 누르세요.
pause

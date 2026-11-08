@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (echo 먼저 "1_설치하기.bat" 을 실행하세요. & pause & exit /b)
call .venv\Scripts\activate.bat
python main.py gui
if errorlevel 1 pause

@echo off
chcp 65001 > nul
rem 설치 파일 만들기: packaging\Output\CBMessengerAI_Setup_<버전>.exe
rem 필요: 1_설치하기.bat 로 만든 .venv, Inno Setup 6 (winget install JRSoftware.InnoSetup)
cd /d %~dp0
if not exist ..\.venv (echo 먼저 1_설치하기.bat 을 실행하세요. & (if not defined CBM_NOPAUSE pause) & exit /b 1)
set PY=..\.venv\Scripts\python.exe

echo [1/4] 빌드 도구 확인...
%PY% -m pip install -q "pyinstaller>=6.10" || goto :fail
for /f "delims=" %%v in ('%PY% -c "import sys; sys.path.insert(0, '..'); import app; print(app.__version__)"') do set VER=%%v
echo     버전 %VER%

echo [2/4] 아이콘 만들기...
%PY% make_icon.py app.ico || goto :fail

echo [3/4] 프로그램 묶기 (PyInstaller, 몇 분 걸림)...
%PY% -m PyInstaller --noconfirm --clean --distpath dist --workpath build cbm.spec || goto :fail

echo [4/4] 설치 파일 만들기 (Inno Setup)...
set ISCC=
rem 직접 지정: set INNO_ISCC=C:\경로\ISCC.exe  (예: NuGet 'Tools.InnoSetup' 패키지의 tools\ISCC.exe)
if defined INNO_ISCC if exist "%INNO_ISCC%" set ISCC="%INNO_ISCC%"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (echo Inno Setup 6 이 없습니다: winget install JRSoftware.InnoSetup & echo 묶은 프로그램은 dist\CBMessengerAI 에 있습니다. & (if not defined CBM_NOPAUSE pause) & exit /b 1)
%ISCC% /Q /DMyAppVersion=%VER% installer.iss || goto :fail

echo.
echo ===== 완료: packaging\Output\CBMessengerAI_Setup_%VER%.exe =====
if not defined CBM_NOPAUSE pause
exit /b 0

:fail
echo.
echo ===== 빌드 실패. 위 오류를 확인하세요. =====
if not defined CBM_NOPAUSE pause
exit /b 1

@echo off
chcp 65001 > nul
rem ===== 새 버전 배포하기 (GitHub Releases) =====
rem 1) app\__init__.py 의 __version__ 을 올리고 커밋·푸시한 뒤 이 파일을 더블클릭하세요.
rem 2) 설치 파일을 만들고, 설치 파일의 SHA-256(손상·변조 확인값) 파일을 함께 만듭니다.
rem 3) GitHub CLI(gh)가 있으면 바로 배포, 없으면 배포 페이지와 파일 폴더를 열어 줍니다.
rem    배포되면 사용자의 캘린더 ⚙ 메뉴에 '새 버전 설치하기'가 나타납니다.
cd /d %~dp0
set REPO=dallalan2277-lab/cb-messenger
set CBM_NOPAUSE=1
call build.bat || goto :fail
set CBM_NOPAUSE=
for /f "delims=" %%v in ('..\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '..'); import app; print(app.__version__)"') do set VER=%%v
set EXE=CBMessengerAI_Setup_%VER%.exe
if not exist "Output\%EXE%" (echo 설치 파일이 없습니다: Output\%EXE% & goto :fail)

echo [5/6] SHA-256 확인값 만들기...
powershell -NoProfile -Command "$h=(Get-FileHash -Algorithm SHA256 'Output\%EXE%').Hash.ToLower(); [IO.File]::WriteAllText('Output\%EXE%.sha256', $h + '  %EXE%' + [char]10)" || goto :fail
type "Output\%EXE%.sha256"

echo [6/6] GitHub 에 배포...
where gh >nul 2>nul
if errorlevel 1 goto :manual
gh release create v%VER% "Output\%EXE%" "Output\%EXE%.sha256" --repo %REPO% --title "v%VER%" --generate-notes || goto :fail
echo.
echo ===== 완료: https://github.com/%REPO%/releases/tag/v%VER% =====
pause
exit /b 0

:manual
echo.
echo GitHub CLI(gh)가 없어 직접 올려야 합니다. 열리는 배포 페이지에서:
echo   - Tag 칸: v%VER%   (그대로 두면 됨)
echo   - 함께 열린 폴더의 두 파일을 아래 상자로 끌어다 놓기:
echo       %EXE%
echo       %EXE%.sha256
echo   - [Publish release] 누르기
start "" "https://github.com/%REPO%/releases/new?tag=v%VER%&title=v%VER%"
start "" explorer "%~dp0Output"
pause
exit /b 0

:fail
echo.
echo ===== 배포 실패. 위 오류를 확인하세요. =====
pause
exit /b 1

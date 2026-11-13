@echo off
chcp 65001 > nul
rem ===== 새 버전 배포하기 (GitHub Releases) =====
rem 1) app\__init__.py 의 __version__ 을 올리고 커밋·푸시한 뒤 이 파일을 더블클릭하세요.
rem 2) 설치 파일을 만들고, 설치 파일의 SHA-256(손상·변조 확인값) 파일을 함께 만듭니다.
rem 3) GitHub CLI(gh)가 있으면 바로 배포, 없으면 배포 페이지와 파일 폴더를 열어 줍니다.
rem    정식판(예: 0.10.0)은 main 브랜치에서만 → 사용자 캘린더 ⚙ 메뉴에 '새 버전 설치하기'가 나타납니다.
rem    시험판(예: 0.10.0-beta.1, 버전에 '-' 가 있음)은 main 이 아닌 브랜치에서만 → 'pre-release' 로 올려
rem    자동 업데이트에 뜨지 않습니다 (설치 파일을 직접 받아 설치). 배포된 0.9.0 은 태그 이름을 보지 않으므로
rem    pre-release 표시를 빼먹으면 모든 사용자에게 시험판이 뜹니다 → 아래 안내대로 꼭 확인하세요.
cd /d %~dp0
set REPO=dallalan2277-lab/cb-messenger
set PY=..\.venv\Scripts\python.exe
if not exist %PY% (echo 먼저 1_설치하기.bat 을 실행하세요. & goto :fail)
for /f "delims=" %%v in ('%PY% -c "import sys; sys.path.insert(0, '..'); import app; print(app.__version__)"') do set VER=%%v
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b
set PRE=0
echo %VER%| findstr /c:"-" >nul && set PRE=1
echo 버전 %VER% / 브랜치 %BRANCH% / 시험판 %PRE%

echo [0/6] 배포 전 확인...
if "%PRE%"=="1" if /i "%BRANCH%"=="main" (echo 시험판^(%VER%^)은 main 에서 배포하지 않습니다. 시험판 브랜치로 바꾸세요. & goto :fail)
if "%PRE%"=="0" if /i not "%BRANCH%"=="main" (echo 정식판^(%VER%^)은 main 에서만 배포합니다. 지금 브랜치: %BRANCH% & goto :fail)
git status --porcelain | findstr . >nul && (echo 커밋하지 않은 변경이 있습니다. 먼저 커밋하세요. & goto :fail)
git fetch -q origin || goto :fail
git rev-parse -q --verify origin/%BRANCH% >nul || (echo GitHub 에 %BRANCH% 브랜치가 없습니다. 먼저 push 하세요. & goto :fail)
for /f %%c in ('git rev-list --count origin/%BRANCH%..HEAD') do set AHEAD=%%c
if not "%AHEAD%"=="0" (echo GitHub 에 올리지 않은 커밋이 %AHEAD%개 있습니다. 먼저 push 하세요. & goto :fail)
git ls-remote --tags origin v%VER% | findstr . >nul && (echo GitHub 에 v%VER% 태그가 이미 있습니다. 버전을 올리세요. & goto :fail)

set CBM_NOPAUSE=1
call build.bat || goto :fail
set CBM_NOPAUSE=
set EXE=CBMessengerAI_Setup_%VER%.exe
if not exist "Output\%EXE%" (echo 설치 파일이 없습니다: Output\%EXE% & goto :fail)

echo [5/6] SHA-256 확인값 만들기...
powershell -NoProfile -Command "$h=(Get-FileHash -Algorithm SHA256 'Output\%EXE%').Hash.ToLower(); [IO.File]::WriteAllText('Output\%EXE%.sha256', $h + '  %EXE%' + [char]10)" || goto :fail
type "Output\%EXE%.sha256"

echo [6/6] GitHub 에 배포...
where gh >nul 2>nul
if errorlevel 1 goto :manual
if "%PRE%"=="1" (
  gh release create v%VER% "Output\%EXE%" "Output\%EXE%.sha256" --repo %REPO% --title "v%VER% (시험판)" --generate-notes --prerelease --latest=false --target %BRANCH% || goto :fail
) else (
  gh release create v%VER% "Output\%EXE%" "Output\%EXE%.sha256" --repo %REPO% --title "v%VER%" --generate-notes --target main || goto :fail
)
echo.
echo ===== 완료: https://github.com/%REPO%/releases/tag/v%VER% =====
pause
exit /b 0

:manual
for /f "delims=" %%u in ('%PY% -c "import sys, urllib.parse as u; print(u.quote(sys.argv[1], safe=''))" %BRANCH%') do set BRANCH_URL=%%u
set "URL=https://github.com/%REPO%/releases/new?tag=v%VER%&target=%BRANCH_URL%&title=v%VER%"
if "%PRE%"=="1" set "URL=%URL%&prerelease=1"
echo.
echo GitHub CLI(gh)가 없어 직접 올려야 합니다. 열리는 배포 페이지에서:
echo   - Tag 칸: v%VER%  /  Target(브랜치): %BRANCH%   (그대로 두면 됨, 다르면 고치기)
echo   - 함께 열린 폴더의 두 파일을 아래 상자로 끌어다 놓기:
echo       %EXE%
echo       %EXE%.sha256
if "%PRE%"=="1" (
  echo   - ★ 시험판: 'Set as a pre-release' 가 체크돼 있는지, 'Set as the latest release' 는 꺼져 있는지 꼭 확인
  echo     ^(빼먹으면 0.9.0 사용자 모두에게 시험판이 업데이트로 뜹니다^)
)
echo   - [Publish release] 누르기
start "" "%URL%"
start "" explorer "%~dp0Output"
pause
exit /b 0

:fail
echo.
echo ===== 배포 실패. 위 오류를 확인하세요. =====
pause
exit /b 1

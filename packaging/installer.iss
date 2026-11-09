; Inno Setup 설치 파일 스크립트 — packaging\build.bat 이 사용합니다.
; 관리자 권한 없이 사용자 폴더에 설치 (학교 PC 처럼 권한이 막힌 곳 대비).
; 설정·쪽지 기록·DB 는 %LOCALAPPDATA%\cb-messenger-ai 에 있으므로 지우거나 다시 설치해도 남습니다.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "충북메신저 AI 비서"
#define MyAppExe "CBMessengerAI.exe"
#define MyCliExe "cbm.exe"

[Setup]
AppId={{6B1C2E4A-8F3D-4C7B-9A21-CB0E5A7D3F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=dallalan2277-lab
DefaultDirName={localappdata}\Programs\CBMessengerAI
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=CBMessengerAI_Setup_{#MyAppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 아이콘 만들기"; GroupDescription: "추가 작업:"
Name: "autostart"; Description: "Windows 에 로그인할 때 자동으로 켜기 (캘린더 + 쪽지·메일 감시)"; GroupDescription: "추가 작업:"

[Files]
Source: "dist\CBMessengerAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\충북메신저 AI 캘린더"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\설정"; Filename: "{app}\{#MyAppExe}"; Parameters: "gui"
Name: "{group}\ChatGPT 로그인"; Filename: "{cmd}"; Parameters: "/c """"{app}\{#MyCliExe}"" chatgpt-login & pause"""; IconFilename: "{app}\{#MyAppExe}"
Name: "{group}\샘플 테스트"; Filename: "{cmd}"; Parameters: "/c """"{app}\{#MyCliExe}"" test & pause"""; IconFilename: "{app}\{#MyAppExe}"
Name: "{group}\쪽지 창 읽기 확인"; Filename: "{cmd}"; Parameters: "/c """"{app}\{#MyCliExe}"" messenger-check & pause"""; IconFilename: "{app}\{#MyAppExe}"
Name: "{group}\명령 프롬프트 (cbm)"; Filename: "{cmd}"; Parameters: "/k cd /d ""{app}"" && cbm.exe --help"; IconFilename: "{app}\{#MyAppExe}"
Name: "{group}\{#MyAppName} 제거"; Filename: "{uninstallexe}"
Name: "{userdesktop}\충북메신저 AI 캘린더"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; 자동 실행은 프로그램 자신의 startup 명령으로 등록 (python 버전과 같은 방식, 하나로 관리)
Filename: "{app}\{#MyCliExe}"; Parameters: "startup"; Flags: runhidden; Tasks: autostart
Filename: "{cmd}"; Parameters: "/c """"{app}\{#MyCliExe}"" chatgpt-login & pause"""; Description: "지금 ChatGPT 로그인하기 (처음 한 번)"; Flags: postinstall waituntilterminated skipifsilent
; ↑ 로그인이 끝난 뒤에 캘린더가 켜지도록 nowait 를 쓰지 않음 (먼저 켜지면 감시가 로그인 오류로 시작 못 함)
Filename: "{app}\{#MyAppExe}"; Description: "충북메신저 AI 캘린더 실행"; Flags: postinstall nowait skipifsilent
; 캘린더의 [업데이트] 로 조용히(/SILENT) 설치한 경우: 끝나면 원래 모드 그대로 캘린더를 다시 켬
;   /RELAUNCHMODE=nowatch → 캘린더만, 없으면 → 캘린더 + 쪽지·메일 감시
Filename: "{app}\{#MyAppExe}"; Parameters: "calendar --watch"; Flags: nowait; Check: RelaunchWatch
Filename: "{app}\{#MyAppExe}"; Parameters: "calendar"; Flags: nowait; Check: RelaunchNoWatch

[Code]
function RelaunchWatch(): Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCHMODE|watch}') <> 'nowatch');
end;

function RelaunchNoWatch(): Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCHMODE|watch}') = 'nowatch');
end;

[UninstallRun]
Filename: "{app}\{#MyCliExe}"; Parameters: "startup --off"; Flags: runhidden; RunOnceId: "StartupOff"

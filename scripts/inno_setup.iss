; RCFlow Windows Installer — Inno Setup Script
;
; Compile with:  iscc inno_setup.iss /DBundleDir=<path> /DAppVersion=<ver> /DArch=<arch>
;
; Parameters (passed via /D on the command line):
;   BundleDir      — Path to the assembled bundle directory
;   AppVersion     — e.g. "0.1.0"
;   Arch           — e.g. "x64"
;   OutputDir      — Output directory for setup.exe (default: dist/)
;   OutputFilename — Base name for the installer (no .exe)

#ifndef BundleDir
  #error "BundleDir must be defined via /DBundleDir=..."
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef Arch
  #define Arch "x64"
#endif

#ifndef OutputDir
  #define OutputDir "dist"
#endif

#ifndef OutputFilename
  #define OutputFilename "rcflow-setup"
#endif

[Setup]
AppName=RCFlow
AppVersion={#AppVersion}
AppPublisher=RCFlow
AppPublisherURL=https://github.com/user/rcflow
DefaultDirName={autopf}\RCFlow
DefaultGroupName=RCFlow
OutputDir={#OutputDir}
OutputBaseFilename={#OutputFilename}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
SetupIconFile={#BundleDir}\tray_icon.ico
UninstallDisplayIcon={app}\tray_icon.ico
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=
; Allow user to choose whether to launch at startup
ChangesEnvironment=yes
; Code signing — requires SignTool to be defined when invoking ISCC.
; To sign the installer, pass /DSignFiles=1 and define a signtool:
;   iscc ... /DSignFiles=1 /SMySignTool="signtool.exe sign /f $qcert.pfx$q /p pass /tr http://timestamp.digicert.com /td sha256 /fd sha256 $f"
#ifdef SignFiles
SignTool=MySignTool
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Main executable and internal files
Source: "{#BundleDir}\rcflow.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; Tray icon
Source: "{#BundleDir}\tray_icon.ico"; DestDir: "{app}"; Flags: ignoreversion

; Tool definitions
Source: "{#BundleDir}\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Alembic
Source: "{#BundleDir}\migrations\*"; DestDir: "{app}\migrations"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#BundleDir}\alembic.ini"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

; Config file (preserved on upgrades if it already exists)
Source: "{#BundleDir}\settings.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist onlyifdoesntexist

; VERSION file
Source: "{#BundleDir}\VERSION"; DestDir: "{app}"; Flags: ignoreversion

; LICENSE
Source: "{#BundleDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Dirs]
Name: "{app}\data"
Name: "{app}\logs"

[Icons]
; Start Menu shortcut — launches the tray app
Name: "{group}\RCFlow"; Filename: "{app}\rcflow.exe"; Parameters: "tray"; IconFilename: "{app}\tray_icon.ico"; Comment: "RCFlow Action Server"
Name: "{group}\Uninstall RCFlow"; Filename: "{uninstallexe}"

[Tasks]
Name: "startwithwindows"; Description: "Start RCFlow when Windows starts"; GroupDescription: "Additional options:"

[Registry]
; "Start with Windows" — only if user checks the task
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "RCFlow"; ValueData: """{app}\rcflow.exe"" tray"; Flags: uninsdeletevalue; Tasks: startwithwindows

[Run]
; Run database migrations after install (timeout handled in [Code])
Filename: "{app}\rcflow.exe"; Parameters: "migrate"; StatusMsg: "Running database migrations..."; Flags: runhidden waituntilterminated
; Optionally launch the tray app after install
Filename: "{app}\rcflow.exe"; Parameters: "tray"; Description: "Launch RCFlow"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
; Clean up any running instance before uninstall
Filename: "taskkill"; Parameters: "/F /IM rcflow.exe"; Flags: runhidden; RunOnceId: "KillRCFlow"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// Kill any orphan rcflow.exe processes during uninstall or rollback to
// prevent file locks from blocking file removal.
procedure KillRCFlowProcesses;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/F /IM rcflow.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure DeinitializeSetup;
begin
  // Called when setup exits (including after cancel/rollback).
  // Kill any rcflow.exe that might still be running from the migration step.
  KillRCFlowProcesses;
end;

procedure DeinitializeUninstall;
begin
  KillRCFlowProcesses;
end;

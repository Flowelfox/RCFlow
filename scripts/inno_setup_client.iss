; RCFlow Client Windows Installer — Inno Setup Script
;
; Compile with:
;   iscc inno_setup_client.iss /DBundleDir=<path> /DAppVersion=<ver> /DArch=<arch>
;
; Parameters (passed via /D on the command line):
;   BundleDir      — Path to the Flutter Windows build output directory
;                    (e.g. rcflowclient\build\windows\x64\runner\Release)
;   AppVersion     — e.g. "1.34.0"
;   Arch           — e.g. "amd64"
;   OutputDir      — Output directory for the installer (default: dist)
;   OutputFilename — Base name for the .exe installer (no extension)

#ifndef BundleDir
  #error "BundleDir must be defined via /DBundleDir=..."
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef Arch
  #define Arch "amd64"
#endif

#ifndef OutputDir
  #define OutputDir "dist"
#endif

#ifndef OutputFilename
  #define OutputFilename "rcflow-client-setup"
#endif

[Setup]
AppName=RCFlow Client
AppVersion={#AppVersion}
AppPublisher=RCFlow
AppPublisherURL=https://github.com/user/rcflow
DefaultDirName={autopf}\RCFlow Client
DefaultGroupName=RCFlow Client
OutputDir={#OutputDir}
OutputBaseFilename={#OutputFilename}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=yes
; Code signing — pass /DSignFiles=1 and define a signtool when invoking ISCC
#ifdef SignFiles
SignTool=MySignTool
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Main Flutter executable
Source: "{#BundleDir}\rcflowclient.exe"; DestDir: "{app}"; Flags: ignoreversion
; Flutter engine and plugin DLLs (all *.dll files in the release directory)
Source: "{#BundleDir}\*.dll"; DestDir: "{app}"; Flags: ignoreversion
; Flutter app data (assets, fonts, icudtl.dat, etc.)
Source: "{#BundleDir}\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\RCFlow Client"; Filename: "{app}\rcflowclient.exe"; Comment: "RCFlow Desktop Client"
Name: "{group}\Uninstall RCFlow Client"; Filename: "{uninstallexe}"
Name: "{autodesktop}\RCFlow Client"; Filename: "{app}\rcflowclient.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"

[Run]
Filename: "{app}\rcflowclient.exe"; Description: "Launch RCFlow Client"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM rcflowclient.exe"; Flags: runhidden; RunOnceId: "KillRCFlowClient"

[Code]
procedure KillClientProcesses;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/F /IM rcflowclient.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure DeinitializeSetup;
begin
  KillClientProcesses;
end;

procedure DeinitializeUninstall;
begin
  KillClientProcesses;
end;

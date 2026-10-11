; Instalador do MultiDesk para Windows (Inno Setup 6).
; Gerado por build_windows.ps1, que passa /DAppVersion=... e /DSourceDir=...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\MultiDesk"
#endif

[Setup]
AppId={{6F3B2C1E-8A4D-4C7B-9E2F-5D1A0B7C3E91}
AppName=MultiDesk
AppVersion={#AppVersion}
AppVerName=MultiDesk {#AppVersion}
AppPublisher=MultiDesk
DefaultDirName={autopf}\MultiDesk
DefaultGroupName=MultiDesk
DisableProgramGroupPage=yes
; Instala só para o usuário atual: não pede senha de administrador.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist\installer
OutputBaseFilename=MultiDesk-Setup-{#AppVersion}
SetupIconFile=multidesk.ico
UninstallDisplayIcon={app}\MultiDesk.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
Name: "autostart"; Description: "Iniciar o MultiDesk junto com o Windows"; GroupDescription: "Inicialização:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MultiDesk"; Filename: "{app}\MultiDesk.exe"
Name: "{autodesktop}\MultiDesk"; Filename: "{app}\MultiDesk.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MultiDesk"; ValueData: """{app}\MultiDesk.exe"" --autostart"; Tasks: autostart

[Run]
Filename: "{app}\MultiDesk.exe"; Description: "Abrir o MultiDesk agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im MultiDesk.exe"; Flags: runhidden; RunOnceId: "KillMultiDesk"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { O próprio app pode ter ligado "iniciar com o sistema"; remove ao desinstalar. }
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MultiDesk');
end;

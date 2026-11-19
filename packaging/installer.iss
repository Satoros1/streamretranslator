; Установщик StreamClipper (Inno Setup). Ставится без прав администратора.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppId={{8C2F3B1E-6B0D-4E6E-9E55-5C1A7D2B9F10}
AppName=StreamClipper
AppVersion={#AppVersion}
AppPublisher=Satoros1
AppPublisherURL=https://github.com/Satoros1/streamretranslator
DefaultDirName={localappdata}\Programs\StreamClipper
DefaultGroupName=StreamClipper
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=StreamClipper-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=streamclipper.ico
UninstallDisplayIcon={app}\StreamClipper.exe
CloseApplications=force
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "..\dist\StreamClipper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\StreamClipper"; Filename: "{app}\StreamClipper.exe"
Name: "{autodesktop}\StreamClipper"; Filename: "{app}\StreamClipper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StreamClipper.exe"; Description: "Запустить StreamClipper"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM StreamClipper.exe /F & taskkill /IM sc-worker.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

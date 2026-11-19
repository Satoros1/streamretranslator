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
; журнал установки в папке TEMP: по нему видно, почему обновление не поставилось
SetupLogging=yes
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
; Обновление кнопкой из самой программы: она запускает установщик с ключом /UPDATE=1 и закрывается,
; а установщик после установки открывает новую версию.
Filename: "{app}\StreamClipper.exe"; Parameters: "--updated"; Flags: nowait; Check: IsUpdate

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM StreamClipper.exe /F & taskkill /IM sc-worker.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

// Программа запустила установщик и закрывается: останавливает запись стримов и свои служебные процессы.
// Пока она это делает, её файлы заняты. Ждём до минуты; если не дождались, установщик закроет её сам
// (CloseApplications=force).
function InitializeSetup: Boolean;
var
  Waited: Integer;
begin
  Result := True;
  if IsUpdate then
  begin
    Waited := 0;
    while CheckForMutexes('StreamClipper_single_instance') and (Waited < 240) do
    begin
      Sleep(250);
      Waited := Waited + 1;
    end;
  end;
end;

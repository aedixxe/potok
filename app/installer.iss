; Inno Setup script — установщик «Поток» (Potok) 1.0.0
; Сборка: "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
; Требует собранный onedir-дистрибутив в dist\Potok\ (PyInstaller).

#define AppName "Поток"
#define AppVersion "1.0.0"
#define AppExe "Potok.exe"
#define AppPublisher "Поток"
#define AppURL "https://t.me/exxidea"

[Setup]
; Стабильный AppId — не менять между версиями (нужен для апдейтов/деинсталляции)
AppId={{B7A4E2C9-3F61-4D58-9A0E-1C2D5E8F4A37}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
; Папка установки — латиницей (кириллица в пути может ломать нативные onnx-библиотеки)
DefaultDirName={autopf}\Potok
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user установка — без прав администратора и без UAC
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename=Potok-Setup
SetupIconFile=ptt\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked
Name: "autostart"; Description: "Запускать «Поток» при входе в Windows"; GroupDescription: "Автозапуск:"

[Files]
Source: "dist\Potok\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Автозапуск — тот же ключ и имя, что использует тумблер в трее (autostart.py)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "Potok"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить «Поток» сейчас"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; config.toml создаётся приложением рядом с exe — удалить при деинсталляции
Type: files; Name: "{app}\config.toml"
Type: dirifempty; Name: "{app}"

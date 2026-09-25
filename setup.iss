#define MyAppName "Video Converter"
#define MyAppVersion "0.3.5 beta 1"
#define VersionInfoVersion "0.1.0.0"
#define MyAppPublisher "Vanyunin Alexander"

[Setup]
AppId={{F9F05D79-D5E3-490B-A324-A878E44C23B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#VersionInfoVersion}
AppCopyright=Copyright (C) 2025-2026 {#MyAppPublisher}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\video_converter
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=.\dist
SetupIconFile=.\images\favicon.ico
UninstallDisplayIcon={app}\VC.exe
LicenseFile=.\LICENSE
OutputBaseFilename=Video_Converter {#MyAppVersion} Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes

[Icons]
; Иконка в меню "Пуск"
Name: "{group}\Video Converter"; Filename: "{app}\VC.exe"

; Иконка на рабочем столе
Name: "{autodesktop}\Video Converter"; Filename: "{app}\VC.exe"


[Tasks]
Name: "contextmenu"; Description: "{cm:ContextMenuTask}"; GroupDescription: "{cm:ContextMenuGroup}"

[CustomMessages]
english.ContextMenuGroup=Explorer integration:
russian.ContextMenuGroup=Интеграция с Проводником:
english.ContextMenuTask=Add the "Convert" item to the context menu of video files (MKV, MP4, MOV, AVI)
russian.ContextMenuTask=Добавить пункт «Сконвертировать» в контекстное меню видеофайлов (MKV, MP4, MOV, AVI)
english.ContextMenuVerb=Convert
russian.ContextMenuVerb=Сконвертировать

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: ".\dist\VC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; Пункт контекстного меню Проводника для видеофайлов. Ключи в HKCU, т.к. установка без прав администратора.
; MultiSelectModel=Player снимает ограничение Проводника в 15 выделенных файлов.
; Проводник запускает VC.exe отдельно для каждого файла; приложение само собирает их в одно окно.
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\VideoConverter"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{cm:ContextMenuVerb}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\VideoConverter"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\VC.exe"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\VideoConverter"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mkv\shell\VideoConverter\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VC.exe"" ""%1"""; Tasks: contextmenu

Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\VideoConverter"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{cm:ContextMenuVerb}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\VideoConverter"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\VC.exe"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\VideoConverter"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mp4\shell\VideoConverter\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VC.exe"" ""%1"""; Tasks: contextmenu

Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\VideoConverter"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{cm:ContextMenuVerb}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\VideoConverter"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\VC.exe"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\VideoConverter"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.mov\shell\VideoConverter\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VC.exe"" ""%1"""; Tasks: contextmenu

Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\VideoConverter"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{cm:ContextMenuVerb}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\VideoConverter"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\VC.exe"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\VideoConverter"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.avi\shell\VideoConverter\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VC.exe"" ""%1"""; Tasks: contextmenu

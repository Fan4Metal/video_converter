; Версию (из __VERSION__ в vc/__init__.py) передаёт tools/make_release.py:
; MyAppVersion — как есть, VersionInfoVersion — дополненной до четырёх чисел.
#ifndef MyAppVersion
  #error Установщик собирается через tools/make_release.py: он передаёт версию (MyAppVersion)
#endif
#ifndef VersionInfoVersion
  #error Установщик собирается через tools/make_release.py: он передаёт версию (VersionInfoVersion)
#endif
#define MyAppName "Video Converter"
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
OutputDir=..\dist
SetupIconFile=..\images\favicon.ico
UninstallDisplayIcon={app}\VC.exe
LicenseFile=..\LICENSE
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
english.ContextMenuTask=Add the "Convert" item to the context menu of video files (MKV, MP4, MOV, AVI, M2TS and others)
russian.ContextMenuTask=Добавить пункт «Сконвертировать» в контекстное меню видеофайлов (MKV, MP4, MOV, AVI, M2TS и другие)
english.ContextMenuVerb=Convert
russian.ContextMenuVerb=Сконвертировать

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "..\dist\VC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; Пункт контекстного меню Проводника для видеофайлов. Ключи в HKCU, т.к. установка без прав администратора.
; MultiSelectModel=Player снимает ограничение Проводника в 15 выделенных файлов.
; Проводник запускает VC.exe отдельно для каждого файла; приложение само собирает их в одно окно.
; Списки расширений (Exts — с пунктом меню, NoExts — без него) make_release.py
; генерирует из vc/formats.py и передаёт путь к файлу в FormatsInclude.
#ifndef FormatsInclude
  #error Установщик собирается через tools/make_release.py: он передаёт список расширений (FormatsInclude)
#endif
#include FormatsInclude
#define I

; Пункт мог остаться от предыдущей версии установщика, где расширение было в списке, — удаляем.
#sub RemovedContextMenuEntry
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#NoExts[I]}\shell\VideoConverter"; ValueType: none; Flags: deletekey
#endsub

#sub ContextMenuEntry
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Exts[I]}\shell\VideoConverter"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{cm:ContextMenuVerb}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Exts[I]}\shell\VideoConverter"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\VC.exe"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Exts[I]}\shell\VideoConverter"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Exts[I]}\shell\VideoConverter\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VC.exe"" ""%1"""; Tasks: contextmenu
#endsub

#for {I = 0; I < DimOf(NoExts); I++} RemovedContextMenuEntry
#for {I = 0; I < DimOf(Exts); I++} ContextMenuEntry

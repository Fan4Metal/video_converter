#define MyAppName "Video Converter"
#define MyAppVersion "0.2.3"
#define VersionInfoVersion "0.1.0.0"
#define MyAppPublisher "Vanyunin Alexander"

[Setup]
; NOTE: The value of AppId uniquely identifies this application. Do not use the same AppId value in installers for other applications.
; (To generate a new GUID, click Tools | Generate GUID inside the IDE.)
AppId={{F9F05D79-D5E3-490B-A324-A878E44C23B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#VersionInfoVersion}
AppCopyright=Copyright (C) 2025 {#MyAppPublisher}
;AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\video_converter
DefaultGroupName={#MyAppName}
; Remove the following line to run in administrative install mode (install for all users.)
PrivilegesRequired=lowest
OutputDir=.\dist
SetupIconFile=.\images\favicon.ico
UninstallDisplayIcon=.\images\favicon.ico
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


[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: ".\dist\VC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

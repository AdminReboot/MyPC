; Bộ cài NetWatchdog (Inno Setup 6). Build: packaging\build.ps1 -Version 1.2.3
; Cài vào Program Files, tạo Scheduled Task chạy nền khi đăng nhập, lối tắt Start Menu/Desktop.
; Cấu hình nằm ở %LOCALAPPDATA%\NetWatchdog nên cập nhật / gỡ cài đặt không mất cấu hình.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3C2B1A-6D4E-4B7A-9C21-5E0F7A9D3B64}
AppName=NetWatchdog
AppVersion={#AppVersion}
AppVerName=NetWatchdog {#AppVersion}
AppPublisher=AdminReboot
AppPublisherURL=https://github.com/AdminReboot/MyPC
AppSupportURL=https://github.com/AdminReboot/MyPC/issues
AppUpdatesURL=https://github.com/AdminReboot/MyPC/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\NetWatchdog
DefaultGroupName=NetWatchdog
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\build\installer
OutputBaseFilename=NetWatchdog-Setup-{#AppVersion}
SetupIconFile=netwatchdog.ico
UninstallDisplayIcon={app}\NetWatchdog.exe
UninstallDisplayName=NetWatchdog
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Xóa thư viện của bản cũ trước khi chép bản mới
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\build\dist\NetWatchdog\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NetWatchdog"; Filename: "{app}\NetWatchdog.exe"
Name: "{autodesktop}\NetWatchdog"; Filename: "{app}\NetWatchdog.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NetWatchdog.exe"; Parameters: "--install-task"; StatusMsg: "Registering background task..."; Flags: runhidden waituntilterminated
Filename: "{sys}\schtasks.exe"; Parameters: "/Run /TN NetWatchdog"; Flags: runhidden waituntilterminated
Filename: "{app}\NetWatchdog.exe"; Description: "Open NetWatchdog"; Flags: postinstall nowait skipifsilent
; Cập nhật từ trong ứng dụng (/SILENT /reopen=1): mở lại cửa sổ sau khi cài xong
Filename: "{app}\NetWatchdog.exe"; Flags: nowait; Check: ShouldReopen

[UninstallRun]
Filename: "{app}\NetWatchdog.exe"; Parameters: "--uninstall-task"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM NetWatchdog.exe"; Flags: runhidden waituntilterminated; RunOnceId: "KillApp"

[Code]
function ShouldReopen: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:reopen|0}') = '1');
end;

{ Dừng bản đang chạy (kể cả bản chạy bằng Python cũ) để chép đè được file }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN NetWatchdog', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM NetWatchdog.exe', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like ''*netwatchdog.py*'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
    '', SW_HIDE, ewWaitUntilTerminated, rc);
  Result := '';
end;

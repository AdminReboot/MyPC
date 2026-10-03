# Go NetWatchdog: dung va xoa Scheduled Task, xoa loi tat Desktop (giu lai config.json, logs).
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    exit
}
Stop-ScheduledTask -TaskName 'NetWatchdog' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'NetWatchdog' -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like '*netwatchdog.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'NetWatchdog - Cai dat.lnk') -ErrorAction SilentlyContinue
Write-Host 'Da go NetWatchdog.' -ForegroundColor Green
Read-Host 'Enter de dong'

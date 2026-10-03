@echo off
rem ==== Dung NetWatchdog dang chay nen ====
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*netwatchdog.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Da dung PID ' + $_.ProcessId) }"
echo Xong. (Neu da cai bang install.ps1, NetWatchdog se tu chay lai o lan dang nhap sau.)
timeout /t 4 >nul

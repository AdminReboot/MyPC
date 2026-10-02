# Cai dat NetWatchdog: tao Scheduled Task chay nen voi quyen Administrator moi khi dang nhap.
# Chay: chuot phai install.ps1 -> "Run with PowerShell" (hoac: powershell -ExecutionPolicy Bypass -File install.ps1)
$ErrorActionPreference = 'Stop'
$TaskName = 'NetWatchdog'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Tu nang quyen Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    exit
}

# Tim Python 3
$python = $null
foreach ($cmd in @('py', 'python')) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $args3 = @(); if ($cmd -eq 'py') { $args3 = @('-3') }
        $exe = & $cmd @args3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { $python = $exe.Trim(); break }
    }
}
if (-not $python) {
    Write-Host 'Khong tim thay Python 3. Cai tu https://www.python.org/downloads/ (tick "Add python.exe to PATH") roi chay lai.' -ForegroundColor Red
    Read-Host 'Enter de thoat'; exit 1
}
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Write-Host "Python: $python"

Write-Host 'Cai psutil (bao cao CPU/RAM/pin)...'
& $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $Dir 'requirements.txt')

$cfg = Join-Path $Dir 'config.json'
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $Dir 'config.example.json') $cfg }

# Scheduled Task: chay khi user hien tai dang nhap, quyen cao nhat, tu chay lai neu bi tat
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$Dir\netwatchdog.py`"" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Theo doi mang, tu khoi phuc, bao cao Telegram' -Force | Out-Null

# Loi tat "NetWatchdog - Cai dat" tren Desktop
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'NetWatchdog - Cai dat.lnk'
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = $pythonw
$sh.Arguments = "`"$Dir\settings_gui.py`""
$sh.WorkingDirectory = $Dir
$sh.Save()

Write-Host ''
Write-Host 'Da cai dat xong. Mo cua so cai dat de nhap Telegram + chon Wi-Fi/card mang/ung dung...' -ForegroundColor Green
Start-Process $pythonw -ArgumentList "`"$Dir\settings_gui.py`"" -WorkingDirectory $Dir -Wait
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Write-Host 'NetWatchdog dang chay nen. Log: logs\netwatchdog.log' -ForegroundColor Green
Read-Host 'Enter de dong'

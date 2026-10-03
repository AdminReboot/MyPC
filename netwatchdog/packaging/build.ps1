# Build NetWatchdog.exe (PyInstaller) va bo cai NetWatchdog-Setup-<version>.exe (Inno Setup 6).
# Can: Python 3.10+, pip install -r requirements.txt pyinstaller, Inno Setup 6 (choco install innosetup).
# Chay: powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.2.3
# Ket qua: build\installer\NetWatchdog-Setup-1.2.3.exe
param([string]$Version = '0.0.0')
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version phai co dang X.Y.Z (nhan duoc '$Version')" }

$pack = $PSScriptRoot
$root = Split-Path -Parent $pack
$verFile = Join-Path $root 'version.py'
$verBackup = Get-Content $verFile -Raw

Push-Location $root
try {
    # Ghi phien ban vao version.py de ung dung biet minh la ban nao (dung khi kiem tra cap nhat)
    [IO.File]::WriteAllText($verFile, "`"`"`"Phien ban NetWatchdog (CI ghi theo tag).`"`"`"`n__version__ = `"$Version`"`n")

    python -m PyInstaller --noconfirm --clean --windowed --uac-admin --name NetWatchdog `
        --icon "$pack\netwatchdog.ico" --add-data "$pack\netwatchdog.ico;packaging" `
        --collect-submodules comtypes --exclude-module comtypes.test `
        --distpath "$root\build\dist" --workpath "$root\build\work" --specpath "$root\build" "$root\app.py"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller loi' }

    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $iscc)) { throw 'Khong tim thay Inno Setup 6 (choco install innosetup)' }
    & $iscc /Q "/DAppVersion=$Version" "$pack\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup loi' }
    Get-ChildItem "$root\build\installer\NetWatchdog-Setup-$Version.exe" | ForEach-Object { "Da tao: $($_.FullName)" }
}
finally {
    [IO.File]::WriteAllText($verFile, $verBackup)
    Pop-Location
}

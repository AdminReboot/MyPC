@echo off
rem ==== Chay NetWatchdog ngay (chay nen, khong can cai Scheduled Task) ====
rem Lan dau: tu cai psutil, tao config.json va mo cua so Cai dat.
setlocal
cd /d "%~dp0"

rem Can quyen Administrator de tat/bat card mang va khoi dong lai may
net session >nul 2>&1
if errorlevel 1 (
    echo Dang xin quyen Administrator...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

call :find_python || goto :eof

"%PY%" -c "import psutil" >nul 2>&1
if errorlevel 1 (
    echo Dang cai psutil...
    "%PY%" -m pip install --quiet --disable-pip-version-check -r requirements.txt
)

if not exist config.json (
    copy /y config.example.json config.json >nul
    echo Lan dau chay: hay nhap Telegram, chon Wi-Fi / card mang / ung dung roi bam Luu.
    "%PY%" settings_gui.py
)

start "" "%PYW%" "%~dp0netwatchdog.py"
echo.
echo NetWatchdog dang chay nen. Log: %~dp0logs\netwatchdog.log
echo (Neu da chay roi thi ban moi se tu thoat, khong chay trung.)
timeout /t 5 >nul
exit /b 0

:find_python
set "PY="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY (
    echo Khong tim thay Python 3. Cai tu https://www.python.org/downloads/
    echo nho tick "Add python.exe to PATH", roi chay lai file nay.
    pause
    exit /b 1
)
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
exit /b 0

@echo off
rem ==== Mo cua so Cai dat NetWatchdog ====
setlocal
cd /d "%~dp0"
set "PY="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY (
    echo Khong tim thay Python 3. Cai tu https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
    pause
    exit /b 1
)
if not exist config.json copy /y config.example.json config.json >nul
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" "%~dp0settings_gui.py"

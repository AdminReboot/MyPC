@echo off
rem Chay thu NetWatchdog o cua so console: chi ghi log, KHONG doi mang / khoi dong lai / mo app.
cd /d "%~dp0"
python netwatchdog.py --dry-run -v
pause

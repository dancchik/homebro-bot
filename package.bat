@echo off
rem Builds a zip archive of the bot in the dist folder.
title HomeBro bot - package
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run install.bat first.
    pause
    exit /b 1
)

echo What should the archive contain?
echo   1 - code only (safe to send to anyone)
echo   2 - code + data: token (.env) and database (bot.db) - to move the bot or as a backup
choice /c 12 /n /m "Press 1 or 2: "
if errorlevel 2 (
    ".venv\Scripts\python.exe" tools\make_package.py --with-data
) else (
    ".venv\Scripts\python.exe" tools\make_package.py
)
pause

@echo off
rem First-time setup on a new computer: creates .venv, installs libraries, creates .env.
title HomeBro bot - install
cd /d "%~dp0"

rem Find Python: the "py" launcher first, then "python"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto no_python

echo Using Python:
%PY% --version
if errorlevel 1 goto no_python

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto failed
)

echo Installing libraries ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo File .env was created. Notepad will open now:
    echo put the bot token into BOT_TOKEN and admin IDs into ADMIN_IDS, then save.
    notepad ".env"
)

echo.
echo Done! Start the bot with start.bat
pause
exit /b 0

:no_python
echo Python was not found.
echo Install Python 3.12 or newer from https://www.python.org/downloads/
echo During installation tick "Add python.exe to PATH", then run install.bat again.
pause
exit /b 1

:failed
echo.
echo Installation failed. See the messages above.
pause
exit /b 1

@echo off
rem Starts the bot in this window and shows its logs.
rem If the bot crashes, it is restarted after 10 seconds. Close the window to stop it.
title HomeBro bot
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The bot is not installed yet. Run install.bat first.
    pause
    exit /b 1
)
if not exist ".env" (
    echo File .env not found. Run install.bat or copy .env.example to .env and fill it in.
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

:run
echo ============================================================
echo  Bot is starting. Logs are also saved to logs\bot.log
echo  To stop the bot, close this window.
echo ============================================================
".venv\Scripts\python.exe" -m bot
if %ERRORLEVEL% EQU 2 (
    echo.
    echo Settings problem - see the error above. Fix .env and run start.bat again.
    pause
    exit /b 2
)
echo.
echo The bot stopped (exit code %ERRORLEVEL%). Restarting in 10 seconds...
echo Close this window to cancel.
timeout /t 10
goto run

@echo off
setlocal
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
cd /d "%~dp0"
set PYTHONUTF8=1
rem No env gate on purpose: a scheduled task does not inherit shell env vars,
rem and poll_ipo_replies reads SLACK_BOT_TOKEN from .env on import.
"%PYTHON%" scripts\poll_ipo_replies.py %*
if errorlevel 1 goto fail
endlocal
exit /b 0
:fail
echo Coverage Manager poll_ipo_replies FAILED with errorlevel %errorlevel%
endlocal
exit /b 1

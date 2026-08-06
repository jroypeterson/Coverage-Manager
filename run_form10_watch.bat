@echo off
setlocal
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
cd /d "%~dp0"
set PYTHONUTF8=1
"%PYTHON%" cli.py form10-watch --days 14
set RC=%errorlevel%
rem rc 2 = something to act on, or something unclassifiable. Both are
rem findings. rc 0 only when the window held nothing relevant at all.
if %RC% NEQ 0 goto fail
endlocal & exit /b 0
:fail
echo Coverage Manager form10-watch finished with errorlevel %RC%
rem endlocal AND exit on ONE line -- endlocal discards RC first otherwise,
rem and a failing run silently reports success.
endlocal & exit /b %RC%

@echo off
setlocal
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
cd /d "%~dp0"
set PYTHONUTF8=1
"%PYTHON%" cli.py symbol-directory
set RC=%errorlevel%
rem rc 2 = a covered name is no longer on the exchange, or the fetch was
rem inconclusive. Both are findings that must not read as a clean run.
if %RC% NEQ 0 goto fail
endlocal & exit /b 0
:fail
echo Coverage Manager symbol-directory finished with errorlevel %RC%
rem endlocal AND exit on ONE line: endlocal discards RC, so a separate
rem `exit /b %RC%` expands to `exit /b` and silently reports success.
endlocal & exit /b %RC%

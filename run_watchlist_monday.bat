@echo off
setlocal
title Weekly Watchlist Report

REM ============================================================================
REM Weekly Watchlist Report - run by the WatchlistMondayReport task (Mon 09:00).
REM Unattended: NO `pause` (it hangs forever under Task Scheduler with no
REM console -> the task aborts as 0x8007042B). Full interpreter path per
REM CONVENTIONS.md. Surfaces python's exit code so a real failure shows red.
REM CRLF + ASCII only.
REM ============================================================================

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
cd /d "%USERPROFILE%\Dropbox\Claude Folder\Coverage Manager"

echo ============================================
echo   Weekly Watchlist Report
echo   %date% %time%
echo ============================================
echo.

"%PYTHON_EXE%" cli.py watchlist-report
set "RC=%errorlevel%"

echo.
echo ============================================
echo   Done (exit code %RC%).
echo ============================================
endlocal & exit /b %RC%

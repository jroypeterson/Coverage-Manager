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

REM Wrapped by run_guarded (board #287): measured BLIND -- ran every week and
REM never heartbeated, so a failure was invisible until the weekly monitor
REM noticed, up to 7 days later. Posts an error health/v1 heartbeat on a
REM non-zero exit and re-exits with python's own code, so RC below is unchanged.
set "GUARD=%USERPROFILE%\Dropbox\Claude Folder\scheduled_jobs_monitor\run_guarded.py"
"%PYTHON_EXE%" "%GUARD%" --lane "WatchlistMondayReport" --project "Coverage Manager" --cadence weekly -- "%PYTHON_EXE%" cli.py watchlist-report
set "RC=%errorlevel%"

echo.
echo ============================================
echo   Done (exit code %RC%).
echo ============================================
endlocal & exit /b %RC%

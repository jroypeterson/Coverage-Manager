@echo off
setlocal
title CRSP Constituent Snapshot

REM ============================================================================
REM CRSP / Morningstar US Total Market constituent snapshot.
REM Run by the CrspQuarterlySnapshot task, Mondays at 08:00.
REM
REM WHY WEEKLY FOR A QUARTERLY FILE: CRSP OVERWRITES the constituents CSV each
REM quarter and keeps no archive, so a quarter missed is gone permanently. It
REM posts roughly a month after each rebalance, but the exact date drifts. A
REM weekly poll costs one 2 MB download and no-ops ("unchanged") whenever the
REM live TradeDate is already archived; a quarterly poll aimed at a drifting
REM date can silently miss one. The cheap run is the safe run.
REM
REM Unattended: NO `pause` (it hangs forever under Task Scheduler with no
REM console -> the task aborts as 0x8007042B). Full interpreter path per
REM CONVENTIONS.md. Surfaces python's exit code so a real failure shows red.
REM Exit 2 = download or verification failed; investigate, do not ignore.
REM CRLF + ASCII only.
REM ============================================================================

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
cd /d "%USERPROFILE%\Dropbox\Claude Folder\Coverage Manager"

echo ============================================
echo   CRSP Constituent Snapshot
echo   %date% %time%
echo ============================================
echo.

"%PYTHON_EXE%" cli.py crsp-snapshot
set "RC=%errorlevel%"

echo.
echo ============================================
echo   Done (exit code %RC%).
echo ============================================
endlocal & exit /b %RC%

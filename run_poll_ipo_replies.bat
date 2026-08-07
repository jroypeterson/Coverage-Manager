@echo off
setlocal
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
cd /d "%~dp0"
set PYTHONUTF8=1
rem No env gate on purpose: a scheduled task does not inherit shell env vars,
rem and poll_ipo_replies reads SLACK_BOT_TOKEN from .env on import.
REM Wrapped by run_guarded (board #287): measured BLIND -- ran 3x daily and never
REM heartbeated, so a failure was invisible until the weekly monitor noticed.
REM Posts an error health/v1 heartbeat on a non-zero exit and re-exits with the
REM payload's own code, so the errorlevel branch below is unchanged. run_guarded
REM resolves the webhook from a sibling .env for the same reason named above --
REM a scheduled task inherits no shell env vars.
set "GUARD=%USERPROFILE%\Dropbox\Claude Folder\scheduled_jobs_monitor\run_guarded.py"
"%PYTHON%" "%GUARD%" --lane "CoverageManager-IpoReplyPoll" --project "Coverage Manager" --cadence daily -- "%PYTHON%" scripts\poll_ipo_replies.py %*
if errorlevel 1 goto fail
endlocal
exit /b 0
:fail
echo Coverage Manager poll_ipo_replies FAILED with errorlevel %errorlevel%
endlocal
exit /b 1

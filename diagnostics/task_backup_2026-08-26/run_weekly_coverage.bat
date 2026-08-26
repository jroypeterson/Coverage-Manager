@echo off
setlocal enabledelayedexpansion
title Weekly Coverage Universe Builder

REM ============================================================================
REM Weekly Coverage Universe Builder - run by the WeeklyCoverageBuilder task
REM (Fri 08:00). Runs the coverage prompt via Claude Code in HEADLESS mode, then
REM commits + pushes whatever it produced.
REM
REM Headless `-p` is REQUIRED for unattended runs. Without it, claude starts an
REM interactive REPL that can't function under Task Scheduler (no console) -- it
REM does the work, then hangs for input until the host aborts the process
REM (rc 0x8007042B = ERROR_PROCESS_ABORTED, the symptom this fixes 2026-05-31).
REM `--dangerously-skip-permissions` lets the unattended agent use tools without
REM prompts. Output is logged so the run is no longer blind.
REM ============================================================================

set "PROJECT_DIR=C:\Users\jroyp\Dropbox\Claude Folder\Coverage Manager"
set "CLAUDE=C:\Users\jroyp\.local\bin\claude.exe"
set "PYTHON=C:\Users\jroyp\AppData\Local\Programs\Python\Python314\python.exe"

for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATESTAMP=%%i"
set "LOG_DIR=%PROJECT_DIR%\.health"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG=%LOG_DIR%\weekly_coverage_%DATESTAMP%.log"

cd /d "%PROJECT_DIR%"

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo === %date% %time% weekly coverage start === >> "%LOG%"
echo ============================================================ >> "%LOG%"

REM Pre-build: sync the Notion 'Stock Watchlist' edits into the positions CSV.
REM Best-effort and non-gating -- the reconciler validates + rolls back on its
REM own, so a sync failure (e.g. wake-time network race) must NOT block the build.
echo --- Notion to CM positions sync before build --- >> "%LOG%"
call "C:\Users\jroyp\Dropbox\Claude Folder\notion_watchlist\run_sync.bat" >> "%LOG%" 2>&1
echo notion sync exit code: !errorlevel! >> "%LOG%"
cd /d "%PROJECT_DIR%"

REM --- PRE-FLIGHT exports publish (added 2026-07-20) ------------------------
REM On 2026-07-17 the task fired 13h late via StartWhenAvailable (machine off
REM at the Fri 08:00 trigger) and the process tree was killed ~26s into the
REM claude.exe step -- rc 0xC000013A, STATUS_CONTROL_C_EXIT. Because EVERY
REM backstop below runs AFTER that long, fragile step, none of them executed:
REM exports/ sat 9 days stale for the seven downstream consumers, and the
REM frozen reporting_calendar.json disabled transcripts' skip gate, which
REM burned its shared AlphaVantage budget.
REM
REM Publishing FIRST costs a few minutes (--skip-discovery does no fundamentals
REM re-fetch) and is idempotent, so the contract is refreshed within minutes of
REM the task starting. A later kill then leaves consumers CURRENT instead of
REM stale. The post-session publish below still runs and is still gated -- this
REM is an additional floor, not a replacement.
REM
REM Deliberately NON-gating: a transient hiccup here must not skip the week's
REM actual work. The post-session publish is the one that turns the task RED.
echo --- pre-flight exports publish --- >> "%LOG%"
"%PYTHON%" cli.py weekly-universe --skip-discovery >> "%LOG%" 2>&1
echo pre-flight publish exit code: !errorlevel! (non-gating) >> "%LOG%"

"%CLAUDE%" -p "run the prompt in weekly_coverage_prompt.md" --dangerously-skip-permissions >> "%LOG%" 2>&1
set "CLAUDE_RC=!errorlevel!"
echo. >> "%LOG%"
echo claude.exe headless exit code: !CLAUDE_RC! >> "%LOG%"

REM Deterministic publish backstop. The headless session is SUPPOSED to run the
REM exports publish itself, but on 2026-06-26 it backgrounded the build and
REM exited (claude -p has no re-invocation), so exports went 10 days stale while
REM the task still showed rc=0. Running the publish here unconditionally
REM guarantees exports/ + manifest.json are regenerated every week regardless of
REM what the agent did. It is idempotent: a clean re-publish just refreshes the
REM manifest timestamp. --skip-discovery means no fundamentals re-fetch (~few min).
echo --- deterministic exports publish backstop --- >> "%LOG%"
"%PYTHON%" cli.py weekly-universe --skip-discovery >> "%LOG%" 2>&1
set "PUBLISH_RC=!errorlevel!"
echo publish backstop exit code: !PUBLISH_RC! >> "%LOG%"
if not "!PUBLISH_RC!"=="0" goto publishfail

REM Commit + push whatever the session produced (the prompt may also commit
REM internally; this is the backstop). Uses goto, not a paren block. Each git
REM step's exit code is captured + gated so a failed publish/commit/push turns
REM the task RED instead of exiting green-but-stale -- otherwise the backstop
REM reintroduces the exact 2026-06-26 blind spot it exists to close (Codex
REM review 2026-06-29).
echo --- git add / commit / push --- >> "%LOG%"
git add -A >> "%LOG%" 2>&1
git diff --cached --quiet
if not errorlevel 1 goto nochanges
git commit -m "Weekly update %DATESTAMP%" >> "%LOG%" 2>&1
set "COMMIT_RC=!errorlevel!"
if not "!COMMIT_RC!"=="0" goto commitfail
git push origin master >> "%LOG%" 2>&1
set "PUSH_RC=!errorlevel!"
if not "!PUSH_RC!"=="0" goto pushfail
echo Pushed to GitHub. >> "%LOG%"
goto done
:nochanges
echo No changes to commit. >> "%LOG%"
:done

REM Deterministic performance-report backstop. The full consolidated coverage
REM report (coverage_consolidated_*.html + per-segment HTML + xlsx) is produced
REM ONLY by the reporting-side performance step, which NO scheduled task runs --
REM so it silently went stale after 2026-05-29 while the Monday watchlist report
REM kept updating. Generate it here unconditionally, AFTER the exports publish +
REM git push, so a transient provider hiccup in the report never blocks the
REM critical exports contract. reports/ is gitignored, so nothing needs
REM committing here. EMAIL_ENABLED=False, so no email is sent (cli.py
REM performance honors the flag). cli.py performance exits nonzero only on a
REM real exception, so gating on it below won't false-RED on soft flags.
echo --- deterministic performance-report backstop --- >> "%LOG%"
"%PYTHON%" cli.py performance >> "%LOG%" 2>&1
set "PERF_RC=!errorlevel!"
echo performance backstop exit code: !PERF_RC! >> "%LOG%"
if not "!PERF_RC!"=="0" goto perffail

echo === %date% %time% done (claude rc=!CLAUDE_RC!) === >> "%LOG%"
REM Surface the Claude session's own exit code as the task result, so a genuine
REM session failure still shows red -- but a clean headless run now exits 0.
endlocal & exit /b %CLAUDE_RC%

REM --- failure exits: any of these turns the scheduled task RED so a stale
REM publish is caught Friday, not 10 days later by the downstream watchdog. ---
:publishfail
echo ERROR: publish backstop failed (rc=!PUBLISH_RC!); skipping git publish, returning failure. >> "%LOG%"
echo === %date% %time% FAILED: publish backstop (rc=!PUBLISH_RC!) === >> "%LOG%"
endlocal & exit /b %PUBLISH_RC%

:commitfail
echo ERROR: git commit failed (rc=!COMMIT_RC!); returning failure. >> "%LOG%"
echo === %date% %time% FAILED: git commit (rc=!COMMIT_RC!) === >> "%LOG%"
endlocal & exit /b %COMMIT_RC%

:pushfail
echo ERROR: git push failed (rc=!PUSH_RC!); GitHub consumers may be stale; returning failure. >> "%LOG%"
echo === %date% %time% FAILED: git push (rc=!PUSH_RC!) === >> "%LOG%"
endlocal & exit /b %PUSH_RC%

:perffail
echo ERROR: performance-report backstop failed (rc=!PERF_RC!); exports were published but the consolidated coverage report is stale; returning failure. >> "%LOG%"
echo === %date% %time% FAILED: performance backstop (rc=!PERF_RC!) === >> "%LOG%"
endlocal & exit /b %PERF_RC%

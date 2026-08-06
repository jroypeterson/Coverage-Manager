@echo off
setlocal
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
cd /d "%~dp0"
set PYTHONUTF8=1
rem One-off: fill ISIN / Country (HQ) / Currency on the 2026-08-06 biopharma
rem batch. The weekly pipeline runs cik_backfill but NOT enrich, so CIKs heal
rem on Friday and these three columns do not. Run overnight: yfinance was
rem timing out at 30s per ticker during the day, which is ~11h for 1,328 rows.
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set STAMP=%DT:~0,8%
"%PYTHON%" cli.py enrich > ".health\enrich_%STAMP%.log" 2>&1
set RC=%errorlevel%
if %RC% NEQ 0 goto fail
endlocal & exit /b 0
:fail
echo Coverage Manager enrich finished with errorlevel %RC%
endlocal & exit /b %RC%

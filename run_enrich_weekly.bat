@echo off
setlocal
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
cd /d "%~dp0"
set PYTHONUTF8=1
rem Identifier enrichment (ISIN / Country / Currency / FIGI). Deliberately NOT
rem a step of weekly-universe: it fetches all ~1,330 tickers from yfinance and
rem took ~1.5h on 2026-08-06 at 30s-per-ticker timeouts. Friday's build is the
rem publish contract and has already been killed mid-run once; this is
rem maintenance and belongs off that critical path.
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set STAMP=%DT:~0,8%
"%PYTHON%" cli.py enrich > ".health\enrich_%STAMP%.log" 2>&1
set RC=%errorlevel%
if %RC% NEQ 0 goto fail
endlocal & exit /b 0
:fail
echo Coverage Manager enrich finished with errorlevel %RC%
endlocal & exit /b %RC%

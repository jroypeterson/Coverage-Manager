@echo off
title Weekly Watchlist Report
cd /d "C:\Users\jroyp\Dropbox\Claude Folder\Coverage Manager"
echo ============================================
echo   Weekly Watchlist Report
echo   %date% %time%
echo ============================================
echo.
python cli.py watchlist-report
echo.
echo ============================================
echo   Done. Press any key to close.
echo ============================================
pause

@echo off
title Weekly Coverage Universe Builder
cd /d "C:\Users\jroyp\Dropbox\Claude Folder"
echo ============================================
echo   Weekly Coverage Universe Builder
echo   %date% %time%
echo ============================================
echo.
echo Starting Claude Code with weekly coverage prompt...
echo.
"C:\Users\jroyp\.local\bin\claude.exe" "run the prompt in Coverage Manager/weekly_coverage_prompt.md"
echo.
echo ============================================
echo   Done. Press any key to close.
echo ============================================
pause

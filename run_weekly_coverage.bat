@echo off
title Weekly Coverage Universe Builder
cd /d "%USERPROFILE%\Dropbox\Claude Folder\Coverage Manager"
echo ============================================
echo   Weekly Coverage Universe Builder
echo   %date% %time%
echo ============================================
echo.
echo Running weekly build workflow...
echo.
python cli.py weekly-build --skip-discovery
echo.
echo ============================================
echo   Done. Press any key to close.
echo ============================================
pause

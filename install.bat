@echo off
title LessonFlow setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "install.ps1"
echo.
pause

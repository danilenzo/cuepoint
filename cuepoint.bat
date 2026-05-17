@echo off
cd /d "%~dp0"
python -m cuepoint.gui
if %errorlevel% neq 0 pause

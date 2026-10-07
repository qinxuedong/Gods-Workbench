@echo off
setlocal
cd /d "%~dp0"
python run.py --prod --open-browser
exit /b %errorlevel%

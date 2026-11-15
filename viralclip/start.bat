@echo off
REM Double-click to start ViralClip (Windows). First run sets everything up.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 run.py %*) else (python run.py %*)
pause

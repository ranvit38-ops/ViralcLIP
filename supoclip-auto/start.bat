@echo off
REM Double-click to run the SupoClip automation. Leave the window open (or use autostart.ps1).
cd /d "%~dp0"
for %%V in (3.12 3.11 3.13) do (
  py -%%V -c "pass" >nul 2>nul && (py -%%V auto.py %* & goto :end)
)
python auto.py %*
:end
pause

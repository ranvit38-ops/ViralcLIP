@echo off
REM Double-click to start ViralClip (Windows). First run sets everything up.
cd /d "%~dp0"
REM Use the first Python that actually runs; 3.12 is the tested version.
for %%V in (3.12 3.11 3.13) do (
  py -%%V -c "pass" >nul 2>nul && (py -%%V run.py %* & goto :end)
)
python -c "pass" >nul 2>nul && (python run.py %* & goto :end)
echo No working Python 3.11-3.13 found. Run:  winget install Python.Python.3.12
echo then close and reopen this window.
:end
pause

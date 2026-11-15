# Run ViralClip in the background every time you log into Windows — no window needed for automation.
#   Turn on:   powershell -ExecutionPolicy Bypass -File .\autostart.ps1
#   Turn off:  powershell -ExecutionPolicy Bypass -File .\autostart.ps1 -Remove
# Open the app any time at http://localhost:3000 (or double-click start.bat: it just opens the browser).
# Your PC must be on and awake for automation to run.
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$link = Join-Path ([Environment]::GetFolderPath("Startup")) "ViralClip.lnk"
$pyw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
$pyArgs = "-3 "
if (-not $pyw) { $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source; $pyArgs = "" }
if (-not $pyw) { Write-Host "Python isn't installed. Run: winget install Python.Python.3.12" -ForegroundColor Red; exit 1 }

if ($Remove) {
  Remove-Item $link -ErrorAction SilentlyContinue
  & $pyw.Replace("pyw.exe", "py.exe").Replace("pythonw.exe", "python.exe") "$dir\run.py" --stop
  Write-Host "Autostart removed and ViralClip stopped." -ForegroundColor Green
  exit 0
}

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($link)
$s.TargetPath = $pyw
$s.Arguments = "$pyArgs`"$dir\run.py`" --background --no-browser"
$s.WorkingDirectory = $dir
$s.WindowStyle = 7
$s.Description = "ViralClip automation (runs in the background)"
$s.Save()

Start-Process -FilePath $pyw -ArgumentList "$pyArgs`"$dir\run.py`" --background --no-browser" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "Done. ViralClip now runs in the background and starts by itself when you log in." -ForegroundColor Green
Write-Host "First start may take several minutes (setup). Then open http://localhost:3000"
Write-Host "Logs: $dir\backend\data\launcher.log  |  Turn off: .\autostart.ps1 -Remove"
Write-Host "`nTip: so automation doesn't pause, stop the PC from sleeping while plugged in:"
Write-Host "  powercfg /change standby-timeout-ac 0"

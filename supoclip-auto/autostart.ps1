# Starts the automation hidden at every login.  Remove with:  autostart.ps1 -Remove
param([switch]$Remove)
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'SupoClip-Auto.lnk'
if ($Remove) { Remove-Item $lnk -ErrorAction SilentlyContinue; "Removed."; exit }
$pyw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command pythonw).Source }
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw; $s.Arguments = "`"$PSScriptRoot\auto.py`""; $s.WorkingDirectory = $PSScriptRoot; $s.Save()
"Done. The automation starts hidden at every login (SupoClip itself must be running in Docker)."

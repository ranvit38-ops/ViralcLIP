# Moves ViralClip to the D: drive and deletes ViralClip's downloads from C:.
# Run from PowerShell (NOT as administrator):
#   powershell -ExecutionPolicy Bypass -File .\move-to-d.ps1
# Nothing is deleted until you type YES. Close the ViralClip window first.
param([string]$Target = "D:\ViralcLIP")

$ErrorActionPreference = "Stop"
function Size($p) { if (Test-Path $p) { "{0:N1} GB" -f ((Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB) } else { "-" } }

if (-not (Test-Path "D:\")) { Write-Host "There is no D: drive on this computer." -ForegroundColor Red; exit 1 }
if (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "ViralClip seems to be running (port 3000). Close its window first, then run this again." -ForegroundColor Yellow; exit 1
}

$oldCopy = Join-Path $HOME "ViralcLIP"

# 1. Move the app if it's on C: and not on D: yet
if ((Test-Path $oldCopy) -and -not (Test-Path $Target)) {
  Write-Host "Moving $oldCopy -> $Target (this can take a few minutes)..."
  Move-Item $oldCopy $Target
  Remove-Item (Join-Path $Target "viralclip\backend\.venv") -Recurse -Force -ErrorAction SilentlyContinue  # rebuilt on next start
  Write-Host "Moved." -ForegroundColor Green
}

# Keep autostart working: point the Startup shortcut (from autostart.ps1) at the new location.
$link = Join-Path ([Environment]::GetFolderPath("Startup")) "ViralClip.lnk"
if ((Test-Path $link) -and (Test-Path $Target)) {
  $sh = New-Object -ComObject WScript.Shell
  $s = $sh.CreateShortcut($link)
  $newDir = Join-Path $Target "viralclip"
  $s.Arguments = $s.Arguments -replace [regex]::Escape($oldCopy + "\viralclip"), $newDir
  $s.WorkingDirectory = $newDir
  $s.Save()
  Write-Host "Updated the autostart shortcut to $newDir." -ForegroundColor Green
}

# 2. ViralClip's downloads on C: (only ViralClip's — other apps' files are left alone)
$hf = Join-Path $HOME ".cache\huggingface\hub"
$items = @()
if ((Test-Path $oldCopy) -and (Test-Path $Target)) { $items += $oldCopy }                       # duplicate old copy
$items += Get-ChildItem $hf -Directory -Filter "models--Systran--faster-whisper-*" -ErrorAction SilentlyContinue | ForEach-Object FullName
$items += Get-ChildItem $hf -Directory -Filter "models--mobiuslabsgmbh--faster-whisper-*" -ErrorAction SilentlyContinue | ForEach-Object FullName
$caches = @((Join-Path $env:LOCALAPPDATA "pip\Cache"), (Join-Path $env:LOCALAPPDATA "npm-cache"))   # safe to delete: just download caches
$items += $caches | Where-Object { Test-Path $_ }

if ($items.Count -eq 0) { Write-Host "Nothing of ViralClip's is left on C:. Done." -ForegroundColor Green; exit 0 }
Write-Host "`nThese will be DELETED from C: :"
foreach ($i in $items) { Write-Host ("  {0,-8} {1}" -f (Size $i), $i) }
Write-Host "(pip/npm caches are only download caches; programs re-download what they need.)"
if ((Read-Host "`nType YES to delete") -ne "YES") { Write-Host "Nothing deleted."; exit 0 }
foreach ($i in $items) { Remove-Item $i -Recurse -Force -ErrorAction Continue; Write-Host "  deleted $i" }
Write-Host "`nDone. Start ViralClip with $Target\viralclip\start.bat — first start rebuilds its setup on D: (5-15 min)." -ForegroundColor Green

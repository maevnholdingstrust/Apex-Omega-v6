# fix_01_utf8_logging.ps1
# Run from repo root:
# powershell -ExecutionPolicy Bypass -File .\fix_01_utf8_logging.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== APEX UTF-8 LOGGING FIX ==="

$repo = Get-Location
$pythonDir = Join-Path $repo "python"
$botFile = Join-Path $pythonDir "polygon_arbitrage_bot.py"

if (!(Test-Path $botFile)) {
    throw "Missing file: $botFile"
}

$backup = "$botFile.bak_utf8_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $botFile $backup
Write-Host "Backup created: $backup"

$content = Get-Content $botFile -Raw

$patch = @'
# --- APEX PATCH: Windows UTF-8 stdout/stderr safety ---
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# --- END APEX PATCH ---
'@

if ($content -notmatch "APEX PATCH: Windows UTF-8 stdout/stderr safety") {
    if ($content -match "^\s*import\s+") {
        $content = $patch + "`r`n" + $content
    } else {
        $content = $patch + "`r`n" + $content
    }

    Set-Content -Path $botFile -Value $content -Encoding UTF8
    Write-Host "Patched UTF-8 safety into polygon_arbitrage_bot.py"
} else {
    Write-Host "UTF-8 patch already present. No change."
}

Write-Host ""
Write-Host "Now run:"
Write-Host "  cd `"$pythonDir`""
Write-Host "  `$env:PYTHONUTF8='1'"
Write-Host "  `$env:PYTHONIOENCODING='utf-8'"
Write-Host "  chcp 65001"
Write-Host "  python polygon_arbitrage_bot.py"
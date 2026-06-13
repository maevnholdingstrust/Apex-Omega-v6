$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "logs"
$PidFile = Join-Path $LogDir "autonomous_scanner.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "No autonomous scanner PID file found."
    exit 0
}

$PidText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $PidText) {
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "Removed empty PID file."
    exit 0
}

$Process = Get-Process -Id ([int]$PidText) -ErrorAction SilentlyContinue
if (-not $Process) {
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "Autonomous scanner was not running. Removed stale PID file."
    exit 0
}

Stop-Process -Id $Process.Id -Force
Remove-Item -LiteralPath $PidFile -Force
Write-Host "Autonomous scanner stopped: PID $PidText"

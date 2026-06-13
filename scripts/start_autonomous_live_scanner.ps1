$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "logs"
$OutLog = Join-Path $LogDir "autonomous_scanner.out.log"
$ErrLog = Join-Path $LogDir "autonomous_scanner.err.log"
$PidFile = Join-Path $LogDir "autonomous_scanner.pid"
$Script = Join-Path $RepoRoot "scripts\autonomous_live_scanner.py"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path $PidFile) {
    $ExistingPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($ExistingPid) {
        $Existing = Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue
        if ($Existing) {
            Write-Host "Autonomous scanner already running: PID $ExistingPid"
            exit 0
        }
    }
}

$env:PYTHONPATH = (Join-Path $RepoRoot "python") + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:LIVE_TRADING_ENABLED = "false"
$env:DRY_RUN = "true"
if (-not $env:MIN_POOL_TVL_USD) {
    $env:MIN_POOL_TVL_USD = "100"
}

$Process = Start-Process `
    -FilePath "python" `
    -ArgumentList ('"{0}"' -f $Script) `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

$Process.Id | Set-Content -Path $PidFile -Encoding UTF8
Write-Host "Autonomous scanner started: PID $($Process.Id)"
Write-Host "Status: $LogDir\autonomous_scanner_status.json"
Write-Host "Logs: $OutLog"

$ErrorActionPreference = "Stop"

$repo = Get-Location
$activeEnv = Join-Path $repo "runtime\active_endpoints.env"

if (Test-Path $activeEnv) {
    Get-Content $activeEnv | ForEach-Object {
        if ($_ -match "^\s*#" -or $_ -match "^\s*$") { return }
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
        }
    }
}

if (-not $env:POLYGON_RPC_URL) {
    throw "POLYGON_RPC_URL is empty. Run .\boot_with_latency_monitor.ps1 once first, or set POLYGON_RPC_URL in .env."
}

Write-Host "Starting Anvil fork from selected Polygon RPC..."
Write-Host "POLYGON_RPC_URL=<loaded>"

anvil --fork-url "$env:POLYGON_RPC_URL" --chain-id 137 --host 127.0.0.1 --port 8545

$ErrorActionPreference = "Stop"

$repo = Get-Location
$envFile = Join-Path $repo ".env"
$activeEnv = Join-Path $repo "runtime\active_endpoints.env"

function Import-ProcessEnvFile([string]$path) {
    if (-not (Test-Path $path)) { return }
    Get-Content $path | ForEach-Object {
        if ($_ -match "^\s*#" -or $_ -match "^\s*$") { return }
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            $value = [regex]::Replace(
                $parts[1],
                '\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
                {
                    param($match)
                    $resolved = [Environment]::GetEnvironmentVariable($match.Groups[1].Value, "Process")
                    if ($null -eq $resolved) { return "" }
                    return $resolved
                }
            )
            [Environment]::SetEnvironmentVariable($parts[0], $value, "Process")
        }
    }
}

Import-ProcessEnvFile $envFile
Import-ProcessEnvFile $activeEnv

$forkUpstream = if ($env:FORK_UPSTREAM_RPC_URL) { $env:FORK_UPSTREAM_RPC_URL } else { $env:POLYGON_RPC_URL }

if (-not $forkUpstream) {
    throw "FORK_UPSTREAM_RPC_URL and POLYGON_RPC_URL are empty. Set the upstream Polygon RPC in .env."
}

Write-Host "Starting Anvil fork from selected Polygon RPC..."
Write-Host "FORK_UPSTREAM_RPC_URL=<loaded>"

anvil --fork-url "$forkUpstream" --chain-id 137 --host 127.0.0.1 --port 8545

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$providerRoot = Join-Path $repoRoot "vendor\web3-rpc-provider"
$stdoutLog = Join-Path $repoRoot "logs\dodo-rpc-provider.out.log"
$stderrLog = Join-Path $repoRoot "logs\dodo-rpc-provider.err.log"

if (-not (Test-Path $providerRoot)) {
    throw "Missing vendor\web3-rpc-provider. Clone https://github.com/DODOEX/web3-rpc-provider first."
}

if (-not $env:PUPPETEER_EXECUTABLE_PATH) {
    $browserCandidates = @(
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    $env:PUPPETEER_EXECUTABLE_PATH = $browserCandidates |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
}

if (-not $env:PUPPETEER_EXECUTABLE_PATH) {
    throw "Set PUPPETEER_EXECUTABLE_PATH to a local Chrome or Chromium executable."
}

$existing = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "DODO RPC provider is already listening on http://127.0.0.1:3000"
    exit 0
}

if (-not (Test-Path (Join-Path $providerRoot "dist\bootstrap.js"))) {
    & pnpm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "DODO RPC provider build failed."
    }
}

$process = Start-Process `
    -FilePath "npm.cmd" `
    -ArgumentList @("run", "start:prod") `
    -WorkingDirectory $providerRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Start-Sleep -Seconds 3
if ($process.HasExited) {
    throw "DODO RPC provider failed to start. See $stderrLog"
}

Write-Host "DODO RPC provider started on http://127.0.0.1:3000 (PID $($process.Id))"

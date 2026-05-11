# test_dexscreener_connectivity.ps1
# Valid PowerShell connectivity test for DEXScreener.

$ErrorActionPreference = "Continue"

Write-Host "=== DNS TEST ==="
try {
    Resolve-DnsName api.dexscreener.com
} catch {
    Write-Host "DNS failed:"
    Write-Host $_.Exception.Message
}

Write-Host "`n=== POWERSHELL HTTPS TEST ==="
try {
    $url = "https://api.dexscreener.com/latest/dex/tokens/0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
    $r = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 20
    Write-Host "Status:" $r.StatusCode
    Write-Host "Length:" $r.Content.Length
    Write-Host $r.Content.Substring(0, [Math]::Min(300, $r.Content.Length))
} catch {
    Write-Host "HTTPS failed:"
    Write-Host $_.Exception.Message
}

Write-Host "`n=== PYTHON AIOHTTP TEST ==="

$pyFile = Join-Path $env:TEMP "apex_dexscreener_test.py"

@'
import asyncio
import aiohttp

async def main():
    url = "https://api.dexscreener.com/latest/dex/tokens/0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as r:
                print("status=", r.status)
                text = await r.text()
                print("length=", len(text))
                print(text[:300])
    except Exception as e:
        print("aiohttp_error=", repr(e))

asyncio.run(main())
'@ | Set-Content -Path $pyFile -Encoding UTF8

try {
    Push-Location ".\python"
    python $pyFile
} catch {
    Write-Host "Python test failed:"
    Write-Host $_.Exception.Message
} finally {
    Pop-Location
}

Remove-Item $pyFile -Force -ErrorAction SilentlyContinue
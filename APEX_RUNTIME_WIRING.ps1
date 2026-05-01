<#
APEX_RUNTIME_WIRING.ps1
Creates runtime_hooks.py and injects auto-import into apex_omega_core.
#>

$ErrorActionPreference = "Stop"

# 1) create runtime folder
$rtDir = "python\apex_omega_core\runtime"
New-Item -ItemType Directory -Force $rtDir | Out-Null

# 2) write runtime_hooks.py
@"
\"\"\"runtime_hooks.py  -  wires external data providers at import time.\"\"\"

from apex_omega_core.scanner import dex_intake
from mygraph.client import fetch_tokens, fetch_pools
from myprice.oracle import get_price as my_price_oracle  # adjust import if needed

dex_intake.TOKEN_UNIVERSE_PROVIDER = lambda: fetch_tokens(chain="polygon")
dex_intake.POOL_STATE_PROVIDER     = lambda tokens: fetch_pools(tokens, chain="polygon")
dex_intake.SPOT_PRICE_PROVIDER     = lambda symbol: my_price_oracle(symbol)
"@ | Set-Content -Encoding UTF8 "$rtDir\runtime_hooks.py"

# 3) ensure apex_omega_core/__init__.py imports the hooks
$initPath = "python\apex_omega_core\__init__.py"
if (-not (Test-Path $initPath)) { "" | Set-Content -Encoding UTF8 $initPath }

$importLine = "from apex_omega_core.runtime import runtime_hooks  # auto-wired providers"
if (-not (Select-String -Path $initPath -Pattern "runtime_hooks" -Quiet)) {
    Add-Content -Path $initPath -Value "`n$importLine`n"
}

Write-Host "[OK] Runtime wiring installed - external providers will load automatically."
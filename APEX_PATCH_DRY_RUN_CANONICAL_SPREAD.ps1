# APEX_PATCH_DRY_RUN_CANONICAL_SPREAD.ps1
# Run from the repo root.

$ErrorActionPreference = "Stop"

$Path = ".\python\dry_run.py"
if (-not (Test-Path $Path)) {
    throw "Cannot find $Path. Run this script from the Apex-Omega-v6 repo root."
}

$Text = Get-Content $Path -Raw
$Backup = "$Path.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Copy-Item $Path $Backup -Force
Write-Host "Backup created: $Backup"

# -----------------------------------------------------------------------------
# 1) Add pool-normalization helper if missing.
#    The canonical spread block must consume USD/token prices only.
# -----------------------------------------------------------------------------
if ($Text -notmatch 'def _pool_token1_price_usd\(') {
    $HelperPattern = '(?s)(def _dex_type_for_slippage\(dex_name: str\) -> str:.*?\n    return "v2"\r?\n\r?\n)'
    $HelperInsert = @'
$1

def _pool_token1_price_usd(pool: "_PoolSnapshot", token0_usd: float) -> float:
    """Return USD per token1 for a pool whose snapshot price is token1 per token0.

    This is the pool-normalization boundary. The canonical spread block
    consumes only USD-per-token prices and does not contain pool-ratio logic.
    """
    if pool.price <= 0.0 or token0_usd <= 0.0:
        return 0.0
    return token0_usd / pool.price

'@
    $NewText = [regex]::Replace($Text, $HelperPattern, $HelperInsert, 1)
    if ($NewText -eq $Text) {
        throw "Could not insert _pool_token1_price_usd helper after _dex_type_for_slippage."
    }
    $Text = $NewText
    Write-Host "Inserted _pool_token1_price_usd helper."
}
else {
    Write-Host "Helper already exists; skipping helper insert."
}

# -----------------------------------------------------------------------------
# 2) Replace the raw spread section inside _compute_opportunity.
#    This removes token-ratio language from the equation layer.
# -----------------------------------------------------------------------------
$BlockPattern = @'
(?s)(    if buy\.kind != "cpmm" or sell\.kind != "cpmm":\r?\n        return None\r?\n\r?\n)(.*?)(    # ------------------------------------------------------------------\r?\n    # Flash-loan sizing)
'@

$CanonicalBlock = @'
$1    sym0, sym1 = pair_key.split("/")
    price0 = token_prices.get(sym0, 1.0)
    price1 = token_prices.get(sym1, 1.0)

    # =============================================================================
    # CANONICAL RAW SPREAD — USD PER TOKENA ONLY
    # =============================================================================
    # Required units:
    #   P_buy_usd  = lowest executable ask for TokenA, in USD / TokenA
    #   P_sell_usd = highest executable bid for TokenA, in USD / TokenA
    #
    # Raw discovery:
    #   delta_p_raw_usd = P_sell_usd - P_buy_usd
    #   raw_spread_bps  = (delta_p_raw_usd / P_buy_usd) * 10_000
    #   raw_profit_usd  = L_usd * (delta_p_raw_usd / P_buy_usd)
    #
    # Pool-specific reserve ratios are normalized before this block.
    # This block only subtracts USD-per-token prices.
    # =============================================================================
    p_buy_usd = _pool_token1_price_usd(buy, price0)
    p_sell_usd = _pool_token1_price_usd(sell, price0)

    if p_buy_usd <= 0.0 or p_sell_usd <= 0.0:
        return None

    delta_p_raw_usd = p_sell_usd - p_buy_usd
    spot_spread_bps = (delta_p_raw_usd / p_buy_usd) * 10_000.0

    if spot_spread_bps < min_spread_bps:
        return None

    raw_profit_usd = trade_size_usd * (delta_p_raw_usd / p_buy_usd)

    # raw_spread_bps starts as raw discovery; upgraded to executable below.
    raw_spread_bps = spot_spread_bps

$3
'@

$Patched = [regex]::Replace($Text, $BlockPattern, $CanonicalBlock, 1)
if ($Patched -eq $Text) {
    throw "Could not replace canonical spread block. Check _compute_opportunity layout."
}

Set-Content -Path $Path -Value $Patched -Encoding UTF8
Write-Host "Patched canonical raw spread block in $Path"

# -----------------------------------------------------------------------------
# 3) Verify syntax and run tests.
# -----------------------------------------------------------------------------
python -m py_compile $Path
Write-Host "Syntax check passed."

python -m pytest python\apex_omega_core\tests -q
Write-Host "Patch complete."

$ErrorActionPreference = "Stop"

cd "C:\Users\The Urban Genius\Documents\Arbitrage\FINAL BUILD\Apex-Omega-v6"

$env:PYTHONPATH = "$PWD\python"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

python -m apex_omega_core.core.finish_today_shadow_runner

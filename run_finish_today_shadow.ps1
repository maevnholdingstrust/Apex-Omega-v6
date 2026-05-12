$ErrorActionPreference = "Stop"

$repo = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $repo

$env:PYTHONPATH = "$PWD\python"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

python -m apex_omega_core.core.finish_today_shadow_runner

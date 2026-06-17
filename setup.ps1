# setup.ps1 -- one-shot setup for the PSO engine on Windows.
#
# Creates TWO environments (they have conflicting deps and must stay separate):
#   .venv      PyPSA stack -- builds/transforms the network (and the native PyPSA workflow)
#   .venv-pso  aimmspy     -- drives PSO for the solve
# Uses `uv` if present (fast); otherwise falls back to stock `python -m venv` + `pip`.
#
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# Prereqs: Python 3.10+ on PATH, plus AIMMS installed (for the solve) -- this
# script does not install AIMMS. aimmspy is installed to match your AIMMS at run
# time; if your AIMMS is older, pin it: see pso.local.toml (aimms_version).

param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$deps = @("pypsa==0.35.2","numpy","pandas","matplotlib","scipy","networkx","openpyxl","highspy")

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "==> uv found -- building envs with uv" -ForegroundColor Cyan
    uv sync
    uv venv .venv-pso
    uv pip install --python .venv-pso aimmspy
} else {
    Write-Host "==> uv not found -- using python -m venv + pip" -ForegroundColor Cyan
    & $Python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install @deps
    & $Python -m venv .venv-pso
    & .\.venv-pso\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv-pso\Scripts\python.exe -m pip install aimmspy
}

# Seed pso.local.toml from the template and wire the solve interpreter.
$cfg = Join-Path $root "pso.local.toml"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $root "pso.local.toml.example") $cfg
    Write-Host "==> created pso.local.toml (edit: project, and license_url if academic)" -ForegroundColor Yellow
}
if (-not (Select-String -Path $cfg -Pattern '^\s*python\s*=' -Quiet)) {
    $psoPy = (Join-Path $root ".venv-pso\Scripts\python.exe") -replace '\\','/'
    Add-Content $cfg "python = `"$psoPy`""
    Write-Host "==> wired solve interpreter -> $psoPy"
}

Write-Host "`nSetup done. Next:" -ForegroundColor Green
Write-Host "  1. Edit pso.local.toml  (project = path to PSO.aimms; license_url if academic)"
Write-Host "  2. .\.venv\Scripts\python.exe pso\doctor.py    # verify"
Write-Host "  3. .\.venv\Scripts\python.exe devnet_stress.py # run (pick PSO in the menu)"

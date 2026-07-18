$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location -LiteralPath $RepoRoot

$env:PYTHONPATH = "backend;mock_edge\src;simulation_edge\src;packages\cloudlabs\src;packages\cloudlabs_edge_dev\src"
$env:CLOUDLABS_SOLO = "1"

if (-not $env:UVICORN_ACCESS_LOG) {
    $env:UVICORN_ACCESS_LOG = "0"
}

& "..\.venv\Scripts\python.exe" "backend\main.py"
exit $LASTEXITCODE

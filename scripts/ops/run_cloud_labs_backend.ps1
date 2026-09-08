$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location -LiteralPath $RepoRoot

$PythonPath = $null
foreach ($Candidate in @(
    (Join-Path $RepoRoot "..\.venv\Scripts\python.exe"),
    (Join-Path $RepoRoot "..\..\.venv\Scripts\python.exe")
)) {
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        $PythonPath = (Resolve-Path -LiteralPath $Candidate).Path
        break
    }
}
if (-not $PythonPath) {
    throw "Python virtual environment not found beside the version folder or workspace root."
}

$env:PYTHONPATH = "backend;mock_backend\src;simulation_edge\src;packages\cloudlabs\src;packages\cloudlabs_edge_dev\src"
$env:CLOUDLABS_SOLO = "1"

if (-not $env:UVICORN_ACCESS_LOG) {
    $env:UVICORN_ACCESS_LOG = "0"
}

& $PythonPath "backend\main.py"
exit $LASTEXITCODE

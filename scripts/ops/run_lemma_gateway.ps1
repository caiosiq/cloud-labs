param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8787,
    [string]$CoordinatorUrl = "http://127.0.0.1:8000"
)

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

$env:CLOUDLABS_LEMMA_COORDINATOR_URL = $CoordinatorUrl

Write-Host "Lemma gateway: http://127.0.0.1:$Port"
Write-Host "Cloud Labs coordinator: $CoordinatorUrl"
Write-Host "Backend lock: sim.default (simulation only)"
if ($env:CLOUDLABS_LEMMA_API_TOKEN) {
    Write-Host "Local bearer-token check: enabled"
} else {
    Write-Host "Local bearer-token check: disabled (use Cloudflare Access before tunnelling)"
}

& $PythonPath -m uvicorn lemma_gateway.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE

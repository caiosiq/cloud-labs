param(
    [switch]$Deep,
    [switch]$RequireObserver,
    [switch]$Json,
    [switch]$Html,
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$MoveItUrl = "http://127.0.0.1:8765",
    [string]$EdgeUrl = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location -LiteralPath $RepoRoot

$WorkspaceRoot = Resolve-Path (Join-Path $RepoRoot "..")
$VenvPython = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }

$argsList = @(
    "scripts\ops\debug_status.py",
    "--backend-url", $BackendUrl,
    "--moveit-url", $MoveItUrl
)

if ($EdgeUrl.Trim()) {
    $argsList += @("--edge-url", $EdgeUrl.Trim())
}
if ($Deep) {
    $argsList += "--deep"
}
if ($RequireObserver) {
    $argsList += "--require-observer"
}
if ($Json) {
    $argsList += "--json"
}
if ($Html) {
    $argsList += "--html"
}

& $Python @argsList
exit $LASTEXITCODE

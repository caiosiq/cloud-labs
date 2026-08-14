param(
    [double]$CarryZMm = 550.0,
    [Nullable[double]]$StartRadiusMm = $null,
    [double]$StartThetaDeg = 0.0,
    [string]$HeldTag = "tag_18"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location -LiteralPath $RepoRoot
$env:PYTHONPATH = "backend;simulation_edge\src;packages\cloudlabs_edge_dev\src"

$Arguments = @(
    "scripts\ops\inspect_radial_carry_poses.py",
    "--carry-z-mm", $CarryZMm,
    "--start-theta-deg", $StartThetaDeg,
    "--held-tag", $HeldTag
)
if ($PSBoundParameters.ContainsKey("StartRadiusMm")) {
    $Arguments += @("--start-radius-mm", [double]$StartRadiusMm)
}

& "..\.venv\Scripts\python.exe" @Arguments
exit $LASTEXITCODE

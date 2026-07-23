param(
    [switch]$Viewer,
    [ValidateRange(0.25, 1.0)]
    [double]$Speed = 1.0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location -LiteralPath $RepoRoot

function Format-EnvFloat([double]$Value) {
    return $Value.ToString("0.###", [System.Globalization.CultureInfo]::InvariantCulture)
}

$env:PYTHONPATH = "backend;simulation_edge\src;packages\cloudlabs_edge_dev\src"
$env:SIMULATION_EDGE_MUJOCO = "1"
$env:SIMULATION_EDGE_PLANNER = "radial"
$env:CLOUDLAB_SIM_PROFILE = "optical_housings"
$env:CLOUDLAB_MUJOCO_VIEWER = if ($Viewer) { "1" } else { "0" }
$env:CLOUDLAB_MUJOCO_REALTIME = if ($Speed -lt 1.0) { "1" } else { "0" }
$env:CLOUDLAB_MUJOCO_VIEWER_SYNC_HZ = "60"
$env:CLOUDLAB_MOTION_TIME_SCALE = Format-EnvFloat (0.5 / $Speed)

& "..\.venv\Scripts\python.exe" -m simulation_edge --port 8120
exit $LASTEXITCODE

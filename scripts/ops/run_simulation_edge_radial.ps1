param(
    [switch]$Viewer,
    [ValidateRange(0.25, 1.0)]
    [Nullable[double]]$Speed = $null,
    [ValidateRange(1.0, 80.0)]
    [double]$JointSpeedDegPerSec = 30.0,
    [ValidateRange(0.1, 32.0)]
    [double]$PlaybackRate = 1.0,
    [ValidateRange(1.0, 120.0)]
    [double]$ViewerFps = 30.0,
    [ValidateSet("noninverted", "original")]
    [string]$Library = "noninverted",
    [ValidateRange(1, 65535)]
    [int]$Port = 8120
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

function Format-EnvFloat([double]$Value) {
    return $Value.ToString("0.###", [System.Globalization.CultureInfo]::InvariantCulture)
}

$env:PYTHONPATH = "backend;simulation_edge\src;packages\cloudlabs_edge_dev\src"
$env:SIMULATION_EDGE_MUJOCO = "1"
$env:SIMULATION_EDGE_PLANNER = "radial"
$env:CLOUDLAB_SIM_PROFILE = "optical_housings"
$LibraryFileName = if ($Library -eq "noninverted") {
    "optical_housings_noninverted.json"
} else {
    "optical_housings.json"
}
$LibraryPath = Join-Path $RepoRoot (
    "simulation_edge\radial_motion_libraries\" + $LibraryFileName
)
if (-not (Test-Path -LiteralPath $LibraryPath -PathType Leaf)) {
    throw "Radial motion library not found: $LibraryPath"
}
$env:CLOUDLAB_RADIAL_LIBRARY_FILE = (
    Resolve-Path -LiteralPath $LibraryPath
).Path
$RequestedJointSpeed = $JointSpeedDegPerSec
if ($PSBoundParameters.ContainsKey("Speed")) {
    # Backward compatibility: the old playback scale used 0.5 as the
    # 0.65-rad/s (37.24-deg/s) baseline.
    $RequestedJointSpeed = 2.0 * (0.65 * 180.0 / [Math]::PI) * [double]$Speed
    Write-Warning (
        "-Speed is deprecated; use -JointSpeedDegPerSec " +
        (Format-EnvFloat $RequestedJointSpeed) + " for the same radial rate."
    )
}
$env:CLOUDLAB_RADIAL_JOINT_SPEED_DEG_PER_S = Format-EnvFloat $RequestedJointSpeed
$env:CLOUDLAB_MUJOCO_PLAYBACK_RATE = Format-EnvFloat $PlaybackRate
$env:CLOUDLAB_MUJOCO_VIEWER = if ($Viewer) { "1" } else { "0" }
$env:CLOUDLAB_MUJOCO_REALTIME = "1"
$env:CLOUDLAB_MUJOCO_VIEWER_SYNC_HZ = Format-EnvFloat $ViewerFps
$env:CLOUDLAB_MOTION_TIME_SCALE = "1"

Write-Host "MuJoCo radial library: $env:CLOUDLAB_RADIAL_LIBRARY_FILE"
Write-Host "Radial joint speed target: $env:CLOUDLAB_RADIAL_JOINT_SPEED_DEG_PER_S deg/s"
Write-Host "MuJoCo playback rate: $env:CLOUDLAB_MUJOCO_PLAYBACK_RATE x"
Write-Host "MuJoCo viewer target: $env:CLOUDLAB_MUJOCO_VIEWER_SYNC_HZ FPS"
Write-Host "Simulation edge: http://127.0.0.1:$Port"

& $PythonPath -m simulation_edge --port $Port
exit $LASTEXITCODE

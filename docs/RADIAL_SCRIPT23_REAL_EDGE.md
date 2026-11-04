# Script23 real-edge integration

Maintained on josh/radial-cloudlabs-integration in both the cloud-labs and
robot-deathray repositories. The working copy used for integration is
josh_robotic_twin_simulation_radial. The original workspace and its legacy
motion implementation are preserved.

## Motion boundary

CloudLabs command -> backend primitive execution -> real edge dispatch ->
adapters.motion.move_component -> managers.radial_motion.service ->
managers.radial_motion.engine and its private source planner.

STORE_COMPONENT, PLACE_FROM_STORAGE, REPACK_STORAGE and RECENTER_IN_STORAGE
already delegate to move_component and inherit Script23. Existing slot
allocation, footprint checks and successful-placement inventory commits stay
in place. Failure does not commit a storage transition or try legacy motion.

OPTIMIZE's invasive pose router passes the internal _legacy_only=True keyword
to move_component. Request JSON cannot override it. Motor optimization, held
motion, pick/hover/place-from-hover, teleoperation and scan-rotation retain
their existing implementations. No shared experiment, vision-manager,
live-controller or xArm-driver class was patched globally.

The production package ports Script23 behavior as ordinary Python source:
directed tag-top grasp, frozen Script24/25 model, radius-scaled radial library,
fresh wrist alignment/depth and pickup TCP Z, preserved arrival orientation
during placement, release, retreat and home. Source pose/yaw provenance and
public clockwise tag-top angles are converted at their established boundary;
public state retains the requested table angle.

Every speed, acceleration, jerk, blend, gripper, vision and pickup-height setting
matches the recent Script27/23 calibration-run command by regression test.
Placement/transition/contact/retreat speeds are 40/20/10/80 mm/s. Placement
acceleration is 100 mm/s²; pickup/retreat acceleration remains 600 mm/s².
Closing position is 510, and dedicated empty-gripper wrist alignment is 60 deg/s.

The shared edge experiment, robot driver and RealSense are borrowed. No new
hardware session or standalone startup stereo scan is introduced. Fresh edge
localization remains available through Refresh Pose, and close-camera feedback
runs for every pickup. Lifecycle hooks and temporary driver guards are restored
on success/failure; per-move height/model/planner state and environment are
cleared. Each move logs its request, model snapshot/SHA256, plan, timeline and
failure traceback under lab_automation/cloudlabs_edge/.radial_move_runs.

The launcher uses this copy's Python and pins its CloudLabs/mapping/log paths
to this workspace. It retains the existing default of skipping MuJoCo collision
checks. Controller, kinematic, endpoint, height, orientation, wrist and J1 cord
guards remain. -SkipMuJoCoChecks:$false enables collision checks. The
-MoveComponentPath legacy option retains legacy MOVE_COMPONENT as a rollback.

The production move no longer depends on Script23, its preserved bytecode, or
the CloudLabs simulation package. It owns its planner, state/catalog readers,
robot meshes, profiles, mapping, library and frozen pickup model inside
lab_automation. Historical private symbols/log names remain for trace
comparison. Standalone experiment scripts are unchanged. The internal
background worker starts managers.radial_motion.worker as a Python module.

Offline tests compare reference/native joint waypoints and Cartesian command
sequences, fault gates, calibration inference, directed grasp and placement
conventions. A subprocess blocks scripts-folder/CloudLabs reads and still
builds a native plan. No hardware validation was performed for this source port.

## He-Ne reference in the real UI

The orange he_ne overlay combines the held-camera fit
radial_motion_hene_tag22_calibration.json with the latest placed-camera
correction radial_motion_hene_tag22_placed_calibration.json (Script29 dataset).
Source identities, orientation, baseline snapshot and interpolation policy are
validated by the existing loaders. SHA256 provenance is included in the overlay.

For each original robot Y station:
1. Evaluate robot X from the held line and add the placed robot-X correction.
2. Apply the inverse of the saved table-to-robot transform once.
3. Draw the resulting seven table-XY points as a finite piecewise-linear path.

No TCP-to-detector offset, absolute optical axis, fixed absolute Z or new fit is
introduced. The path is specific to tag_22, camera 054032723075, tag rotation
-90 degrees and grip 510. Z during motion still comes from each fresh pickup.

| Point | Original robot TCP Y (mm) | CloudLabs table X (mm) | CloudLabs table Y (mm) |
| --- | --- | --- | --- |
| 1 | -300 | 367.040099 | -290.342849 |
| 2 | -225 | 367.397792 | -215.306805 |
| 3 | -150 | 366.093345 | -140.314975 |
| 4 | -75 | 364.134115 | -65.340561 |
| 5 | 0 | 362.300805 | 9.637201 |
| 6 | 75 | 360.530455 | 84.616639 |
| 7 | 150 | 358.520858 | 159.589712 |

Fractional positions interpolate on the exact transformed segments. The
path never extrapolates beyond the measured range. The diode remains red.
The calibrated path replaces the historical orange He-Ne geometry and the
separate blue overlay, and is the default He-Ne alignment reference. Drag
snapping follows all six finite segments continuously, for any component,
without treating calibration knots as intersection locks. Legacy scalar
coefficient consumers never approximate this path by its two endpoints.
The two-point edit modal cannot
overwrite calibration geometry, but normal visibility toggles remain available.

The explicit command console supports:

    movelaser tag_22 <TABLE_Y_MM> -90 he_ne
    placelaser tag_22 <TABLE_Y_MM> -90 he_ne

The second command is for a stored component. TABLE_Y_MM is the displayed
table coordinate, not the original robot-Y station number. The table alignment
reference accepts all component tags and retains the requested rotation;
the underlying data was calibrated using tag_22 at -90 degrees and grip 510.
The finite range is checked. Targets already contain the placed correction; the
motion adapter does not apply it a second time.

The real.default seed, local laser-lines file, working lab_state overlay and
session checkpoint are updated. Old three-line sessions migrate to the two-line display. Mock/simulation overlays, existing VC commits,
the legacy laser_line_fit.npy and experiment laser registries are unchanged.
Polyline points/provenance survive configuration capture and VC restoration;
interior-point edits are detected by configuration diffing.

To re-import a later calibration, stop the new backend and run from this
workspace root:

    .\.venv\Scripts\python.exe .\cloud-labs-radial-merge\scripts\ops\import_radial_hene_calibration.py --install

The importer is idempotent, preserves other state/lines and never changes
existing VC history. Without --install it only prints the proposed overlay.

## Branch synchronization and verification (2026-11-04)

Cloud Labs includes commit 03e211da77e2e2d7ec2e3bdb01cc2fc6d4039f61
from josh/radial-motion-library-updated_8-30-2026. Both overlapping backend
files merged automatically. The simulation_edge, lemma_gateway and
packages/cloudlabs trees match that source branch exactly. Native real motion
continues to use Lab Automation's separately owned, validated planner.

Matching Lab Automation revision:
360fccda429af2e450f5935f577154b83a29d1e2, on the same integration branch name.
It includes the native runtime, frozen models, required robot assets and
calibration measurement records. Git attributes preserve byte-hashed assets
across checkout platforms. The portable reference tests compare measurement
identities and hashes without requiring the calibration computer's absolute
directory, and supply an explicit historical laser fixture.

Offline checks for this synchronization:

- 171 focused backend/gateway checks passed (plus 22 subtests): command
  outcomes, atomic persistence, state, overlays, queueing and edge clients.
- 40 MuJoCo safety/motion checks passed (plus 29 subtests).
- 60 native motion, calibration, differential parity and storage/optimization
  routing checks passed, including from committed source snapshots.
- 308 standalone calibration/reference checks passed (plus 6 subtests).
- 7 calibrated laser UI checks passed.
- All 59 frozen native assets matched their recorded SHA256 after export.

The broad backend/gateway comparison used clean snapshots of 03e211d and the
combined branch. It found the same 39 failing test identities in both, with
no additional failures in the combined branch. The baseline had 606 passed
and 2 skipped; the combined branch had 612 passed and 1 skipped. Existing
failures include old fixture paths, catalog assumptions, missing test kernels
and Windows snapshot-path limits; the full broad suite is not green.

The user reported testing the native integration before this synchronization.
This merge validation used offline tests; it did not start a server, camera
or physical robot. Restart existing backend/edge processes to load the new
source.

## Switching an existing Cloud Labs clone

Save any local edits first. Once the branch is available on GitHub, run from
the existing Cloud Labs checkout:

```powershell
git fetch origin
git switch --track origin/josh/radial-cloudlabs-integration
```

For later updates while on the integration branch, use `git pull --ff-only`.
An existing clone reuses its Git objects; a full re-download is unnecessary.
Cloud Labs simulation remains in this repository. Physical real-edge motion
requires the matching Lab Automation integration revision above.

# Script23 real-edge integration

Implemented only in josh_robotic_twin_simulation_radial, on
josh/radial-cloudlabs-integration in both repositories. The original workspace,
its branches and legacy motion implementation are preserved. No commit or push
was made for this integration. No server, camera or physical robot was started.

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

## Verification

All checks were offline; physical placement accuracy remains to be tested.

- 72 focused Lab Automation tests passed: production adapter, repeated session
  success/failure cleanup, storage success/refusal bookkeeping, legacy routing,
  optimization, frames and held-placement regression cases.
- The production service built actual headless Script23 plans for tag_22 and
  a lens, including moves into and out of the configured storage region.
- Existing Script23 model/grasp/height/FK/joint-limit/home tests passed.
- 20 backend tests passed: calibration export, frame transform, preserved
  legacy coefficients, idempotent import, interior-point VC diffs and existing
  line-versioning/geometry cases.
- 3 Node tests passed: exact six-segment geometry and 61 fractional stations,
  camera/orientation/range checks, unchanged legacy selection and drag snapping.
- Changed JavaScript syntax and Git patch whitespace checks passed.
- Scripts23/24/25, legacy experiment/vision/live-control/xArm-driver modules,
  and the old Script18 production adapter match the original files byte-for-byte.

A separate broader run found one pre-existing failure:
test_cloudlabs_script18_move_service.py::
CloudLabsScript18MoveServiceTests::
test_production_runtime_uses_script21_fixed_normal_placement.
It expects Script21's fixed-normal function, while the current Script21 file
uses its preserved placement function. The exact test also fails in the
untouched original workspace. It was not changed to hide that mismatch and
does not exercise the new Script23 adapter.

Restart the backend and real edge from this new workspace to use these
changes. No hardware validation or alignment guarantee is claimed by the
offline checks.

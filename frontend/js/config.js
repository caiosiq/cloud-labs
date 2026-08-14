/** Canvas and lab geometry (mm ↔ px mapping). Values are set from GET /api/lab-layout at boot. */
export const CANVAS_WIDTH = 1000;
export const CANVAS_HEIGHT = 700;

export let LAB_X_MIN = -500;
export let LAB_X_MAX = 500;
export let LAB_Y_MIN = -500;
export let LAB_Y_MAX = 500;

/** Robot base / keep-out circle radius in lab mm (same frame as component centers). */
export let DANGER_RADIUS_MM = 90;

/** Clearance reserved inside each visible lab-frame boundary. */
export let FRAME_SAFETY_CLEARANCE_MM = 0;
export let FRAME_SAFETY_MIN_WIDTH_MM = 0;
export let FRAME_SAFETY_MIN_HEIGHT_MM = 0;
export let MANUAL_MOTION_CORNER_CUTOFF_MM = 0;

export let LAB_WIDTH_MM = LAB_X_MAX - LAB_X_MIN;
export let LAB_HEIGHT_MM = LAB_Y_MAX - LAB_Y_MIN;
export let LAB_SCALE = Math.min(CANVAS_WIDTH / LAB_WIDTH_MM, CANVAS_HEIGHT / LAB_HEIGHT_MM);
export let LAB_CENTER_PX = { x: CANVAS_WIDTH / 2, y: CANVAS_HEIGHT / 2 };

export const QUARTER_INCH_MM = 25.4 / 4;
export let BREADBOARD_GRID_FINE_TUNE_X_MM = -1.05;

/** Total X offset (mm) aligning visual grid to calibrated lab frame; refreshed from layout. */
export let BREADBOARD_GRID_OFFSET_X_MM = -QUARTER_INCH_MM + BREADBOARD_GRID_FINE_TUNE_X_MM;

/** Optional Y offset from layout.breadboard.origin_offset_mm */
export let BREADBOARD_GRID_OFFSET_Y_MM = 0;

/** Centered grid step in mm (default 25 mm). */
export let BREADBOARD_GRID_SPACING_MM = 25;

/** Explicit inventory rectangle edges in lab mm; from `/api/lab-layout` -> `storage_grid.q3`. */
export let STORAGE_RECT_X_MIN = -500;
export let STORAGE_RECT_X_MAX = 0;
export let STORAGE_RECT_Y_MIN = -500;
export let STORAGE_RECT_Y_MAX = 0;

/**
 * Background Twin lab-state poll (ms) while IDLE.
 *
 * Historically 500ms (~2Hz) so the canvas felt live; that proxies every tick to
 * the edge ``GET /lab-state`` and floods the real-edge uvicorn access log.
 * Commands / VC / tunable reads already call ``fetchLabState()`` on demand.
 * While BUSY/HOLDING/OPTIMIZING the poller speeds up (see lab-state.js).
 */
export const POLLING_INTERVAL = 10000;

/** Faster poll while a southbound primitive is in flight (status badge / dirty). */
export const BUSY_POLLING_INTERVAL = 1000;

/**
 * Minimum pause (ms) the reconcile plan runner holds between primitive steps
 * when applying a configuration / stashing / popping, so each step is visible
 * (component highlight + System Monitor line) instead of blurring together.
 * The mock already adds ~2s of hardware latency per move; this is purely the
 * inter-step breather. Set to 0 to disable pacing.
 */
export const RECONCILE_STEP_DELAY_MS = 600;

/** How often the plan runner polls lab-state while waiting for a step to finish. */
export const RECONCILE_POLL_INTERVAL_MS = 200;

/** Max time (ms) to wait for a single primitive step to leave BUSY before giving up. */
export const RECONCILE_STEP_TIMEOUT_MS = 30000;

function recomputeDerived() {
    LAB_WIDTH_MM = LAB_X_MAX - LAB_X_MIN;
    LAB_HEIGHT_MM = LAB_Y_MAX - LAB_Y_MIN;
    LAB_SCALE = Math.min(CANVAS_WIDTH / LAB_WIDTH_MM, CANVAS_HEIGHT / LAB_HEIGHT_MM);
    LAB_CENTER_PX = { x: CANVAS_WIDTH / 2, y: CANVAS_HEIGHT / 2 };
}

/**
 * @param {Record<string, unknown>} payload — body of GET /api/lab-layout (layout.json + enrichment)
 */
export function applyLabLayoutFromApiDoc(payload) {
    const b = payload.lab_bounds_mm;
    if (!b || typeof b !== 'object') {
        throw new Error('lab-layout: missing lab_bounds_mm');
    }
    LAB_X_MIN = Number(b.x_min);
    LAB_X_MAX = Number(b.x_max);
    LAB_Y_MIN = Number(b.y_min);
    LAB_Y_MAX = Number(b.y_max);
    const dz = payload.danger_zone;
    if (dz && typeof dz === 'object' && Number.isFinite(Number(dz.radius_mm))) {
        DANGER_RADIUS_MM = Number(dz.radius_mm);
    }
    FRAME_SAFETY_CLEARANCE_MM = 0;
    FRAME_SAFETY_MIN_WIDTH_MM = 0;
    FRAME_SAFETY_MIN_HEIGHT_MM = 0;
    MANUAL_MOTION_CORNER_CUTOFF_MM = 0;
    const frameSafety = payload.frame_safety;
    if (frameSafety && typeof frameSafety === 'object') {
        if (Number.isFinite(Number(frameSafety.clearance_mm))) {
            FRAME_SAFETY_CLEARANCE_MM = Math.max(0, Number(frameSafety.clearance_mm));
        }
        const minimum = frameSafety.minimum_component_footprint_mm;
        if (minimum && typeof minimum === 'object') {
            if (Number.isFinite(Number(minimum.width))) {
                FRAME_SAFETY_MIN_WIDTH_MM = Math.max(0, Number(minimum.width));
            }
            if (Number.isFinite(Number(minimum.height))) {
                FRAME_SAFETY_MIN_HEIGHT_MM = Math.max(0, Number(minimum.height));
            }
        }
    }
    const manualWorkspace = payload.manual_motion_workspace;
    if (manualWorkspace && typeof manualWorkspace === 'object') {
        const cornerCutoff = manualWorkspace.corner_cutoff_mm
            ?? manualWorkspace.half_extent_mm;
        if (Number.isFinite(Number(cornerCutoff))) {
            MANUAL_MOTION_CORNER_CUTOFF_MM = Math.max(
                0,
                Number(cornerCutoff),
            );
        }
    }
    const br = payload.breadboard;
    if (br && typeof br === 'object') {
        if (Number.isFinite(Number(br.grid_spacing_mm))) {
            BREADBOARD_GRID_SPACING_MM = Number(br.grid_spacing_mm);
        }
        const off = br.origin_offset_mm;
        if (off && typeof off === 'object') {
            if (Number.isFinite(Number(off.x))) {
                BREADBOARD_GRID_OFFSET_X_MM = Number(off.x);
            }
            if (Number.isFinite(Number(off.y))) {
                BREADBOARD_GRID_OFFSET_Y_MM = Number(off.y);
            }
        }
        if (
            typeof br.fine_tune_x_mm === 'number' ||
            typeof br.grid_spacing_mm === 'number'
        ) {
            BREADBOARD_GRID_FINE_TUNE_X_MM = Number.isFinite(Number(br.fine_tune_x_mm))
                ? Number(br.fine_tune_x_mm)
                : BREADBOARD_GRID_FINE_TUNE_X_MM;
        }
    }
    let srx = LAB_X_MIN;
    let srxx = 0;
    let sry = LAB_Y_MIN;
    let sryy = 0;
    // Prefer edge ``storage.bounds_mm`` (SoT on the layout document). Fall back to
    // enriched ``storage_grid.q3`` which used to lag when process geometry was
    // still bound to another backend.
    const st = payload.storage;
    const bounds = st && typeof st === 'object' ? st.bounds_mm : null;
    if (bounds && typeof bounds === 'object') {
        if (Number.isFinite(Number(bounds.x_min))) srx = Number(bounds.x_min);
        if (Number.isFinite(Number(bounds.x_max))) srxx = Number(bounds.x_max);
        if (Number.isFinite(Number(bounds.y_min))) sry = Number(bounds.y_min);
        if (Number.isFinite(Number(bounds.y_max))) sryy = Number(bounds.y_max);
    } else {
        const sg = payload.storage_grid;
        if (sg && typeof sg === 'object') {
            const q = sg.q3;
            if (q && typeof q === 'object') {
                if (Number.isFinite(Number(q.x_min))) srx = Number(q.x_min);
                if (Number.isFinite(Number(q.x_max))) srxx = Number(q.x_max);
                if (Number.isFinite(Number(q.y_min))) sry = Number(q.y_min);
                if (Number.isFinite(Number(q.y_max))) sryy = Number(q.y_max);
            }
        }
    }
    STORAGE_RECT_X_MIN = srx;
    STORAGE_RECT_X_MAX = srxx;
    STORAGE_RECT_Y_MIN = sry;
    STORAGE_RECT_Y_MAX = sryy;
    recomputeDerived();
}

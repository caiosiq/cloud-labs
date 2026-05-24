import { store } from './state/store.js';
import { getStatecontrol, normalizeCapabilities, tunableValue } from './component-state.js';

/**
 * Client-side accessors for tunables vs measurables.
 *
 * See `universal_component_architecture.md` §7-8 for the canvas-truth model:
 * the canvas draws `tunables.nominal_pose` (intent), and `measurables` are
 * off-canvas receipts (encoder readback, camera_image, optimization score).
 *
 * ## Pose intent editing (three surfaces)
 *
 * Operators edit `nominal_pose` intent through three UI surfaces. All editors
 * share `store.ghostState[tag]` as canvas-truth until a primitive commits:
 *
 * | Surface | Module | Role |
 * |---------|--------|------|
 * | Canvas ghost | `canvas/interaction.js` | Drag to translate; wheel to rotate. |
 * | Context X/Y/Rot | `ui/context-panel.js` (`#ctx-x`, `#ctx-y`, `#ctx-rot`) | Numeric entry + Move → `MOVE_COMPONENT`. |
 * | TablePose widget | `widgets/table-pose.js` | **Read-only** receipt of committed tunable in the capability panel. |
 *
 * TeleOp v2 adds LIVE/TARGET layers (`teleopLivePose`, `teleopTarget`) for
 * session driving; they do not replace ghost intent for normal table moves.
 * During held-object TeleOp, canvas drag writes `teleopTarget` instead of
 * `ghostState` (see `interaction.js`).
 *
 * - `drawPose(c)` — canvas-truth pose for drawing, collision, ghost init.
 *   Prefers `tunables.nominal_pose`; falls back to `measurables.pose` for
 *   legacy state files.
 * - `nominalPose(c)` — strict tunables lookup (intent semantics).
 * - `measPose(c)` — strict measurables.pose (encoder readback only; never
 *   use as an editor source).
 */

export const PRESENCE_BREADBOARD = 'breadboard';
export const PRESENCE_STORAGE = 'storage';
export const PRESENCE_OFF_TABLE = 'off_table';

/**
 * Strict `measurables.pose` accessor. **Not** the canvas-truth pose; see
 * `drawPose` for that. Reserved for off-canvas readouts that genuinely
 * need the hardware-reported value (e.g. motor encoder rotations).
 * @param {object | undefined} c
 */
export function measPose(c) {
    return getStatecontrol(c).measurables?.pose || {};
}

export function nominalPose(c) {
    const n = getStatecontrol(c).tunables?.nominal_pose;
    return n && typeof n === 'object' ? n : {};
}

/** Committed motor setpoints (tunables intent), keyed by motor id string. */
export function nominalMotorPositions(c) {
    const nmp = tunableValue(c, 'nominal_motor_positions');
    return nmp && typeof nmp === 'object' ? nmp : {};
}

/**
 * Canvas-truth pose: `tunables.nominal_pose` with a defensive fallback to
 * `measurables.pose` for state files that haven't been migrated to the
 * tunables-only canvas model yet. Use this everywhere the canvas, ghost
 * state, layout validation, or collision detection needs "where the user
 * intends this component to sit".
 * @param {object | undefined} c
 */
export function drawPose(c) {
    const np = nominalPose(c);
    if (np && (typeof np.x === 'number' || typeof np.y === 'number')) return np;
    return measPose(c);
}

/** @param {object | undefined} c */
export function componentPresence(c) {
    const p = getStatecontrol(c).tunables?.presence;
    if (p === PRESENCE_STORAGE || p === PRESENCE_OFF_TABLE || p === PRESENCE_BREADBOARD) return p;
    return PRESENCE_BREADBOARD;
}

/** Stored in inventory Q3 intent (storage presence or in_storage flag). */
export function isStoredComponent(c) {
    if (!c) return false;
    if (componentPresence(c) === PRESENCE_STORAGE) return true;
    const s = getStatecontrol(c).tunables?.storage;
    return !!(s && s.in_storage);
}

/** On physical table layout (breadboard or storage), not off-table inventory. */
export function isOnTableComponent(c) {
    const p = componentPresence(c);
    return p === PRESENCE_BREADBOARD || p === PRESENCE_STORAGE;
}

/**
 * Catalog row for a tag (``store.catalogMap`` is keyed by ``tag_id``).
 * @param {string} tagId
 * @returns {object | null}
 */
export function getCatalogRow(tagId) {
    if (!tagId || !store?.catalogMap) return null;
    return store.catalogMap[tagId] || store.catalogMap[tagId.replace(/^tag_/, '')] || null;
}

/** Declares ``tunables.nominal_pose`` (TablePose) — drawable / draggable on canvas. */
export function catalogDeclaresTablePose(tagId) {
    const row = getCatalogRow(tagId);
    const caps = normalizeCapabilities(row?.capabilities);
    return Object.prototype.hasOwnProperty.call(caps.statecontrol.tunables || {}, 'nominal_pose');
}

export function isChromeComponent(tagId) {
    const row = getCatalogRow(tagId);
    const caps = normalizeCapabilities(row?.capabilities);
    const tun = caps.statecontrol.tunables || {};
    const keys = Object.keys(tun);
    if (!keys.length) return false;
    return !catalogDeclaresTablePose(tagId);
}

/** Whether this in-lab component should be drawn on the optical table canvas. */
export function shouldRenderOnCanvas(tagId, comp) {
    return isOnTableComponent(comp) && catalogDeclaresTablePose(tagId);
}

/** Chrome-bar tags currently in lab state. */
export function listChromeComponentTags(labState) {
    if (!labState || !labState.components) return [];
    return Object.keys(labState.components).filter((tagId) => {
        const comp = labState.components[tagId];
        return comp && isChromeComponent(tagId);
    });
}

export function isBreadboardIntent(c) {
    return componentPresence(c) === PRESENCE_BREADBOARD;
}

export function isOffTableComponent(c) {
    return componentPresence(c) === PRESENCE_OFF_TABLE;
}

/** Optimization outcome: strategy mode + numeric score (no legacy is_optimized). */
export function hasOptimizationOutcome(c) {
    const m = getStatecontrol(c).measurables;
    return !!(m && m.last_optimization_score != null && Number.isFinite(Number(m.last_optimization_score)));
}

export function placementMode(c) {
    const pl = getStatecontrol(c).tunables?.placement;
    const m = pl && pl.mode;
    return (typeof m === 'string' && m) ? m.toUpperCase() : 'MANUAL';
}

/**
 * Set of ``placement.mode`` values that are *not* the result of an
 * optimization strategy. Anything outside this set (e.g. ``COBYLA``,
 * ``NEWTON``, ``GRID_SCAN``, ...) is treated as an optimization-sourced
 * placement so the sidebar dot can light up gold-on-green.
 */
export const NON_OPTIMIZATION_PLACEMENT_MODES = new Set([
    'MANUAL',
    'STORAGE',
    'HOVER',
    'PICK',
]);

/**
 * True iff the component's *current* placement came from an optimization
 * run, i.e. it has a finite ``last_optimization_score`` AND ``placement.mode``
 * is a strategy name (not MANUAL / STORAGE / HOVER / PICK).
 *
 * This intentionally excludes parts that were optimized earlier but then
 * manually re-moved or picked up — "optimized" should describe the *pose
 * the component is currently sitting at*, not its whole history.
 * @param {object | undefined} c
 */
export function isOptimizedPlacement(c) {
    if (!hasOptimizationOutcome(c)) return false;
    return !NON_OPTIMIZATION_PLACEMENT_MODES.has(placementMode(c));
}

// --- HOLDING-state helpers (see new_primitives.md) ---------------------------

export const SYSTEM_STATUS_HOLDING = 'HOLDING';

/**
 * Normalized ``holding`` block from lab state. Always returns an object with
 * ``tag_id``, ``nominal_pose``, ``requires_operator_confirm`` so callers can
 * use optional chaining / destructuring without fallbacks.
 * @param {object | null | undefined} labState
 */
export function getHolding(labState) {
    const h = labState && labState.holding;
    return {
        tag_id: (h && typeof h.tag_id === 'string' && h.tag_id) || null,
        nominal_pose: (h && h.nominal_pose && typeof h.nominal_pose === 'object') ? h.nominal_pose : null,
        requires_operator_confirm: !!(h && h.requires_operator_confirm),
    };
}

/** ``true`` when the lab reports ``system_status === "HOLDING"``. */
export function isHoldingState(labState) {
    return !!(labState && labState.system_status === SYSTEM_STATUS_HOLDING);
}

/** ``true`` when the given tag is the one currently in the gripper. */
export function isHeldTag(tagId, labState) {
    if (!isHoldingState(labState)) return false;
    const h = getHolding(labState);
    return !!(tagId && h.tag_id === tagId);
}

/** ``true`` when HOLDING but tag is unknown / unconfirmed (UI must lock). */
export function isHoldingUnconfirmed(labState) {
    if (!isHoldingState(labState)) return false;
    return getHolding(labState).requires_operator_confirm;
}

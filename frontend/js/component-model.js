/**
 * Client-side accessors for tunables vs measurables.
 *
 * See `universal_component_architecture.md` §7-8 for the canvas-truth model:
 * the canvas draws `tunables.nominal_pose` (intent), and `measurables` are
 * off-canvas receipts (encoder readback, camera_image, optimization score).
 *
 * - `drawPose(c)` — the pose for canvas drawing, collision, ghost init,
 *   storage validation, and command-revert logic. Prefers
 *   `tunables.nominal_pose`; falls back to `measurables.pose` defensively
 *   for legacy state files that may predate the universal-component
 *   migration.
 * - `nominalPose(c)` — strict tunables.nominal_pose lookup, no fallback.
 *   Use when "intent" semantics specifically matter and a missing nominal
 *   should be visible to the caller.
 * - `measPose(c)` — strict measurables.pose lookup. Use *only* for
 *   legitimate measurable consumers (encoder readback in
 *   `ui/context-panel.js`). Do not use for canvas drawing.
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
    return (c && c.measurables && c.measurables.pose) || {};
}

/**
 * Strict `tunables.nominal_pose` accessor. Returns `{}` if missing.
 * @param {object | undefined} c
 */
export function nominalPose(c) {
    const n = c && c.tunables && c.tunables.nominal_pose;
    return n && typeof n === 'object' ? n : {};
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
    const p = c && c.tunables && c.tunables.presence;
    if (p === PRESENCE_STORAGE || p === PRESENCE_OFF_TABLE || p === PRESENCE_BREADBOARD) return p;
    return PRESENCE_BREADBOARD;
}

/** Stored in inventory Q3 intent (storage presence or in_storage flag). */
export function isStoredComponent(c) {
    if (!c || !c.tunables) return false;
    if (componentPresence(c) === PRESENCE_STORAGE) return true;
    const s = c.tunables.storage;
    return !!(s && s.in_storage);
}

/** On physical table layout (breadboard or storage), not off-table inventory. */
export function isOnTableComponent(c) {
    const p = componentPresence(c);
    return p === PRESENCE_BREADBOARD || p === PRESENCE_STORAGE;
}

export function isBreadboardIntent(c) {
    return componentPresence(c) === PRESENCE_BREADBOARD;
}

export function isOffTableComponent(c) {
    return componentPresence(c) === PRESENCE_OFF_TABLE;
}

/** Optimization outcome: strategy mode + numeric score (no legacy is_optimized). */
export function hasOptimizationOutcome(c) {
    const m = c && c.measurables;
    return !!(m && m.last_optimization_score != null && Number.isFinite(Number(m.last_optimization_score)));
}

/**
 * Raw ``tunables.placement.mode`` for a component, upper-cased. Defaults to
 * ``MANUAL`` when unset / malformed so callers never have to guard.
 * @param {object | undefined} c
 */
export function placementMode(c) {
    const pl = c && c.tunables && c.tunables.placement;
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

import { getPreviewOptimizationEntry, isConfigViewMode } from './control/control-state.js';
import { store } from './state/store.js';
import { getStatecontrol, normalizeCapabilities, tunableValue } from './component-state.js';

/**
 * Client-side accessors for tunables vs measurables.
 *
 * - **Tunables** — commanded intent (`nominal_pose`, motor setpoints) and
 *   **reported** values of the same DOFs (`reported_pose`, encoder angles).
 *   Refreshing pose from the lab updates the reported tunable; it is not a
 *   measurable.
 * - **Measurables** — observations with no 1:1 tunable (camera_image, scores).
 *
 * Canvas draws commanded pose via `drawPose` → `nominal_pose` (ghost). Solid
 * overlays / readouts that need bench-reported pose use `measPose` /
 * `reportedPose` (legacy name kept for call sites).
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
 *   Prefers `tunables.nominal_pose`; falls back to reported pose for legacy.
 * - `nominalPose(c)` — strict commanded pose.
 * - `reportedPose(c)` / `measPose(c)` — bench-reported pose (tunable family).
 */

export const PRESENCE_BREADBOARD = 'breadboard';
export const PRESENCE_STORAGE = 'storage';
export const PRESENCE_OFF_TABLE = 'off_table';

/**
 * Bench-reported pose (`tunables.reported_pose`), with legacy fallback to
 * `measurables.pose`. Not a measurable — same DOF as `nominal_pose`.
 * @param {object | undefined} c
 */
export function reportedPose(c) {
    const sc = getStatecontrol(c);
    const rp = sc.tunables?.reported_pose;
    if (rp && typeof rp === 'object') return rp;
    return sc.measurables?.pose || {};
}

/**
 * @deprecated Prefer {@link reportedPose}. Historical name from when pose
 * lived under measurables.
 * @param {object | undefined} c
 */
export function measPose(c) {
    return reportedPose(c);
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
 * Canvas-truth pose: commanded `tunables.nominal_pose`, with defensive
 * fallback to reported pose for incomplete state files.
 * @param {object | undefined} c
 */
export function drawPose(c) {
    const np = nominalPose(c);
    if (np && (typeof np.x === 'number' || typeof np.y === 'number')) return np;
    return reportedPose(c);
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
    if (!row || catalogDeclaresTablePose(tagId)) return false;
    // Fixed overview camera (table-top): may declare no tunables on purpose.
    const params = row.parameters || row.properties || {};
    if (params.stream_source === 'overhead' || row.id === 'cam_table_top') {
        return true;
    }
    if (params.fixture === true) return true;
    const caps = normalizeCapabilities(row?.capabilities);
    const tun = caps.statecontrol.tunables || {};
    const keys = Object.keys(tun);
    if (!keys.length) return false;
    return true;
}

/** Fixed ceiling / table-overview camera (not a placeable science cam). */
export function isOverviewCamera(tagId) {
    const row = getCatalogRow(tagId);
    if (!row) return false;
    if (row.id === 'cam_table_top') return true;
    const params = row.parameters || row.properties || {};
    return params.stream_source === 'overhead';
}

/** Whether this in-lab component should be drawn on the optical table canvas. */
export function shouldRenderOnCanvas(tagId, comp) {
    const pose = drawPose(comp);
    return (
        isOnTableComponent(comp) &&
        catalogDeclaresTablePose(tagId) &&
        Number.isFinite(pose?.x) &&
        Number.isFinite(pose?.y) &&
        Number.isFinite(pose?.rotation)
    );
}

/** Chrome-bar tags currently in lab state and under operator control. */
export function listChromeComponentTags(labState) {
    if (!labState || !labState.components) return [];
    return Object.keys(labState.components).filter((tagId) => {
        const comp = labState.components[tagId];
        return comp && isChromeComponent(tagId) && isComponentControlled(tagId);
    });
}

export function isBreadboardIntent(c) {
    return componentPresence(c) === PRESENCE_BREADBOARD;
}

export function isOffTableComponent(c) {
    return componentPresence(c) === PRESENCE_OFF_TABLE;
}

/** Part is in the operator-controlled session (runtime + active catalog). */
export function isComponentControlled(tagId, { libraryOnly = false } = {}) {
    if (!tagId || libraryOnly) return false;
    const tags = store.activeCatalogTags;
    if (!Array.isArray(tags)) return false;
    return tags.includes(tagId);
}

/** @deprecated use isComponentControlled */
export function isTrackedTag(tagId) {
    return isComponentControlled(tagId);
}

/** Physical mount / placement hint (not the same as tracked vs controlled). */
export function physicalMountLabel(tagId, comp) {
    if (isChromeComponent(tagId)) return 'Fixed mount';
    if (!comp) return 'In library';
    if (isStoredComponent(comp)) return 'In storage';
    if (isOnTableComponent(comp)) return 'On table';
    return 'Off bench';
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
 * While viewing a configuration commit, uses that node's stored metadata
 * instead of live-bench measurables.
 * @param {object | undefined} c
 * @param {string | undefined} tagId
 */
export function isOptimizedPlacement(c, tagId) {
    return getOptimizationDisplayInfo(c, tagId) != null;
}

/**
 * Strategy + score for sidebar labels (live bench or viewed commit metadata).
 * @param {object | undefined} c
 * @param {string | undefined} tagId
 * @returns {{ mode: string, score: number } | null}
 */
export function getOptimizationDisplayInfo(c, tagId) {
    const id = tagId || (c && c.id);
    if (isConfigViewMode() && id) {
        const preview = getPreviewOptimizationEntry(id);
        if (preview) {
            const score = preview.last_optimization_score;
            const mode = String(preview.placement_mode || 'MANUAL').toUpperCase();
            if (
                score != null
                && Number.isFinite(Number(score))
                && !NON_OPTIMIZATION_PLACEMENT_MODES.has(mode)
            ) {
                return { mode, score: Number(score) };
            }
        }
        return null;
    }
    if (!hasOptimizationOutcome(c)) return null;
    const mode = placementMode(c);
    if (NON_OPTIMIZATION_PLACEMENT_MODES.has(mode)) return null;
    const m = getStatecontrol(c).measurables;
    return { mode, score: Number(m.last_optimization_score) };
}

/** Human-readable optimization label, e.g. ``NEWTON · 0.990``. */
export function formatOptimizationLabel(info) {
    if (!info) return '';
    const score = Number(info.score);
    if (Number.isFinite(score)) {
        return `${info.mode} · ${score.toFixed(3)}`;
    }
    return info.mode;
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

/** ``true`` when the gripper is holding a part (including during TELEOP). */
export function isHoldingState(labState) {
    if (!labState) return false;
    if (labState.system_status === SYSTEM_STATUS_HOLDING) return true;
    // START_TELEOP sets system_status to TELEOP while holding.tag_id stays set.
    return !!getHolding(labState).tag_id;
}

/** ``true`` when the given tag is the one currently in the gripper. */
export function isHeldTag(tagId, labState) {
    if (!tagId || !labState) return false;
    return getHolding(labState).tag_id === tagId;
}

/** ``true`` when HOLDING but tag is unknown / unconfirmed (UI must lock). */
export function isHoldingUnconfirmed(labState) {
    if (!isHoldingState(labState)) return false;
    return getHolding(labState).requires_operator_confirm;
}

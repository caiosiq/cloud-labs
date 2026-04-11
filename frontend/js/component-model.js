/**
 * Client-side accessors for tunables vs measurables (see refactor.md).
 */

export const PRESENCE_BREADBOARD = 'breadboard';
export const PRESENCE_STORAGE = 'storage';
export const PRESENCE_OFF_TABLE = 'off_table';

/** @param {object | undefined} c */
export function measPose(c) {
    return (c && c.measurables && c.measurables.pose) || {};
}

/** @param {object | undefined} c */
export function nominalPose(c) {
    const n = c && c.tunables && c.tunables.nominal_pose;
    return n && typeof n === 'object' ? n : {};
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

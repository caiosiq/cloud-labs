/**
 * Lab frame (mm): third quadrant (x < 0 and y < 0) is the storage / inventory strip on the breadboard.
 * Placed / active area = anywhere else within lab bounds.
 */

import {
    componentPresence,
    isBreadboardIntent,
    isStoredComponent,
    measPose,
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
} from './component-model.js';

export function isStorageRegion(x, y) {
    return x < 0 && y < 0;
}

export function isPlacedRegion(x, y) {
    return !isStorageRegion(x, y);
}

/**
 * @returns {string[]} human-readable warnings when measured pose disagrees with presence intent
 */
export function collectLayoutWarnings(labState) {
    const warnings = [];
    if (!labState || !labState.components) return warnings;
    for (const [id, comp] of Object.entries(labState.components)) {
        const p = measPose(comp);
        if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') continue;
        const { x, y } = p;
        const pres = componentPresence(comp);
        if (pres === PRESENCE_STORAGE && !isStorageRegion(x, y)) {
            warnings.push(`${id}: storage intent but center (${x.toFixed(1)}, ${y.toFixed(1)}) is not in storage (x<0, y<0).`);
        }
        if (pres === PRESENCE_BREADBOARD && isStorageRegion(x, y)) {
            warnings.push(`${id}: breadboard intent but center (${x.toFixed(1)}, ${y.toFixed(1)}) lies in storage quadrant.`);
        }
    }
    return warnings;
}

/**
 * Region-only collision (in addition to part–part collision). Uses tunables.presence.
 */
export function regionMoveBlocked(presenceBreadboard, x, y) {
    if (presenceBreadboard && isStorageRegion(x, y)) {
        return { blocked: true, reason: 'STORAGE QUADRANT (use Store to move parts here)' };
    }
    if (!presenceBreadboard && isPlacedRegion(x, y)) {
        return { blocked: true, reason: 'BREADBOARD AREA (stored parts must stay in x<0, y<0)' };
    }
    return { blocked: false };
}

export { isStoredComponent, isBreadboardIntent, PRESENCE_STORAGE, PRESENCE_BREADBOARD };

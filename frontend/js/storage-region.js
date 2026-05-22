/**
 * Lab frame (mm): inventory uses the rectangle from `/api/lab-layout` (`storage_grid.q3`):
 * [STORAGE_RECT_X_MIN, 0) × [STORAGE_RECT_Y_MIN, 0). Breadboard = complementary within lab bounds.
 */

import {
    STORAGE_RECT_X_MIN,
    STORAGE_RECT_Y_MIN,
} from './config.js';
import {
    componentPresence,
    drawPose,
    isBreadboardIntent,
    isStoredComponent,
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
} from './component-model.js';

export function isStorageRegion(x, y) {
    return STORAGE_RECT_X_MIN <= x && x < 0 && STORAGE_RECT_Y_MIN <= y && y < 0;
}

export function isPlacedRegion(x, y) {
    return !isStorageRegion(x, y);
}

/**
 * @returns {string[]} human-readable warnings when committed pose disagrees with presence intent
 */
export function collectLayoutWarnings(labState) {
    const warnings = [];
    if (!labState || !labState.components) return warnings;
    for (const [id, comp] of Object.entries(labState.components)) {
        const p = drawPose(comp);
        if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') continue;
        const { x, y } = p;
        const pres = componentPresence(comp);
        if (pres === PRESENCE_STORAGE && !isStorageRegion(x, y)) {
            warnings.push(`${id}: storage intent but center (${x.toFixed(1)}, ${y.toFixed(1)}) is outside the configured storage rectangle.`);
        }
        if (pres === PRESENCE_BREADBOARD && isStorageRegion(x, y)) {
            warnings.push(`${id}: breadboard intent but center (${x.toFixed(1)}, ${y.toFixed(1)}) lies in the inventory storage rectangle.`);
        }
    }
    return warnings;
}

/**
 * Region-only collision (in addition to part–part collision). Uses tunables.presence.
 */
export function regionMoveBlocked(presenceBreadboard, x, y) {
    if (presenceBreadboard && isStorageRegion(x, y)) {
        return { blocked: true, reason: 'STORAGE (use Store / inventory tooling for this region)' };
    }
    if (!presenceBreadboard && isPlacedRegion(x, y)) {
        return { blocked: true, reason: 'BREADBOARD AREA (stored parts stay in inventory rectangle)' };
    }
    return { blocked: false };
}

export { isStoredComponent, isBreadboardIntent, PRESENCE_STORAGE, PRESENCE_BREADBOARD };

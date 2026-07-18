/**
 * Lab frame (mm): inventory uses the rectangle from `/api/lab-layout` (`storage_grid.q3`):
 * [STORAGE_RECT_X_MIN, 0) × [STORAGE_RECT_Y_MIN, 0). Breadboard = complementary within lab bounds.
 */

import {
    STORAGE_RECT_X_MIN,
    STORAGE_RECT_Y_MIN,
} from './config.js';
import {
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

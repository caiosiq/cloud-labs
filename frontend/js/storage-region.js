/**
 * Lab frame (mm): inventory uses the rectangle from `/api/lab-layout` (`storage_grid.q3`):
 * [STORAGE_RECT_X_MIN, STORAGE_RECT_X_MAX) x
 * [STORAGE_RECT_Y_MIN, STORAGE_RECT_Y_MAX). Breadboard is the complement.
 */

import {
    STORAGE_RECT_X_MIN,
    STORAGE_RECT_X_MAX,
    STORAGE_RECT_Y_MIN,
    STORAGE_RECT_Y_MAX,
} from './config.js';
import {
    isBreadboardIntent,
    isStoredComponent,
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
} from './component-model.js';
import { getStatecontrol } from './component-state.js';
import { store } from './state/store.js';

export function isStorageRegion(x, y) {
    return STORAGE_RECT_X_MIN <= x && x < STORAGE_RECT_X_MAX
        && STORAGE_RECT_Y_MIN <= y && y < STORAGE_RECT_Y_MAX;
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

/** Occupied storage cells from live lab state (exclude optional tag). */
export function occupiedStorageSlots(excludeTagId = null) {
    const comps = store.labState?.components || {};
    const occ = new Set();
    for (const [tid, comp] of Object.entries(comps)) {
        if (excludeTagId && tid === excludeTagId) continue;
        if (!isStoredComponent(comp)) continue;
        const slot = getStatecontrol(comp).tunables?.storage?.slot;
        if (slot && Number.isFinite(Number(slot.i)) && Number.isFinite(Number(slot.j))) {
            occ.add(`${Number(slot.i)},${Number(slot.j)}`);
        }
    }
    return occ;
}

/**
 * Map lab mm → storage cell indices, or null if outside the grid.
 * @returns {{ i: number, j: number } | null}
 */
export function labXyToStorageSlot(x, y) {
    const spec = store.storageGridSpec;
    if (!spec || !spec.nx || !spec.ny || !spec.q3) return null;
    const { nx, ny, cell_width_mm: cw, cell_height_mm: ch, q3 } = spec;
    if (!(q3.x_min <= x && x < q3.x_max && q3.y_min <= y && y < q3.y_max)) {
        return null;
    }
    const i = Math.floor((x - q3.x_min) / cw);
    const j = Math.floor((y - q3.y_min) / ch);
    if (i < 0 || j < 0 || i >= nx || j >= ny) return null;
    return { i, j };
}

/**
 * Cell-center pose for a storage slot (versioned inventory draws from slot only).
 * @returns {{ x: number, y: number, rotation: number } | null}
 */
export function storageSlotCenterPose(slot) {
    if (!slot || !Number.isFinite(Number(slot.i)) || !Number.isFinite(Number(slot.j))) {
        return null;
    }
    const spec = store.storageGridSpec;
    if (!spec || !spec.q3) return null;
    const cw = Number(spec.cell_width_mm);
    const ch = Number(spec.cell_height_mm);
    if (!Number.isFinite(cw) || !Number.isFinite(ch) || cw <= 0 || ch <= 0) return null;
    const i = Number(slot.i);
    const j = Number(slot.j);
    const x = spec.q3.x_min + (i + 0.5) * cw;
    const y = spec.q3.y_min + (j + 0.5) * ch;
    const rot =
        spec.nominal_storage_rotation_deg != null
            ? Number(spec.nominal_storage_rotation_deg)
            : 0;
    return { x, y, rotation: Number.isFinite(rot) ? rot : 0 };
}

export function clearStoreToSlotMode() {
    if (store.storeToSlotTag) {
        store.storeToSlotTag = null;
    }
}

export { isStoredComponent, isBreadboardIntent, PRESENCE_STORAGE, PRESENCE_BREADBOARD };

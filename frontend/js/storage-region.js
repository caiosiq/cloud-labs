/**
 * Lab frame (mm): third quadrant (x < 0 and y < 0) is the storage / inventory strip on the breadboard.
 * Placed / active area = anywhere else within lab bounds.
 */

export function isStorageRegion(x, y) {
    return x < 0 && y < 0;
}

export function isPlacedRegion(x, y) {
    return !isStorageRegion(x, y);
}

/**
 * @returns {string[]} human-readable warnings when pose disagrees with PLACED / STORED semantics
 */
export function collectLayoutWarnings(labState) {
    const warnings = [];
    if (!labState || !labState.components) return warnings;
    for (const [id, comp] of Object.entries(labState.components)) {
        const p = comp.pose;
        if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') continue;
        const { x, y } = p;
        const st = comp.state;
        if (st === 'STORED' && !isStorageRegion(x, y)) {
            warnings.push(`${id}: STORED but center (${x.toFixed(1)}, ${y.toFixed(1)}) is not in storage (x<0, y<0).`);
        }
        if (st === 'PLACED' && isStorageRegion(x, y)) {
            warnings.push(`${id}: PLACED but center (${x.toFixed(1)}, ${y.toFixed(1)}) lies in storage quadrant.`);
        }
    }
    return warnings;
}

/**
 * Region-only collision (in addition to part–part collision). Uses component semantic state.
 */
export function regionMoveBlocked(state, x, y) {
    if (state === 'PLACED' && isStorageRegion(x, y)) {
        return { blocked: true, reason: 'STORAGE QUADRANT (use Store to move parts here)' };
    }
    if (state === 'STORED' && isPlacedRegion(x, y)) {
        return { blocked: true, reason: 'BREADBOARD AREA (stored parts must stay in x<0, y<0)' };
    }
    return { blocked: false };
}

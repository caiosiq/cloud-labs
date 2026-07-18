/**
 * Alignment snap engine — laser lines + user guides → snap point in lab mm.
 * No DOM. Reads `store.laserLinesDoc` and `store.guideLines`.
 *
 * Snap rules:
 *   1. Junction (segment crossing) in capture zone → glue there. Latch with hysteresis.
 *   2. Otherwise nearest line segment within ALIGNMENT_SNAP_THRESHOLD_MM.
 *   3. Otherwise pass-through (no snap).
 *
 * Thresholds are tunable in dev via URL when `?debug=1`:
 *   - `alignment_snap_mm`              (segment perpendicular snap distance)
 *   - `alignment_intersection_mm`      (enter-junction radius)
 *   - `alignment_intersection_latch_mm`(stay-on-junction radius)
 */
import { store } from '../state/store.js';
import {
    twoPointsToLineModel,
    clipTwoPointLineToLabBounds,
    closestPointOnSegment,
    pointInLabBoundsMm,
    lineModelIntersection,
    infiniteLineIntersection,
} from '../geometry/lines.js';

/** Perpendicular snap to lines during drag (junction glue still preferred when in range). */
export const ALIGNMENT_LINE_SNAP_ENABLED = true;
/** Draw amber dots at every detected line crossing (lab mm). */
export const ALIGNMENT_SHOW_INTERSECTION_MARKERS = true;

/** Perpendicular snap to alignment segments — lab mm. */
let ALIGNMENT_SNAP_THRESHOLD_MM = 10;
/**
 * Junction capture: pointer within this distance (mm) of the crossing point, OR within
 * ALIGNMENT_SNAP_THRESHOLD_MM of both lines that meet (corner zone — same feel as line glue).
 */
let ALIGNMENT_INTERSECTION_ENTER_MM = 18;
/** While latched to a junction, stay locked until pointer leaves this radius (mm) from the point. */
let ALIGNMENT_INTERSECTION_LATCH_MM = 26;

(() => {
    if (typeof URLSearchParams === 'undefined') return;
    const params = new URLSearchParams(window.location.search || '');
    if (params.get('debug') !== '1') return;

    const segRaw = parseFloat(params.get('alignment_snap_mm'));
    if (Number.isFinite(segRaw) && segRaw > 0 && segRaw <= 200) {
        ALIGNMENT_SNAP_THRESHOLD_MM = segRaw;
    }

    const enterRaw = parseFloat(params.get('alignment_intersection_mm'));
    if (Number.isFinite(enterRaw) && enterRaw > 0 && enterRaw <= 300) {
        ALIGNMENT_INTERSECTION_ENTER_MM = enterRaw;
    }

    const latchRaw = parseFloat(params.get('alignment_intersection_latch_mm'));
    if (Number.isFinite(latchRaw) && latchRaw > 0 && latchRaw <= 300) {
        ALIGNMENT_INTERSECTION_LATCH_MM = latchRaw;
    }

    console.info('[alignment snap] thresholds (mm)', {
        segment: ALIGNMENT_SNAP_THRESHOLD_MM,
        intersectionEnter: ALIGNMENT_INTERSECTION_ENTER_MM,
        intersectionLatch: ALIGNMENT_INTERSECTION_LATCH_MM,
    });
})();

export function getAlignmentSnapThresholdMm() {
    return ALIGNMENT_SNAP_THRESHOLD_MM;
}

/** Switch glued segment during drag only if competitor is this much tighter (mm) to pointer. */
const ALIGNMENT_DRAG_STICKY_BREAK_MM = 8;
/** Prefer previous sticky segment if feet are nearly tied — tie zone (mm) when using prev ghost. */
const ALIGNMENT_SEGMENT_CHOICE_TIE_MM = 2;

/** While dragging: latched segment index. Reset on drag end. */
let _dragAlignmentStickySegIdx = null;
/** @type {{ x: number; y: number } | null} While dragging: latched junction point. Reset on drag end. */
let _dragAlignmentStickyIntersection = null;

export function getDragAlignmentStickySegIdx() {
    return _dragAlignmentStickySegIdx;
}
export function setDragAlignmentStickySegIdx(idx) {
    _dragAlignmentStickySegIdx = idx;
}
export function getDragAlignmentStickyIntersection() {
    return _dragAlignmentStickyIntersection;
}
export function setDragAlignmentStickyIntersection(pt) {
    _dragAlignmentStickyIntersection = pt;
}
export function resetDragAlignmentSticky() {
    _dragAlignmentStickySegIdx = null;
    _dragAlignmentStickyIntersection = null;
}

/**
 * With Shift: lock the free end to horizontal, vertical, or — when
 * ``refDirection`` is supplied — the segment's existing direction through
 * the anchor. The constraint whose projection is closest to the pointer wins,
 * so a sloped line stays sloped until the cursor clearly favors H or V.
 *
 * Used by guide drawing (H/V only) and endpoint resize (H/V + current angle).
 *
 * @param {number} sx anchor x (fixed point)
 * @param {number} sy anchor y
 * @param {number} cx cursor x
 * @param {number} cy cursor y
 * @param {boolean} shiftKey
 * @param {{ refDx?: number, refDy?: number } | null} [refDirection] existing direction from anchor (endpoint resize)
 */
export function constrainGuideEndWithShift(sx, sy, cx, cy, shiftKey, refDirection = null) {
    if (!shiftKey) return { x: cx, y: cy };

    const dx = cx - sx;
    const dy = cy - sy;

    /** @type {{ x: number, y: number }[]} */
    const candidates = [
        { x: cx, y: sy },
        { x: sx, y: cy },
    ];

    const refDx = refDirection?.refDx;
    const refDy = refDirection?.refDy;
    if (Number.isFinite(refDx) && Number.isFinite(refDy)) {
        const len2 = refDx * refDx + refDy * refDy;
        if (len2 > 1e-12) {
            const len = Math.sqrt(len2);
            const ux = refDx / len;
            const uy = refDy / len;
            const t = dx * ux + dy * uy;
            candidates.push({ x: sx + t * ux, y: sy + t * uy });
        }
    }

    let best = candidates[0];
    let bestD2 = (cx - best.x) ** 2 + (cy - best.y) ** 2;
    for (let i = 1; i < candidates.length; i++) {
        const c = candidates[i];
        const d2 = (cx - c.x) ** 2 + (cy - c.y) ** 2;
        if (d2 < bestD2) {
            bestD2 = d2;
            best = c;
        }
    }
    return best;
}

/**
 * @typedef {{
 *   p1: { x: number; y: number },
 *   p2: { x: number; y: number },
 *   source: 'laser'|'guide',
 *   model: ReturnType<typeof twoPointsToLineModel>,
 *   label: string,
 * }} AlignmentSegment
 */

/** @returns {AlignmentSegment[]} */
export function collectAlignmentSegments() {
    const segments = [];
    const doc = store.laserLinesDoc;
    if (doc && Array.isArray(doc.lines)) {
        doc.lines.forEach((line) => {
            if (!line || line.enabled === false || !line.p1 || !line.p2) return;
            const model = twoPointsToLineModel(line.p1, line.p2);
            const seg = clipTwoPointLineToLabBounds(line.p1, line.p2);
            if (!seg || !model) return;
            segments.push({
                p1: seg[0],
                p2: seg[1],
                source: 'laser',
                model,
                label: line.name || line.id || 'laser',
            });
        });
    }
    (store.guideLines || []).forEach((g, gi) => {
        if (g && g.p1 && g.p2 && [g.p1.x, g.p1.y, g.p2.x, g.p2.y].every(Number.isFinite)) {
            const model = twoPointsToLineModel(g.p1, g.p2);
            if (!model) return;
            segments.push({
                p1: { x: g.p1.x, y: g.p1.y },
                p2: { x: g.p2.x, y: g.p2.y },
                source: 'guide',
                model,
                label: g.id || `guide-${gi}`,
            });
        }
    });
    return segments;
}

function intersectionForSegmentPair(Sa, Sb) {
    let IX = lineModelIntersection(Sa.model, Sb.model);
    if (!IX) {
        IX = infiniteLineIntersection(Sa.p1, Sa.p2, Sb.p1, Sb.p2);
    }
    return IX;
}

/** Pairwise crossings (infinite lines); `inBounds` marks table-visible junctions. */
function collectSegmentIntersections(segments, { includeOutOfBounds = false } = {}) {
    const out = [];
    const n = segments.length;
    for (let i = 0; i < n; i++) {
        const Sa = segments[i];
        for (let j = i + 1; j < n; j++) {
            const Sb = segments[j];
            const IX = intersectionForSegmentPair(Sa, Sb);
            if (!IX) continue;
            const inBounds = pointInLabBoundsMm(IX.x, IX.y);
            if (!inBounds && !includeOutOfBounds) continue;
            out.push({
                x: IX.x,
                y: IX.y,
                segA: i,
                segB: j,
                inBounds,
                labelA: Sa.label,
                labelB: Sb.label,
            });
        }
    }
    return out;
}

let _alignmentIntersectionCache = [];
let _alignmentIntersectionOutOfBounds = [];
let _alignmentIntersectionLogKey = '';

/** Cache lab-mm junctions (in & out of bounds) for marker drawing + snap; recomputes from current store. */
export function refreshAlignmentIntersectionCache() {
    const segments = collectAlignmentSegments();
    const allMath = collectSegmentIntersections(segments, { includeOutOfBounds: true });
    _alignmentIntersectionCache = allMath.filter((ix) => ix.inBounds);
    _alignmentIntersectionOutOfBounds = allMath.filter((ix) => !ix.inBounds);

    const pairNotes = [];
    const n = segments.length;
    for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
            const Sa = segments[i];
            const Sb = segments[j];
            const IX = intersectionForSegmentPair(Sa, Sb);
            if (!IX) {
                pairNotes.push({
                    a: Sa.label,
                    b: Sb.label,
                    reason: 'parallel or degenerate',
                });
                continue;
            }
            if (!pointInLabBoundsMm(IX.x, IX.y)) {
                pairNotes.push({
                    a: Sa.label,
                    b: Sb.label,
                    reason: 'crosses outside lab bounds',
                    x: Math.round(IX.x * 10) / 10,
                    y: Math.round(IX.y * 10) / 10,
                });
                continue;
            }
            pairNotes.push({
                a: Sa.label,
                b: Sb.label,
                reason: 'junction',
                x: Math.round(IX.x * 10) / 10,
                y: Math.round(IX.y * 10) / 10,
            });
        }
    }

    const logKey = JSON.stringify({
        n: segments.length,
        in: _alignmentIntersectionCache.length,
        pairNotes,
    });
    if (logKey !== _alignmentIntersectionLogKey) {
        _alignmentIntersectionLogKey = logKey;
        console.info('[alignment] junction scan', {
            segmentCount: segments.length,
            segments: segments.map((s) => ({
                label: s.label,
                source: s.source,
                kind: s.model?.kind,
            })),
            inBoundsJunctions: _alignmentIntersectionCache.length,
            outOfBoundsCrossings: _alignmentIntersectionOutOfBounds.length,
            pairs: pairNotes,
        });
    }
    return _alignmentIntersectionCache;
}

export function getAlignmentIntersectionOutOfBounds() {
    return _alignmentIntersectionOutOfBounds;
}

/**
 * True when pointer is in range to glue to this junction: near the point itself, or in the
 * "corner" where both meeting segments are within line snap distance (like line glue).
 */
function intersectionInCaptureZone(px, py, ix, da, segmentSnapMm, enterMm) {
    const dPoint = Math.hypot(px - ix.x, py - ix.y);
    if (dPoint <= enterMm) return true;
    if (!ALIGNMENT_LINE_SNAP_ENABLED) return false;
    const dA = da[ix.segA];
    const dB = da[ix.segB];
    return (
        Number.isFinite(dA) &&
        Number.isFinite(dB) &&
        dA <= segmentSnapMm &&
        dB <= segmentSnapMm
    );
}

function resolveLatchedIntersection(stickyIntersection, intersections) {
    if (
        !stickyIntersection ||
        !Number.isFinite(stickyIntersection.x) ||
        !Number.isFinite(stickyIntersection.y)
    ) {
        return null;
    }
    let match = null;
    let matchD = 2.5;
    for (const ix of intersections) {
        const d = Math.hypot(ix.x - stickyIntersection.x, ix.y - stickyIntersection.y);
        if (d + 1e-9 < matchD - 1e-9) {
            matchD = d;
            match = ix;
        }
    }
    return (
        match || {
            x: stickyIntersection.x,
            y: stickyIntersection.y,
            segA:
                typeof stickyIntersection.segA === 'number' ? stickyIntersection.segA : 0,
            segB:
                typeof stickyIntersection.segB === 'number' ? stickyIntersection.segB : 1,
        }
    );
}

/**
 * Best junction to glue to: must be in capture zone; prefer smallest distance to crossing.
 * @returns {{ x: number; y: number; segA: number; segB: number } | null}
 */
function bestIntersectionForCapture(px, py, intersections, da, segmentSnapMm, enterMm) {
    let best = null;
    let bestD = Infinity;
    for (const ix of intersections) {
        if (!intersectionInCaptureZone(px, py, ix, da, segmentSnapMm, enterMm)) continue;
        const dPoint = Math.hypot(px - ix.x, py - ix.y);
        if (dPoint + 1e-9 < bestD - 1e-9) {
            bestD = dPoint;
            best = ix;
        }
    }
    return best;
}

function pickSegmentIndexForSnap(
    n,
    da,
    qix,
    qiy,
    segmentSnapMm,
    { prevGhost, stickySegIdx, useSegmentSticky },
) {
    const argMinBare = da.reduce((b, _, i, arr) => (arr[i] < arr[b] ? i : b), 0);

    let bestIdx = argMinBare;
    if (prevGhost && Number.isFinite(prevGhost.x) && Number.isFinite(prevGhost.y)) {
        let best = 0;
        for (let i = 1; i < n; i++) {
            if (da[i] + 1e-9 < da[best] - 1e-9) {
                best = i;
            } else if (Math.abs(da[i] - da[best]) <= ALIGNMENT_SEGMENT_CHOICE_TIE_MM) {
                const di = Math.hypot(qix[i] - prevGhost.x, qiy[i] - prevGhost.y);
                const db = Math.hypot(qix[best] - prevGhost.x, qiy[best] - prevGhost.y);
                if (di + 1e-9 < db) best = i;
            }
        }
        bestIdx = best;
    }

    let chosenIdx = bestIdx;
    if (
        useSegmentSticky &&
        stickySegIdx != null &&
        Number.isFinite(stickySegIdx) &&
        stickySegIdx >= 0 &&
        stickySegIdx < n
    ) {
        const ds = da[stickySegIdx];
        if (Number.isFinite(ds) && ds < segmentSnapMm * 1.55) {
            const db = da[bestIdx];
            if (!(ds - db > ALIGNMENT_DRAG_STICKY_BREAK_MM)) {
                chosenIdx = stickySegIdx;
            }
        }
    }
    return chosenIdx;
}

/**
 * Alignment snap: nearest junction in range first, else closest line segment.
 * Leaving a junction re-picks the closest line (segment sticky disabled for one frame).
 * @returns {{ x: number; y: number; segIdx: number | null; snapKind: 'intersection'|'segment'|'none'; intersection?: { x: number; y: number; segA: number; segB: number } }}
 */
export function snapLabPointUnified(x, y, options = {}) {
    const {
        segmentSnapMm = ALIGNMENT_SNAP_THRESHOLD_MM,
        prevGhost = null,
        stickySegIdx = null,
        stickyIntersection = null,
    } = options;
    const none = { x, y, segIdx: null, snapKind: 'none' };
    const segments = collectAlignmentSegments();
    if (segments.length === 0) {
        return none;
    }

    const n = segments.length;
    const qix = new Array(n);
    const qiy = new Array(n);
    const da = new Array(n);
    let anyFinite = false;
    for (let i = 0; i < n; i++) {
        const s = segments[i];
        const c = closestPointOnSegment(x, y, s.p1.x, s.p1.y, s.p2.x, s.p2.y);
        qix[i] = c.x;
        qiy[i] = c.y;
        da[i] = c.dist;
        if (Number.isFinite(c.dist)) anyFinite = true;
    }
    if (!anyFinite) {
        return none;
    }

    const intersections = collectSegmentIntersections(segments);
    const enterMm = ALIGNMENT_INTERSECTION_ENTER_MM;
    const latchMm = ALIGNMENT_INTERSECTION_LATCH_MM;
    const hadIxLatch =
        stickyIntersection &&
        Number.isFinite(stickyIntersection.x) &&
        Number.isFinite(stickyIntersection.y);

    let ixHit = null;
    if (hadIxLatch) {
        const latched = resolveLatchedIntersection(stickyIntersection, intersections);
        if (latched) {
            const dPoint = Math.hypot(x - latched.x, y - latched.y);
            const stillInCorner =
                ALIGNMENT_LINE_SNAP_ENABLED &&
                intersectionInCaptureZone(x, y, latched, da, segmentSnapMm, enterMm);
            if (dPoint <= latchMm || stillInCorner) {
                ixHit = latched;
            }
        }
    }
    if (!ixHit) {
        ixHit = bestIntersectionForCapture(
            x,
            y,
            intersections,
            da,
            segmentSnapMm,
            enterMm,
        );
    }

    if (ixHit) {
        const dA = da[ixHit.segA];
        const dB = da[ixHit.segB];
        let segIdx = ixHit.segA;
        if (Number.isFinite(dB) && (!Number.isFinite(dA) || dB + 1e-9 < dA - 1e-9)) {
            segIdx = ixHit.segB;
        }
        return {
            x: ixHit.x,
            y: ixHit.y,
            segIdx,
            snapKind: 'intersection',
            intersection: {
                x: ixHit.x,
                y: ixHit.y,
                segA: ixHit.segA,
                segB: ixHit.segB,
            },
        };
    }

    if (!ALIGNMENT_LINE_SNAP_ENABLED) {
        return none;
    }

    const useSegmentSticky = !hadIxLatch;
    const chosenIdx = pickSegmentIndexForSnap(n, da, qix, qiy, segmentSnapMm, {
        prevGhost,
        stickySegIdx,
        useSegmentSticky,
    });

    if (!Number.isFinite(da[chosenIdx]) || da[chosenIdx] >= segmentSnapMm) {
        return none;
    }

    return {
        x: qix[chosenIdx],
        y: qiy[chosenIdx],
        segIdx: chosenIdx,
        snapKind: 'segment',
    };
}

/**
 * Single-shot placement / non-drag snapping: prefer continuity with nearest spine + corners on it.
 * Drops and context moves use this — no sticky carry-over.
 */
export function snapLabPointToAlignmentGuides(
    x,
    y,
    threshold = ALIGNMENT_SNAP_THRESHOLD_MM,
) {
    const r = snapLabPointUnified(x, y, {
        segmentSnapMm: threshold,
        prevGhost: null,
        stickySegIdx: null,
        stickyIntersection: null,
    });
    return { x: r.x, y: r.y };
}

/**
 * Pointer lab position during a component drag: optional Shift locks motion to horizontal /
 * vertical through (sx,sy). Then alignment snap with optional drag glue
 * (`prevGhost` / `stickySegIdx` / `stickyIntersection`).
 */
export function snapLabPointWithOptionalShiftAxis(
    sx,
    sy,
    cx,
    cy,
    shiftKey,
    alignmentDragOpts = undefined,
) {
    let x = cx;
    let y = cy;
    let horizontal = true;
    if (shiftKey) {
        const dx = cx - sx;
        const dy = cy - sy;
        horizontal = Math.abs(dx) >= Math.abs(dy);
        const c = constrainGuideEndWithShift(sx, sy, cx, cy, true);
        x = c.x;
        y = c.y;
    }
    const snapped = snapLabPointUnified(x, y, {
        segmentSnapMm: ALIGNMENT_SNAP_THRESHOLD_MM,
        ...(alignmentDragOpts || {}),
    });
    let outX = snapped.x;
    let outY = snapped.y;
    if (shiftKey) {
        outX = horizontal ? snapped.x : sx;
        outY = horizontal ? sy : snapped.y;
    }
    return {
        x: outX,
        y: outY,
        segIdx: snapped.segIdx,
        snapKind: snapped.snapKind,
        intersection: snapped.intersection,
    };
}

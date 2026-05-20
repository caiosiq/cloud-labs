/**
 * Pure line / segment math in lab millimetres.
 * Two-point line models, point-line distance / projection, segment helpers, clipping to bounds.
 * No DOM, no store access.
 */
import {
    LAB_X_MIN,
    LAB_X_MAX,
    LAB_Y_MIN,
    LAB_Y_MAX,
} from '../config.js';

/** @typedef {{ x: number, y: number }} Pt */
/**
 * @typedef {
 *   { kind: 'vertical', x0: number }
 *   | { kind: 'horizontal', y0: number }
 *   | { kind: 'ab', a: number, b: number }
 * } LineModel
 */

/**
 * Two-point line in lab mm → analytic model.
 * Vertical (x = x0), horizontal (y = y0), or ``x = a*y + b`` (matches laser-line convention).
 * @param {Pt} p1
 * @param {Pt} p2
 * @returns {LineModel | null}
 */
export function twoPointsToLineModel(p1, p2) {
    const x1 = +p1.x;
    const y1 = +p1.y;
    const x2 = +p2.x;
    const y2 = +p2.y;
    if (![x1, y1, x2, y2].every(Number.isFinite)) return null;
    if (Math.hypot(x2 - x1, y2 - y1) < 1e-6) return null;
    if (Math.abs(x2 - x1) < 1e-6) return { kind: 'vertical', x0: x1 };
    if (Math.abs(y2 - y1) < 1e-6) return { kind: 'horizontal', y0: y1 };
    const a = (x2 - x1) / (y2 - y1);
    const b = x1 - a * y1;
    return { kind: 'ab', a, b };
}

/** Clip segment of line ``x = a*y + b`` to lab bounds; returns [p1, p2] (lab mm) or null. */
export function clipLaserLineToBounds(a, b) {
    const pts = [];
    if (Math.abs(a) < 1e-10) {
        const x = b;
        if (x < LAB_X_MIN || x > LAB_X_MAX) return null;
        pts.push({ x, y: LAB_Y_MIN }, { x, y: LAB_Y_MAX });
    } else {
        const yAtLeft = (LAB_X_MIN - b) / a;
        const yAtRight = (LAB_X_MAX - b) / a;
        if (yAtLeft >= LAB_Y_MIN && yAtLeft <= LAB_Y_MAX) pts.push({ x: LAB_X_MIN, y: yAtLeft });
        if (yAtRight >= LAB_Y_MIN && yAtRight <= LAB_Y_MAX) pts.push({ x: LAB_X_MAX, y: yAtRight });
        const xAtBottom = a * LAB_Y_MIN + b;
        const xAtTop = a * LAB_Y_MAX + b;
        if (xAtBottom >= LAB_X_MIN && xAtBottom <= LAB_X_MAX) pts.push({ x: xAtBottom, y: LAB_Y_MIN });
        if (xAtTop >= LAB_X_MIN && xAtTop <= LAB_X_MAX) pts.push({ x: xAtTop, y: LAB_Y_MAX });
    }
    if (pts.length < 2) return null;
    pts.sort((a_, b_) => a_.y - b_.y);
    return [pts[0], pts[pts.length - 1]];
}

/**
 * Clip two-point line through (p1, p2) to lab bounds.
 * Handles vertical and horizontal lines explicitly; otherwise delegates to clipLaserLineToBounds.
 * @returns {[Pt, Pt] | null}
 */
export function clipTwoPointLineToLabBounds(p1, p2) {
    const m = twoPointsToLineModel(p1, p2);
    if (!m) return null;
    if (m.kind === 'ab') return clipLaserLineToBounds(m.a, m.b);
    if (m.kind === 'vertical') {
        const x = m.x0;
        if (x < LAB_X_MIN || x > LAB_X_MAX) return null;
        return [{ x, y: LAB_Y_MIN }, { x, y: LAB_Y_MAX }];
    }
    const y0 = m.y0;
    if (y0 < LAB_Y_MIN || y0 > LAB_Y_MAX) return null;
    return [{ x: LAB_X_MIN, y: y0 }, { x: LAB_X_MAX, y: y0 }];
}

/** Perpendicular distance (lab mm) from point (x,y) to line model `m`; Infinity if model is null. */
export function distancePointToLineModel(x, y, m) {
    if (!m) return Infinity;
    if (m.kind === 'vertical') return Math.abs(x - m.x0);
    if (m.kind === 'horizontal') return Math.abs(y - m.y0);
    const val = x - m.a * y - m.b;
    return Math.abs(val) / Math.sqrt(1 + m.a * m.a);
}

/** Project point (x,y) onto line model `m`; returns the foot in lab mm. */
export function projectPointToLineModel(x, y, m) {
    if (!m) return { x, y };
    if (m.kind === 'vertical') return { x: m.x0, y };
    if (m.kind === 'horizontal') return { x, y: m.y0 };
    const val = x - m.a * y - m.b;
    const denom = 1 + m.a * m.a;
    const k = val / denom;
    return { x: x - k, y: y + m.a * k };
}

/**
 * Closest point on finite segment A–B to P, with chord distance.
 * Returns `{ x, y, dist }` in lab mm.
 */
export function closestPointOnSegment(px, py, x1, y1, x2, y2) {
    const vx = x2 - x1;
    const vy = y2 - y1;
    const wx = px - x1;
    const wy = py - y1;
    const c1 = wx * vx + wy * vy;
    if (c1 <= 0) {
        const d = Math.hypot(px - x1, py - y1);
        return { x: x1, y: y1, dist: d };
    }
    const c2 = vx * vx + vy * vy;
    if (c2 <= c1) {
        const d = Math.hypot(px - x2, py - y2);
        return { x: x2, y: y2, dist: d };
    }
    const t = c1 / c2;
    const x = x1 + t * vx;
    const y = y1 + t * vy;
    return { x, y, dist: Math.hypot(px - x, py - y) };
}

/** True iff (x,y) lies inside the configured lab bounds (inclusive). */
export function pointInLabBoundsMm(x, y) {
    return (
        x >= LAB_X_MIN &&
        x <= LAB_X_MAX &&
        y >= LAB_Y_MIN &&
        y <= LAB_Y_MAX
    );
}

/** Analytic crossing of two line models (lab mm); null if parallel or degenerate. */
export function lineModelIntersection(mA, mB) {
    if (!mA || !mB) return null;
    if (mA.kind === 'vertical' && mB.kind === 'vertical') return null;
    if (mA.kind === 'horizontal' && mB.kind === 'horizontal') return null;
    if (mA.kind === 'vertical' && mB.kind === 'horizontal') {
        return { x: mA.x0, y: mB.y0 };
    }
    if (mA.kind === 'horizontal' && mB.kind === 'vertical') {
        return { x: mB.x0, y: mA.y0 };
    }
    if (mA.kind === 'vertical' && mB.kind === 'ab') {
        const x = mA.x0;
        if (Math.abs(mB.a) < 1e-12) return null;
        return { x, y: (x - mB.b) / mB.a };
    }
    if (mA.kind === 'ab' && mB.kind === 'vertical') {
        const x = mB.x0;
        if (Math.abs(mA.a) < 1e-12) return null;
        return { x, y: (x - mA.b) / mA.a };
    }
    if (mA.kind === 'horizontal' && mB.kind === 'ab') {
        const y = mA.y0;
        return { x: mB.a * y + mB.b, y };
    }
    if (mA.kind === 'ab' && mB.kind === 'horizontal') {
        const y = mB.y0;
        return { x: mA.a * y + mA.b, y };
    }
    const da = mA.a - mB.a;
    if (Math.abs(da) < 1e-12) return null;
    const y = (mB.b - mA.b) / da;
    const x = mA.a * y + mA.b;
    return { x, y };
}

/** Crossing of infinite lines through (p1a,p2a) and (p1b,p2b); null if parallel. */
export function infiniteLineIntersection(p1a, p2a, p1b, p2b) {
    const ax1 = p1a.x;
    const ay1 = p1a.y;
    const ax2 = p2a.x;
    const ay2 = p2a.y;
    const bx1 = p1b.x;
    const by1 = p1b.y;
    const bx2 = p2b.x;
    const by2 = p2b.y;
    const rx = ax2 - ax1;
    const ry = ay2 - ay1;
    const sx = bx2 - bx1;
    const sy = by2 - by1;
    const denom = rx * sy - ry * sx;
    if (Math.abs(denom) < 1e-10) return null;
    const qpx = ax1 - bx1;
    const qpy = ay1 - by1;
    const t = (qpx * sy - qpy * sx) / denom;
    return { x: ax1 + t * rx, y: ay1 + t * ry };
}

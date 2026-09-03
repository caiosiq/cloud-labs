/**
 * Resolve breadboard X from Y on a laser line (Command Console stitch moves).
 * Kept separate from laser-lines-panel.js so the console can import it without
 * pulling the dock/modal UI module graph.
 */
import { store } from './state/store.js';
import {
    pointInLabBoundsMm,
    twoPointsToLineModel,
    xAtYOnLineModel,
} from './geometry/lines.js';

/** Legacy command / VC id → canonical Helium–Neon line id. */
const LASER_LINE_ID_ALIASES = Object.freeze({ ne_he: 'he_ne' });

/**
 * @param {string | null | undefined} lineId
 * @returns {string}
 */
export function canonicalizeLaserLineId(lineId) {
    const raw = lineId != null ? String(lineId).trim() : '';
    if (!raw) return '';
    return LASER_LINE_ID_ALIASES[raw] || raw;
}

/**
 * Rewrite legacy ``ne_he`` ids/names in a laser-lines doc (UI + console).
 * @param {object | null | undefined} doc
 * @returns {object | null | undefined}
 */
export function migrateLaserLinesDoc(doc) {
    if (!doc || typeof doc !== 'object' || !Array.isArray(doc.lines)) return doc;
    const lines = [];
    const seen = new Set();
    for (const ln of doc.lines) {
        if (!ln || !ln.id) continue;
        const id = canonicalizeLaserLineId(ln.id);
        if (seen.has(id)) continue;
        seen.add(id);
        const next = { ...ln, id };
        if (id === 'he_ne') {
            const n = String(next.name || '')
                .replace(/[\u2013\u2014]/g, '-')
                .trim()
                .toLowerCase();
            if (!next.name || n === 'ne-he' || n === 'ne_he' || n === 'nehe') {
                next.name = 'He\u2013Ne';
            }
        }
        lines.push(next);
    }
    const snap = canonicalizeLaserLineId(doc.snap_line_id) || doc.snap_line_id;
    return { ...doc, snap_line_id: snap, lines };
}

/**
 * @param {number} yMm
 * @param {number} rotationDeg
 * @param {string | null | undefined} lineId  When empty, uses designated snap line (else first enabled).
 * @returns {{ ok: true, x: number, y: number, rotation: number, lineId: string, lineName: string }
 *   | { ok: false, error: string }}
 */
export function resolveLaserStitchPose(yMm, rotationDeg, lineId) {
    const y = Number(yMm);
    const rotation = Number(rotationDeg);
    if (!Number.isFinite(y) || !Number.isFinite(rotation)) {
        return { ok: false, error: 'Y and rotation must be numbers.' };
    }
    const doc = migrateLaserLinesDoc(store.laserLinesDoc);
    if (!doc || !Array.isArray(doc.lines) || doc.lines.length === 0) {
        return { ok: false, error: 'No laser lines loaded. Open laser lines or refresh lab state.' };
    }
    const byId = {};
    doc.lines.forEach((ln) => {
        if (ln && ln.id) byId[ln.id] = ln;
    });

    const requestedRaw =
        lineId != null && String(lineId).trim() ? String(lineId).trim() : '';
    const requested = canonicalizeLaserLineId(requestedRaw) || requestedRaw;
    let chosen = null;
    if (requested) {
        chosen = byId[requested] || byId[requestedRaw] || null;
        if (!chosen) {
            const needle = requested.toLowerCase().replace(/[\u2013\u2014]/g, '-');
            const nameHits = doc.lines.filter((ln) => {
                if (!ln || !ln.id) return false;
                const n = String(ln.name || '')
                    .trim()
                    .toLowerCase()
                    .replace(/[\u2013\u2014]/g, '-');
                return n && n === needle;
            });
            if (nameHits.length === 1) {
                chosen = nameHits[0];
            } else if (nameHits.length > 1) {
                const list = nameHits.map((h) => h.id).join(', ');
                return {
                    ok: false,
                    error: `Ambiguous laser name "${requested}" → ${list}. Use the line id.`,
                };
            }
        }
        if (!chosen) {
            const ids = doc.lines
                .map((ln) => (ln && ln.id ? `${ln.id}${ln.name ? ` ("${ln.name}")` : ''}` : ''))
                .filter(Boolean)
                .sort()
                .join(', ');
            return {
                ok: false,
                error: `Unknown laser line "${requested}". Known: ${ids || '(none)'}`,
            };
        }
    } else {
        const snapId = canonicalizeLaserLineId(doc.snap_line_id) || doc.snap_line_id;
        if (snapId && byId[snapId] && byId[snapId].enabled !== false) {
            chosen = byId[snapId];
        }
        if (!chosen) {
            chosen = doc.lines.find((l) => l && l.enabled !== false && l.p1 && l.p2) || null;
        }
        if (!chosen) {
            return { ok: false, error: 'No enabled laser line to stitch to. Pass an explicit line id.' };
        }
    }

    if (!chosen.p1 || !chosen.p2) {
        return { ok: false, error: `Laser line "${chosen.id}" is missing endpoints.` };
    }
    const model = twoPointsToLineModel(chosen.p1, chosen.p2);
    if (!model) {
        return { ok: false, error: `Laser line "${chosen.id}" geometry is invalid.` };
    }
    if (model.kind === 'horizontal') {
        return {
            ok: false,
            error:
                `Laser line "${chosen.id}" is horizontal (y fixed); X cannot be determined from Y alone.`,
        };
    }
    const x = xAtYOnLineModel(model, y);
    if (!Number.isFinite(x)) {
        return { ok: false, error: `Could not compute X on laser line "${chosen.id}" at Y=${y}.` };
    }
    if (!pointInLabBoundsMm(x, y)) {
        return {
            ok: false,
            error: `Stitch pose (X=${x.toFixed(1)}, Y=${y.toFixed(1)}) is outside lab bounds.`,
        };
    }
    return {
        ok: true,
        x,
        y,
        rotation,
        lineId: String(chosen.id),
        lineName: String(chosen.name || chosen.id),
    };
}

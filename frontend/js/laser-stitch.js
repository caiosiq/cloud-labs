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
    const doc = store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines) || doc.lines.length === 0) {
        return { ok: false, error: 'No laser lines loaded. Open laser lines or refresh lab state.' };
    }
    const byId = {};
    doc.lines.forEach((ln) => {
        if (ln && ln.id) byId[ln.id] = ln;
    });

    const requested = lineId != null && String(lineId).trim() ? String(lineId).trim() : '';
    let chosen = null;
    if (requested) {
        chosen = byId[requested] || null;
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
        const snapId = doc.snap_line_id;
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

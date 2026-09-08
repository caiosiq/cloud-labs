/**
 * Alignment guides — versioned pencil overlays (server-backed) + pencil tool +
 * draw / select / move / delete routines.
 *
 * Guides are now part of the versioned configuration: they live on the backend
 * runtime (`/api/guides`, mirrored into `store.labState.alignment_guides`) and
 * are committed / stashed / checked out with everything else. `store.guideLines`
 * is a local mirror kept in sync from lab-state polling; mutations go through the
 * API and the live runtime becomes "dirty" like any other edit.
 *
 * localStorage (`optics_alignment_guides_v1`) is now only a one-time migration
 * source — see {@link migrateLocalGuidesOnce}.
 */
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import { showConfirmationModal } from '../ui/modals.js';
import { backendHeaders, withBackendQuery } from '../state/backend-selection.js';
import {
    ALIGNMENT_SHOW_INTERSECTION_MARKERS,
    constrainGuideEndWithShift,
    getAlignmentIntersectionOutOfBounds,
    refreshAlignmentIntersectionCache,
} from './alignment-snap.js';

const GUIDE_LINES_STORAGE_KEY = 'optics_alignment_guides_v1';
const GUIDE_MIGRATION_FLAG = 'optics_lines_migrated_v1';
const GUIDE_MIN_LENGTH_MM = 2;
/** Click/hover hit threshold for selecting a guide, in screen px. */
const GUIDE_HIT_PX = 7;
/** Endpoint handle hit radius when a guide is selected (screen px). */
const GUIDE_ENDPOINT_HIT_PX = 11;

/** Per-guide undo/redo stacks of `{ p1, p2 }` snapshots (while selected). */
const _guideHistory = new Map();

let _ctx = null;
let _canvas = null;
let _canvasWidth = 0;
let _canvasHeight = 0;
let _mmToPx = (x, y) => ({ x, y });
let _pxToMm = (x, y) => ({ x, y });
let _render = () => {};
let _refreshControlWorkingState = async () => {};

/**
 * One-time DI for canvas drawing + interaction.
 * @param {{
 *   canvas: HTMLCanvasElement,
 *   ctx: CanvasRenderingContext2D,
 *   canvasWidth: number,
 *   canvasHeight: number,
 *   mmToPx: (x:number, y:number) => { x:number, y:number },
 *   pxToMm: (x:number, y:number) => { x:number, y:number },
 *   render: () => void,
 *   refreshControlWorkingState?: () => Promise<void>,
 * }} deps
 */
export function initGuides(deps) {
    _canvas = deps.canvas;
    _ctx = deps.ctx;
    _canvasWidth = deps.canvasWidth;
    _canvasHeight = deps.canvasHeight;
    _mmToPx = deps.mmToPx;
    _pxToMm = deps.pxToMm;
    _render = deps.render;
    if (typeof deps.refreshControlWorkingState === 'function') {
        _refreshControlWorkingState = deps.refreshControlWorkingState;
    }
}

/**
 * Temporarily direct guide drawing to another canvas context.
 *
 * The table PNG exporter uses this to redraw guides into its high-resolution
 * off-screen canvas without disturbing the interactive on-screen canvas.
 * The callback must be synchronous so the original context can always be
 * restored before browser event handling resumes.
 *
 * @template T
 * @param {CanvasRenderingContext2D} ctx
 * @param {() => T} callback
 * @returns {T}
 */
export function withGuideRenderContext(ctx, callback) {
    const previous = _ctx;
    _ctx = ctx;
    try {
        return callback();
    } finally {
        _ctx = previous;
    }
}

function _validGuide(g) {
    return (
        g &&
        g.p1 &&
        g.p2 &&
        [g.p1.x, g.p1.y, g.p2.x, g.p2.y].every((v) => Number.isFinite(Number(v)))
    );
}

/** Mirror the server's guide set into `store.guideLines` (skips mid-edit). */
export function syncGuidesFromLabState() {
    if (store.guideDrag || store.guideDraw || store.guideEndpointDrag) return;
    const incoming = store.labState && store.labState.alignment_guides;
    if (!Array.isArray(incoming)) return;
    const next = incoming.filter(_validGuide).map((g) => ({
        id: String(g.id),
        p1: { x: Number(g.p1.x), y: Number(g.p1.y) },
        p2: { x: Number(g.p2.x), y: Number(g.p2.y) },
    }));
    const changed = JSON.stringify(next) !== JSON.stringify(store.guideLines || []);
    if (!changed) return;
    store.guideLines = next;
    if (store.selectedGuideId && !next.some((g) => g.id === store.selectedGuideId)) {
        store.selectedGuideId = null;
    }
    refreshAlignmentIntersectionCache();
}

// ---- API helpers ----------------------------------------------------------

async function _guidesApi(method, path, body) {
    const res = await fetch(withBackendQuery(path), {
        method,
        headers: body
            ? backendHeaders({ 'Content-Type': 'application/json' })
            : backendHeaders(),
        body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data && (data.detail?.message || data.detail || data.message);
        throw new Error(detail || res.statusText);
    }
    return data;
}

function _applyGuidesResponse(data) {
    if (data && Array.isArray(data.guides)) {
        store.guideLines = data.guides
            .filter(_validGuide)
            .map((g) => ({
                id: String(g.id),
                p1: { x: Number(g.p1.x), y: Number(g.p1.y) },
                p2: { x: Number(g.p2.x), y: Number(g.p2.y) },
            }));
        refreshAlignmentIntersectionCache();
    }
    void _refreshControlWorkingState();
    _render();
}

// ---- Drawing --------------------------------------------------------------

function drawAlignmentIntersectionDot(px, py, { fill, stroke, r = 4 }) {
    _ctx.save();
    _ctx.fillStyle = fill;
    _ctx.strokeStyle = stroke;
    _ctx.lineWidth = 2;
    _ctx.shadowBlur = 6;
    _ctx.shadowColor = fill;
    _ctx.beginPath();
    _ctx.arc(px, py, r, 0, Math.PI * 2);
    _ctx.fill();
    _ctx.stroke();
    _ctx.restore();
}

export function drawAlignmentIntersectionMarkers() {
    if (!ALIGNMENT_SHOW_INTERSECTION_MARKERS) return;
    const intersections = refreshAlignmentIntersectionCache();
    intersections.forEach((ix) => {
        const p = _mmToPx(ix.x, ix.y);
        drawAlignmentIntersectionDot(p.x, p.y, {
            fill: '#fbbf24',
            stroke: '#0f172a',
        });
    });
    getAlignmentIntersectionOutOfBounds().forEach((ix) => {
        const p = _mmToPx(ix.x, ix.y);
        if (p.x < -40 || p.x > _canvasWidth + 40 || p.y < -40 || p.y > _canvasHeight + 40) {
            return;
        }
        drawAlignmentIntersectionDot(p.x, p.y, {
            fill: 'rgba(168, 85, 247, 0.55)',
            stroke: '#c4b5fd',
            r: 3,
        });
    });
}

/**
 * @param {Array|null} guidesOverride When provided (a node preview's
 *   `alignment_guides`), render exactly those guides read-only — no selection
 *   highlight and no in-progress draw handle. Otherwise render the live set.
 */
export function drawAlignmentGuides(guidesOverride = null) {
    const isPreview = Array.isArray(guidesOverride);
    const guides = isPreview ? guidesOverride : (store.guideLines || []);
    guides.forEach((g) => {
        if (!g || !g.p1 || !g.p2) return;
        const a = _mmToPx(g.p1.x, g.p1.y);
        const b = _mmToPx(g.p2.x, g.p2.y);
        const selected = !isPreview && g.id && g.id === store.selectedGuideId;
        _ctx.strokeStyle = selected ? 'rgba(250, 204, 21, 0.95)' : 'rgba(34, 211, 238, 0.9)';
        _ctx.lineWidth = selected ? 2.5 : 1.5;
        _ctx.setLineDash([4, 6]);
        _ctx.shadowBlur = selected ? 8 : 6;
        _ctx.shadowColor = selected ? 'rgba(250, 204, 21, 0.5)' : 'rgba(34, 211, 238, 0.45)';
        _ctx.beginPath();
        _ctx.moveTo(a.x, a.y);
        _ctx.lineTo(b.x, b.y);
        _ctx.stroke();
        _ctx.setLineDash([]);
        _ctx.shadowBlur = 0;
        if (selected) {
            [a, b].forEach((pt) => {
                _ctx.fillStyle = '#facc15';
                _ctx.strokeStyle = '#0f172a';
                _ctx.lineWidth = 1.5;
                _ctx.beginPath();
                _ctx.arc(pt.x, pt.y, 6, 0, Math.PI * 2);
                _ctx.fill();
                _ctx.stroke();
            });
        }
    });

    if (isPreview) return;

    const gd = store.guideDraw;
    if (gd && gd.startLab && gd.currentLab) {
        const a = _mmToPx(gd.startLab.x, gd.startLab.y);
        const b = _mmToPx(gd.currentLab.x, gd.currentLab.y);
        _ctx.strokeStyle = 'rgba(56, 189, 248, 0.95)';
        _ctx.lineWidth = 2;
        _ctx.setLineDash([6, 4]);
        _ctx.beginPath();
        _ctx.moveTo(a.x, a.y);
        _ctx.lineTo(b.x, b.y);
        _ctx.stroke();
        _ctx.setLineDash([]);
    }
}

export function updatePencilToolButtonUi() {
    const btn = document.getElementById('pencil-tool-btn');
    if (!btn) return;
    const on = !!store.pencilToolActive;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (_canvas) {
        _canvas.style.cursor = on ? 'crosshair' : '';
    }
}

// ---- Hit-testing / selection ----------------------------------------------

function _distPointToSegmentPx(px, py, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len2 = dx * dx + dy * dy;
    if (len2 === 0) return Math.hypot(px - a.x, py - a.y);
    let t = ((px - a.x) * dx + (py - a.y) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (a.x + t * dx), py - (a.y + t * dy));
}

/** Return the guide id nearest to a canvas px point (within threshold), else null. */
export function hitTestGuide(canvasX, canvasY) {
    let best = null;
    let bestDist = GUIDE_HIT_PX;
    (store.guideLines || []).forEach((g) => {
        if (!_validGuide(g)) return;
        const a = _mmToPx(g.p1.x, g.p1.y);
        const b = _mmToPx(g.p2.x, g.p2.y);
        const d = _distPointToSegmentPx(canvasX, canvasY, a, b);
        if (d < bestDist) {
            bestDist = d;
            best = g.id;
        }
    });
    return best;
}

export function selectGuide(id) {
    store.selectedGuideId = id || null;
    _render();
}

export function clearGuideSelection() {
    if (store.selectedGuideId) {
        store.selectedGuideId = null;
        _render();
    }
}

export async function deleteSelectedGuide() {
    const id = store.selectedGuideId;
    if (!id) return;
    store.selectedGuideId = null;
    _guideHistory.delete(id);
    try {
        const data = await _guidesApi('DELETE', `/api/guides/${encodeURIComponent(id)}`);
        _applyGuidesResponse(data);
        log('Alignment guide deleted.', 'info');
    } catch (e) {
        log(`Delete guide failed: ${e.message || e}`, 'error');
        _render();
    }
}

function _clonePose(p) {
    return { x: Number(p.x), y: Number(p.y) };
}

function _cloneGuideGeometry(g) {
    return { p1: _clonePose(g.p1), p2: _clonePose(g.p2) };
}

function _historyFor(id) {
    if (!_guideHistory.has(id)) {
        _guideHistory.set(id, { undo: [], redo: [] });
    }
    return _guideHistory.get(id);
}

function _recordUndo(id, p1, p2) {
    const h = _historyFor(id);
    h.undo.push({ p1: _clonePose(p1), p2: _clonePose(p2) });
    h.redo = [];
}

function _revertGuideLocal(id, p1, p2) {
    const g = (store.guideLines || []).find((x) => x.id === id);
    if (!g) return;
    g.p1 = _clonePose(p1);
    g.p2 = _clonePose(p2);
    refreshAlignmentIntersectionCache();
    _render();
}

async function _patchGuideGeometry(id, p1, p2) {
    const data = await _guidesApi('PATCH', `/api/guides/${encodeURIComponent(id)}`, {
        p1,
        p2,
    });
    _applyGuidesResponse(data);
}

export function canUndoGuide(id) {
    if (!id) return false;
    const h = _guideHistory.get(id);
    return !!(h && h.undo.length);
}

export function canRedoGuide(id) {
    if (!id) return false;
    const h = _guideHistory.get(id);
    return !!(h && h.redo.length);
}

/** Ctrl+Z — revert the last committed guide geometry change (with confirmation). */
export function requestGuideUndo() {
    const id = store.selectedGuideId;
    if (!id || !canUndoGuide(id)) return;
    const g = (store.guideLines || []).find((x) => x.id === id);
    if (!g) return;
    const h = _historyFor(id);
    const prev = h.undo[h.undo.length - 1];
    showConfirmationModal(
        'Undo the last guide move/resize? This updates the bench (uncommitted change).',
        () => {
            void (async () => {
                h.undo.pop();
                h.redo.push(_cloneGuideGeometry(g));
                try {
                    await _patchGuideGeometry(id, prev.p1, prev.p2);
                    log('Guide change undone.', 'info');
                } catch (e) {
                    h.undo.push(prev);
                    h.redo.pop();
                    log(`Undo guide failed: ${e.message || e}`, 'error');
                }
            })();
        },
        null,
        { confirmLabel: 'Undo', cancelLabel: 'Keep' },
    );
}

/** Ctrl+Y — re-apply an undone guide geometry change (with confirmation). */
export function requestGuideRedo() {
    const id = store.selectedGuideId;
    if (!id || !canRedoGuide(id)) return;
    const g = (store.guideLines || []).find((x) => x.id === id);
    if (!g) return;
    const h = _historyFor(id);
    const next = h.redo[h.redo.length - 1];
    showConfirmationModal(
        'Redo the guide change? This updates the bench (uncommitted change).',
        () => {
            void (async () => {
                h.redo.pop();
                h.undo.push(_cloneGuideGeometry(g));
                try {
                    await _patchGuideGeometry(id, next.p1, next.p2);
                    log('Guide change redone.', 'info');
                } catch (e) {
                    h.redo.push(next);
                    h.undo.pop();
                    log(`Redo guide failed: ${e.message || e}`, 'error');
                }
            })();
        },
        null,
        { confirmLabel: 'Redo', cancelLabel: 'Cancel' },
    );
}

/** Return `'p1'`, `'p2'`, or null for a selected guide's endpoint handles. */
export function hitTestGuideEndpoint(canvasX, canvasY) {
    const id = store.selectedGuideId;
    if (!id) return null;
    const g = (store.guideLines || []).find((x) => x.id === id);
    if (!_validGuide(g)) return null;
    const a = _mmToPx(g.p1.x, g.p1.y);
    const b = _mmToPx(g.p2.x, g.p2.y);
    if (Math.hypot(canvasX - a.x, canvasY - a.y) <= GUIDE_ENDPOINT_HIT_PX) return 'p1';
    if (Math.hypot(canvasX - b.x, canvasY - b.y) <= GUIDE_ENDPOINT_HIT_PX) return 'p2';
    return null;
}

// ---- Guide translate (whole line — confirm before commit) ----------------

export function beginGuideDrag(id, mouseLab) {
    const g = (store.guideLines || []).find((x) => x.id === id);
    if (!g) return false;
    store.guideDrag = {
        id,
        mouseLab: { x: mouseLab.x, y: mouseLab.y },
        p1: { x: g.p1.x, y: g.p1.y },
        p2: { x: g.p2.x, y: g.p2.y },
    };
    return true;
}

/** Translate the dragged guide locally; returns true if a drag is active. */
export function updateGuideDrag(mouseLab) {
    const gd = store.guideDrag;
    if (!gd) return false;
    const dx = mouseLab.x - gd.mouseLab.x;
    const dy = mouseLab.y - gd.mouseLab.y;
    const g = (store.guideLines || []).find((x) => x.id === gd.id);
    if (g) {
        g.p1 = { x: gd.p1.x + dx, y: gd.p1.y + dy };
        g.p2 = { x: gd.p2.x + dx, y: gd.p2.y + dy };
        refreshAlignmentIntersectionCache();
        _render();
    }
    return true;
}

/** Finish a whole-line drag: ask for confirmation before PATCH. */
export function finishGuideDrag() {
    const gd = store.guideDrag;
    store.guideDrag = null;
    if (!gd) return;
    const g = (store.guideLines || []).find((x) => x.id === gd.id);
    if (!g) return;
    const moved =
        Math.hypot(g.p1.x - gd.p1.x, g.p1.y - gd.p1.y) > 0.1 ||
        Math.hypot(g.p2.x - gd.p2.x, g.p2.y - gd.p2.y) > 0.1;
    if (!moved) {
        _render();
        return;
    }
    showConfirmationModal(
        'Move this alignment guide? The bench will be marked as having uncommitted changes.',
        () => {
            void (async () => {
                try {
                    _recordUndo(gd.id, gd.p1, gd.p2);
                    await _patchGuideGeometry(gd.id, g.p1, g.p2);
                    log('Alignment guide moved (uncommitted change).', 'info');
                } catch (e) {
                    _revertGuideLocal(gd.id, gd.p1, gd.p2);
                    const h = _historyFor(gd.id);
                    if (h.undo.length) h.undo.pop();
                    log(`Move guide failed: ${e.message || e}`, 'error');
                }
            })();
        },
        () => {
            _revertGuideLocal(gd.id, gd.p1, gd.p2);
        },
        { confirmLabel: 'Move guide', cancelLabel: 'Cancel' },
    );
}

// ---- Guide endpoint resize (drag handles — commit on release) --------------

export function beginGuideEndpointDrag(id, endpoint, mouseLab) {
    const g = (store.guideLines || []).find((x) => x.id === id);
    if (!g || (endpoint !== 'p1' && endpoint !== 'p2')) return false;
    store.guideEndpointDrag = {
        id,
        endpoint,
        origP1: { x: g.p1.x, y: g.p1.y },
        origP2: { x: g.p2.x, y: g.p2.y },
    };
    updateGuideEndpointDrag(mouseLab, false);
    return true;
}

export function updateGuideEndpointDrag(mouseLab, shiftKey) {
    const ed = store.guideEndpointDrag;
    if (!ed) return false;
    const g = (store.guideLines || []).find((x) => x.id === ed.id);
    if (!g) return false;
    const fixed = ed.endpoint === 'p1' ? ed.origP2 : ed.origP1;
    const movingOrig = ed.endpoint === 'p1' ? ed.origP1 : ed.origP2;
    const con = constrainGuideEndWithShift(
        fixed.x,
        fixed.y,
        mouseLab.x,
        mouseLab.y,
        shiftKey,
        {
            refDx: movingOrig.x - fixed.x,
            refDy: movingOrig.y - fixed.y,
        },
    );
    if (ed.endpoint === 'p1') {
        g.p1 = { x: con.x, y: con.y };
    } else {
        g.p2 = { x: con.x, y: con.y };
    }
    refreshAlignmentIntersectionCache();
    _render();
    return true;
}

/** Finish endpoint resize: commit immediately (no confirmation). */
export async function finishGuideEndpointDrag() {
    const ed = store.guideEndpointDrag;
    store.guideEndpointDrag = null;
    if (!ed) return;
    const g = (store.guideLines || []).find((x) => x.id === ed.id);
    if (!g) return;
    const len = Math.hypot(g.p2.x - g.p1.x, g.p2.y - g.p1.y);
    if (len < GUIDE_MIN_LENGTH_MM) {
        _revertGuideLocal(ed.id, ed.origP1, ed.origP2);
        log('Guide is too short — resize cancelled.', 'warn');
        return;
    }
    const changed =
        Math.hypot(g.p1.x - ed.origP1.x, g.p1.y - ed.origP1.y) > 0.1 ||
        Math.hypot(g.p2.x - ed.origP2.x, g.p2.y - ed.origP2.y) > 0.1;
    if (!changed) {
        _render();
        return;
    }
    try {
        _recordUndo(ed.id, ed.origP1, ed.origP2);
        await _patchGuideGeometry(ed.id, g.p1, g.p2);
        log('Guide length updated (uncommitted change).', 'info');
    } catch (e) {
        _revertGuideLocal(ed.id, ed.origP1, ed.origP2);
        const h = _historyFor(ed.id);
        if (h.undo.length) h.undo.pop();
        log(`Resize guide failed: ${e.message || e}`, 'error');
    }
}

// ---- Pencil draw (new guide) ----------------------------------------------

export function bindGuideDrawListeners() {
    const onMove = (ev) => {
        if (!store.guideDraw) return;
        const rect = _canvas.getBoundingClientRect();
        const mx = ev.clientX - rect.left;
        const my = ev.clientY - rect.top;
        const lab = _pxToMm(mx, my);
        const con = constrainGuideEndWithShift(
            store.guideDraw.startLab.x,
            store.guideDraw.startLab.y,
            lab.x,
            lab.y,
            ev.shiftKey,
        );
        store.guideDraw.currentLab = con;
        _render();
    };
    const onKey = (ev) => {
        if (ev.key !== 'Escape') return;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        window.removeEventListener('keydown', onKey);
        store.guideDraw = null;
        log('Guide draw cancelled.', 'info');
        _render();
    };
    const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        window.removeEventListener('keydown', onKey);
        void finishGuideDraw();
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKey, { passive: true });
}

async function finishGuideDraw() {
    const gd = store.guideDraw;
    store.guideDraw = null;
    if (!gd || !gd.startLab || !gd.currentLab) {
        _render();
        return;
    }
    const dx = gd.currentLab.x - gd.startLab.x;
    const dy = gd.currentLab.y - gd.startLab.y;
    if (Math.hypot(dx, dy) < GUIDE_MIN_LENGTH_MM) {
        _render();
        return;
    }
    try {
        const data = await _guidesApi('POST', '/api/guides', {
            p1: { x: gd.startLab.x, y: gd.startLab.y },
            p2: { x: gd.currentLab.x, y: gd.currentLab.y },
        });
        _applyGuidesResponse(data);
        log('Alignment guide added (uncommitted change).', 'info');
    } catch (e) {
        log(`Add guide failed: ${e.message || e}`, 'error');
        _render();
    }
}

export async function clearAllGuides() {
    try {
        const data = await _guidesApi('PUT', '/api/guides', { guides: [] });
        store.selectedGuideId = null;
        _applyGuidesResponse(data);
        log('Cleared drawn alignment guides.', 'info');
    } catch (e) {
        log(`Clear guides failed: ${e.message || e}`, 'error');
    }
}

// ---- One-time localStorage → server migration -----------------------------

export async function migrateLocalGuidesOnce() {
    if (localStorage.getItem(GUIDE_MIGRATION_FLAG)) return;
    let local = [];
    try {
        const raw = localStorage.getItem(GUIDE_LINES_STORAGE_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) local = parsed.filter(_validGuide);
        }
    } catch (e) {
        local = [];
    }
    try {
        if (local.length) {
            // Seed the live runtime with the browser's guides first…
            await fetch(withBackendQuery('/api/guides'), {
                method: 'PUT',
                headers: backendHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ guides: local }),
            });
        }
        // …then backfill every existing commit (all repos) with today's lines so
        // history isn't retroactively "missing" the guides/laser lines.
        await fetch(withBackendQuery('/api/control/backfill-lines'), {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({}),
        });
        localStorage.setItem(GUIDE_MIGRATION_FLAG, '1');
        log('Alignment lines migrated into version control.', 'info');
    } catch (e) {
        console.warn('Alignment line migration failed', e);
    }
}

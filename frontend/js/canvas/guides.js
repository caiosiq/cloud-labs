/**
 * Alignment guides — local-storage backed user pencil lines + pencil tool wiring + draw routines.
 *
 * Renders both the user guide segments and the junction markers (which include laser ↔ guide and
 * laser ↔ laser crossings discovered by the alignment snap engine).
 *
 * Drawing routines need a 2D canvas context + mm→px transform; the pencil tool drag listener
 * triggers a re-render. These collaborators are injected via {@link initGuides} so the module
 * itself stays free of direct top-level DOM lookups.
 */
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import {
    ALIGNMENT_SHOW_INTERSECTION_MARKERS,
    constrainGuideEndWithShift,
    getAlignmentIntersectionOutOfBounds,
    refreshAlignmentIntersectionCache,
} from './alignment-snap.js';

const GUIDE_LINES_STORAGE_KEY = 'optics_alignment_guides_v1';
const GUIDE_MIN_LENGTH_MM = 2;

let _ctx = null;
let _canvas = null;
let _canvasWidth = 0;
let _canvasHeight = 0;
let _mmToPx = (x, y) => ({ x, y });
let _pxToMm = (x, y) => ({ x, y });
let _render = () => {};

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
}

export function loadGuideLinesFromStorage() {
    try {
        const raw = localStorage.getItem(GUIDE_LINES_STORAGE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        store.guideLines = parsed.filter(
            (g) =>
                g &&
                g.p1 &&
                g.p2 &&
                [g.p1.x, g.p1.y, g.p2.x, g.p2.y].every((v) => Number.isFinite(Number(v))),
        );
    } catch (e) {
        console.warn('Alignment guides load failed', e);
        store.guideLines = [];
    }
}

export function saveGuideLinesToStorage() {
    try {
        localStorage.setItem(GUIDE_LINES_STORAGE_KEY, JSON.stringify(store.guideLines || []));
    } catch (e) {
        console.warn('Alignment guides save failed', e);
    }
}

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

export function drawAlignmentGuides() {
    const guides = store.guideLines || [];
    guides.forEach((g) => {
        if (!g || !g.p1 || !g.p2) return;
        const a = _mmToPx(g.p1.x, g.p1.y);
        const b = _mmToPx(g.p2.x, g.p2.y);
        _ctx.strokeStyle = 'rgba(34, 211, 238, 0.9)';
        _ctx.lineWidth = 1.5;
        _ctx.setLineDash([4, 6]);
        _ctx.shadowBlur = 6;
        _ctx.shadowColor = 'rgba(34, 211, 238, 0.45)';
        _ctx.beginPath();
        _ctx.moveTo(a.x, a.y);
        _ctx.lineTo(b.x, b.y);
        _ctx.stroke();
        _ctx.setLineDash([]);
        _ctx.shadowBlur = 0;
    });

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
        finishGuideDraw();
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKey, { passive: true });
}

function finishGuideDraw() {
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
    if (!Array.isArray(store.guideLines)) store.guideLines = [];
    store.guideLines.push({
        id: `g_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
        p1: { x: gd.startLab.x, y: gd.startLab.y },
        p2: { x: gd.currentLab.x, y: gd.currentLab.y },
    });
    saveGuideLinesToStorage();
    refreshAlignmentIntersectionCache();
    log('Alignment guide added. Drag components near it to snap (with laser lines).', 'info');
    _render();
}

/**
 * Canvas rendering pipeline.
 *
 * `render()` paints in this order so the depth stacking matches operator intuition:
 *   1. Background gradient + danger zone + axes + breadboard grid (`clearCanvas`).
 *   2. Storage region rectangle and Q3 cell grid (`drawStorageZone`).
 *   3. Laser segments from `store.laserLinesDoc` (`drawLaserPath`).
 *   4. User-drawn alignment guides + intersection markers (delegated to `guides`).
 *   5. Solid physical components from `store.labState.components`.
 *   6. Ghost components from `store.ghostState` (per-tag intent the user is editing).
 *   7. Optimization metric mini-graph (MOCK lab only).
 *
 * "Pending overlays" (amber / purple halos + labels) come from `pendingOverlayStyle` so the
 * canvas matches the system-status badge and holding banner colors.
 */
import {
    BREADBOARD_GRID_OFFSET_X_MM,
    BREADBOARD_GRID_OFFSET_Y_MM,
    BREADBOARD_GRID_SPACING_MM,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DANGER_RADIUS_MM,
    FRAME_SAFETY_CLEARANCE_MM,
    LAB_CENTER_PX,
    LAB_SCALE,
    LAB_X_MAX,
    LAB_X_MIN,
    LAB_Y_MAX,
    LAB_Y_MIN,
    MANUAL_MOTION_CORNER_CUTOFF_MM,
    STORAGE_RECT_X_MIN,
    STORAGE_RECT_X_MAX,
    STORAGE_RECT_Y_MIN,
    STORAGE_RECT_Y_MAX,
} from '../config.js';
import { mmToPx } from './coordinates.js';
import { store } from '../state/store.js';
import {
    catalogDeclaresTablePose,
    drawPose,
    isHeldTag,
    isStoredComponent,
    shouldRenderOnCanvas,
} from '../component-model.js';
import { isTeleopReady } from '../component-state.js';
import { clipTwoPointLineToLabBounds } from '../geometry/lines.js';
import { drawAlignmentGuides, drawAlignmentIntersectionMarkers } from './guides.js';
import { getComponentSize } from './interaction.js';
import { placementUiLabel } from '../ui/context-panel.js';
import { occupiedStorageSlots, storageSlotCenterPose } from '../storage-region.js';
import {
    getOptimizationHighlightForTag,
    optimizationHighlightColor,
} from '../state/optimization-builder.js';

let _ctx = null;

// Approximate outward footprint of the arm, wrist camera, and gripper during an edge pickup.
// This is visual guidance only and does not affect motion validation.
const APPROX_ROBOT_EDGE_ENVELOPE_MM = 75;
const APPROX_COMPONENT_RADIUS_MM = 48.3;

/**
 * Resolve and cache the 2D canvas context. Returns true on success, false if the canvas element
 * isn't in the DOM yet.
 * @returns {boolean}
 */
export function initRender() {
    const canvas = document.getElementById('optical-table');
    if (!canvas) return false;
    _ctx = canvas.getContext('2d');
    return _ctx != null;
}

function drawApproximateUsableArea() {
    if (!store.showUsableAreaOverlay) return;

    // The shaded boundary describes the component's physical footprint, not its center.
    // Expand the center-safe workspace by one representative component radius.
    const xMin = LAB_X_MIN + FRAME_SAFETY_CLEARANCE_MM
        + APPROX_ROBOT_EDGE_ENVELOPE_MM - APPROX_COMPONENT_RADIUS_MM;
    const xMax = LAB_X_MAX - FRAME_SAFETY_CLEARANCE_MM
        - APPROX_ROBOT_EDGE_ENVELOPE_MM + APPROX_COMPONENT_RADIUS_MM;
    const yMin = LAB_Y_MIN + FRAME_SAFETY_CLEARANCE_MM
        + APPROX_ROBOT_EDGE_ENVELOPE_MM - APPROX_COMPONENT_RADIUS_MM;
    const yMax = LAB_Y_MAX - FRAME_SAFETY_CLEARANCE_MM
        - APPROX_ROBOT_EDGE_ENVELOPE_MM + APPROX_COMPONENT_RADIUS_MM;
    if (xMin >= xMax || yMin >= yMax) return;

    const maxCorner = Math.min(
        Math.abs(xMin),
        Math.abs(xMax),
        Math.abs(yMin),
        Math.abs(yMax),
    );
    const centerCorner = MANUAL_MOTION_CORNER_CUTOFF_MM || maxCorner;
    const corner = Math.min(centerCorner + APPROX_COMPONENT_RADIUS_MM, maxCorner);
    const points = [
        [-corner, yMin], [corner, yMin], [corner, -corner],
        [xMax, -corner], [xMax, corner], [corner, corner],
        [corner, yMax], [-corner, yMax], [-corner, corner],
        [xMin, corner], [xMin, -corner], [-corner, -corner],
    ];

    const ctx = _ctx;
    ctx.beginPath();
    points.forEach(([x, y], index) => {
        const p = mmToPx(x, y);
        if (index === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
    });
    ctx.closePath();
    ctx.arc(
        LAB_CENTER_PX.x,
        LAB_CENTER_PX.y,
        DANGER_RADIUS_MM * LAB_SCALE,
        0,
        Math.PI * 2,
    );
    ctx.fillStyle = 'rgba(34, 197, 94, 0.12)';
    ctx.fill('evenodd');

    ctx.beginPath();
    points.forEach(([x, y], index) => {
        const p = mmToPx(x, y);
        if (index === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
    });
    ctx.closePath();
    ctx.strokeStyle = 'rgba(34, 197, 94, 0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
}

function clearCanvas() {
    const ctx = _ctx;
    const gradient = ctx.createLinearGradient(0, 0, 0, CANVAS_HEIGHT);
    gradient.addColorStop(0, '#1a1d21');
    gradient.addColorStop(1, '#141619');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

    drawApproximateUsableArea();

    // Danger zone: R ≈ 63 mm circle around the robot origin, drawn as a reddish disk + dashed
    // outline so the operator instinctively avoids it during manual drags.
    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, DANGER_RADIUS_MM * LAB_SCALE, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(239, 68, 68, 0.1)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = 'rgba(239, 68, 68, 0.5)';
    ctx.font = '10px Inter';
    ctx.fillText('DANGER ZONE', LAB_CENTER_PX.x - 30, LAB_CENTER_PX.y - 10);

    // X axis (red, right) and Y axis (green, up). Canvas Y is inverted relative to lab Y but
    // `mmToPx` already accounts for that — +labY maps to -canvasY (up on screen).
    ctx.beginPath();
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x + 50, LAB_CENTER_PX.y);
    ctx.stroke();
    ctx.fillStyle = '#ef4444';
    ctx.fillText('X', LAB_CENTER_PX.x + 55, LAB_CENTER_PX.y + 4);

    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y - 50);
    ctx.stroke();
    ctx.fillStyle = '#10b981';
    ctx.fillText('Y', LAB_CENTER_PX.x - 4, LAB_CENTER_PX.y - 55);

    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();

    // Breadboard hole grid (25 mm spacing). The X offset comes from
    // `BREADBOARD_GRID_OFFSET_X_MM` so the rendered dots line up with the physical column of
    // holes — without this the grid drifts visibly on real hardware.
    ctx.fillStyle = '#2a2e36';
    for (let xMm = LAB_X_MIN; xMm <= LAB_X_MAX; xMm += BREADBOARD_GRID_SPACING_MM) {
        for (let yMm = LAB_Y_MIN; yMm <= LAB_Y_MAX; yMm += BREADBOARD_GRID_SPACING_MM) {
            const p = mmToPx(xMm + BREADBOARD_GRID_OFFSET_X_MM, yMm + BREADBOARD_GRID_OFFSET_Y_MM);
            if (p.x >= 0 && p.x <= CANVAS_WIDTH && p.y >= 0 && p.y <= CANVAS_HEIGHT) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
                ctx.fill();
            }
        }
    }
}

/** Visual inventory region: rectangle toward table center (from layout storage_grid.q3). */
function drawStorageZone() {
    const ctx = _ctx;
    const sx = STORAGE_RECT_X_MIN;
    const ex = STORAGE_RECT_X_MAX;
    const sy = STORAGE_RECT_Y_MIN;
    const ey = STORAGE_RECT_Y_MAX;
    const pSw = mmToPx(sx, sy);
    const pSe = mmToPx(ex, sy);
    const pNe = mmToPx(ex, ey);
    const pNw = mmToPx(sx, ey);
    ctx.beginPath();
    ctx.moveTo(pSw.x, pSw.y);
    ctx.lineTo(pSe.x, pSe.y);
    ctx.lineTo(pNe.x, pNe.y);
    ctx.lineTo(pNw.x, pNw.y);
    ctx.closePath();
    ctx.fillStyle = 'rgba(59, 130, 246, 0.07)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(59, 130, 246, 0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(148, 163, 184, 0.95)';
    ctx.font = '11px Inter, sans-serif';
    ctx.fillText('Storage', pSw.x + 10, pSw.y - 10);

    const spec = store.storageGridSpec;
    if (!spec || !spec.nx || !spec.ny) return;
    const { nx, ny, cell_width_mm: cw, cell_height_mm: ch, q3 } = spec;
    const x0 = q3.x_min;
    const x1 = q3.x_max;
    const y0 = q3.y_min;
    const y1 = q3.y_max;
    ctx.strokeStyle = 'rgba(96, 165, 250, 0.55)';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    for (let i = 0; i <= nx; i++) {
        const xm = x0 + i * cw;
        const a = mmToPx(xm, y0);
        const b = mmToPx(xm, y1);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
    }
    for (let j = 0; j <= ny; j++) {
        const ym = y0 + j * ch;
        const a = mmToPx(x0, ym);
        const b = mmToPx(x1, ym);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
    }

    // Choose-slot mode: tint free (green) vs occupied (red) cells.
    if (store.storeToSlotTag) {
        const occ = occupiedStorageSlots(store.storeToSlotTag);
        for (let j = 0; j < ny; j++) {
            for (let i = 0; i < nx; i++) {
                const key = `${i},${j}`;
                const xa = x0 + i * cw;
                const xb = x0 + (i + 1) * cw;
                const ya = y0 + j * ch;
                const yb = y0 + (j + 1) * ch;
                const sw = mmToPx(xa, ya);
                const se = mmToPx(xb, ya);
                const ne = mmToPx(xb, yb);
                const nw = mmToPx(xa, yb);
                ctx.beginPath();
                ctx.moveTo(sw.x, sw.y);
                ctx.lineTo(se.x, se.y);
                ctx.lineTo(ne.x, ne.y);
                ctx.lineTo(nw.x, nw.y);
                ctx.closePath();
                ctx.fillStyle = occ.has(key)
                    ? 'rgba(248, 113, 113, 0.28)'
                    : 'rgba(52, 211, 153, 0.22)';
                ctx.fill();
                // Subtle index for power users while choosing.
                const mid = mmToPx((xa + xb) / 2, (ya + yb) / 2);
                ctx.fillStyle = occ.has(key)
                    ? 'rgba(254, 202, 202, 0.9)'
                    : 'rgba(167, 243, 208, 0.95)';
                ctx.font = '10px ui-monospace, monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(`${i},${j}`, mid.x, mid.y);
            }
        }
        ctx.textAlign = 'start';
        ctx.textBaseline = 'alphabetic';
        ctx.fillStyle = 'rgba(103, 232, 249, 0.95)';
        ctx.font = '11px Inter, sans-serif';
        ctx.fillText('Pick a free cell', pSw.x + 10, pSe.y + 14);
    }
}

function drawLaserPath(docOverride) {
    const ctx = _ctx;
    const doc = docOverride || store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines)) return;
    doc.lines.forEach((line) => {
        if (!line || line.enabled === false || !line.p1 || !line.p2) return;
        const col = line.color || '#ff3b3b';
        const seg = clipTwoPointLineToLabBounds(line.p1, line.p2);
        if (!seg) return;
        const p1 = mmToPx(seg[0].x, seg[0].y);
        const p2 = mmToPx(seg[1].x, seg[1].y);
        ctx.shadowBlur = 10;
        ctx.shadowColor = col;
        ctx.strokeStyle = col;
        ctx.lineWidth = 2;
        ctx.setLineDash([10, 10]);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
    });
}

/**
 * Base purple used by the HOLDING system status badge and reused here for the steady-state
 * "HOLDING" ghost overlay (solid halo + label) so the canvas stays visually consistent with the
 * top-right status pill and the holding banner.
 */
const HOLDING_STEADY_COLOR = '#a855f7';

const TELEOP_TARGET_COLOR = '#fbbf24';
const TELEOP_LIVE_COLOR = '#22d3ee';

/**
 * Style + label for the amber / purple "in flight" overlay drawn on the ghost while
 * `store.pendingCommands` is non-empty for `name`.
 *
 * In-air actions (PICK/HOVER/PLACE_FROM_HOVER) render in purple with a
 * descriptive label — e.g. HOVERING... — instead of the generic amber "MOVING..." used for
 * everything else, to match the HOLDING status badge.
 */
function pendingOverlayStyle(name) {
    const action = store.pendingActions.get(name);
    switch (action) {
        case 'PICK_COMPONENT':
            return { color: '#a855f7', label: 'PICKING UP...' };
        case 'HOVER':
            return { color: '#a855f7', label: 'HOVERING...' };
        case 'PLACE_FROM_HOVER':
            return { color: '#a855f7', label: 'PLACING...' };
        default:
            return { color: '#f59e0b', label: 'MOVING...' };
    }
}

function drawComponent(name, pose, type, mode = 'SOLID') {
    const ctx = _ctx;
    const p = mmToPx(pose.x, pose.y);
    const x = p.x;
    const y = p.y;
    const rotation = pose.rotation * (Math.PI / 180);

    // LAB_SCALE is px/mm, so width_px = width_mm × LAB_SCALE.
    const size = getComponentSize(name);
    const w = size.width * LAB_SCALE;
    const h = size.height * LAB_SCALE;
    const halfW = w / 2;
    const halfH = h / 2;
    const optRole = getOptimizationHighlightForTag(name);

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rotation);

    if (mode === 'GHOST') ctx.globalAlpha = 0.5;
    if (mode === 'PENDING') ctx.globalAlpha = 0.7;
    if (mode === 'HOLDING') ctx.globalAlpha = 0.75;

    const isTranslucent = mode === 'GHOST' || mode === 'HOLDING';
    ctx.shadowColor = isTranslucent ? 'transparent' : 'rgba(0,0,0,0.5)';
    ctx.shadowBlur = isTranslucent ? 0 : 10;

    // Multi-panel selection halo:
    //   - any component with an OPEN panel gets a soft secondary outline so
    //     the operator can see at a glance which parts have a dock window.
    //   - the FOCUSED panel's component gets the bright primary outline
    //     (matches the historical single-selection look exactly).
    // Circumscribed circle covers oblong shapes too.
    const haloR = Math.sqrt(halfW * halfW + halfH * halfH) + 5;
    if (store.openPanels.includes(name) && name !== store.focusedPanel) {
        ctx.strokeStyle = 'rgba(59, 130, 246, 0.45)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.arc(0, 0, haloR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
    }
    if (name === store.focusedPanel) {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(0, 0, haloR, 0, Math.PI * 2);
        ctx.stroke();
    }

    if (optRole) {
        const optColor = optimizationHighlightColor(optRole);
        const rOpt = Math.sqrt(halfW * halfW + halfH * halfH) + (optRole === 'scope' ? 6 : 10);
        ctx.strokeStyle = optColor;
        ctx.lineWidth = optRole === 'scope' ? 2 : 3;
        ctx.setLineDash(optRole === 'scope' ? [5, 4] : []);
        ctx.shadowColor = optColor;
        ctx.shadowBlur = optRole === 'scope' ? 6 : 14;
        ctx.beginPath();
        ctx.arc(0, 0, rOpt, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;
    }

    if (store.dragFromStorageTag === name) {
        ctx.strokeStyle = 'rgba(34, 211, 238, 0.95)';
        ctx.lineWidth = 3;
        ctx.beginPath();
        const rDrag = Math.sqrt(halfW * halfW + halfH * halfH) + 10;
        ctx.arc(0, 0, rDrag, 0, Math.PI * 2);
        ctx.stroke();
    }

    if (mode === 'PENDING') {
        const style = pendingOverlayStyle(name);
        ctx.strokeStyle = style.color;
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 2]);
        const r = Math.sqrt(halfW * halfW + halfH * halfH);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    if (mode === 'HOLDING') {
        ctx.strokeStyle = HOLDING_STEADY_COLOR;
        ctx.lineWidth = 2;
        const r = Math.sqrt(halfW * halfW + halfH * halfH);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.stroke();
    }

    if (store.isOptimizing && store.pendingCommands.has(name)) {
        ctx.shadowColor = '#10b981';
        ctx.shadowBlur = 20;
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        const r = Math.sqrt(halfW * halfW + halfH * halfH);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.stroke();
    }

    // Per-component shape is dispatched by catalog `id` first, then falls back to optical `type`
    // so brand-new tags (or unknown ones) still get a reasonable rendering.
    const catalogItem = store.catalogMap[name];
    const catalogId = catalogItem ? catalogItem.id : null;

    if (catalogId === 'nd_filter') {
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.fillStyle = 'rgba(20, 20, 20, 0.9)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);
    } else if (catalogId === 'filter_generic') {
        ctx.fillStyle = '#333';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#f87171';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.fillStyle = 'rgba(248, 113, 113, 0.3)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);
    } else if (
        catalogId === 'P1' ||
        catalogId === 'tilted_polarizer' ||
        type === 'OPTICAL_POLARIZER'
    ) {
        // Polarizer mount: dark square + circular optic + polarization hatch.
        ctx.fillStyle = '#1a1f2e';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        const r = Math.min(halfW, halfH) * 0.72;
        ctx.fillStyle = 'rgba(56, 189, 248, 0.18)';
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#7dd3fc';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.strokeStyle = 'rgba(251, 191, 36, 0.85)';
        ctx.lineWidth = 1.25;
        const step = Math.max(4, r / 4);
        for (let x = -r; x <= r; x += step) {
            const halfChord = Math.sqrt(Math.max(0, r * r - x * x));
            ctx.beginPath();
            ctx.moveTo(x, -halfChord);
            ctx.lineTo(x, halfChord);
            ctx.stroke();
        }
        // Local +Y arrow = polarization axis cue.
        ctx.strokeStyle = '#fbbf24';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(0, r * 0.55);
        ctx.lineTo(0, -r * 0.55);
        ctx.moveTo(-4, -r * 0.35);
        ctx.lineTo(0, -r * 0.55);
        ctx.lineTo(4, -r * 0.35);
        ctx.stroke();
    } else if (catalogId === 'cam_gripper_1' || catalogId === 'cam_gripper_2' || type === 'OPTICAL_CAMERA') {
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.fillStyle = '#000';
        ctx.beginPath();
        ctx.arc(0, 0, Math.min(w, h) / 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#3b82f6';
        ctx.beginPath();
        ctx.arc(0, 0, Math.min(w, h) / 8, 0, Math.PI * 2);
        ctx.fill();
        // Direction indicator triangle pointing along local +X (90° CW from −Y).
        ctx.fillStyle = '#ef4444';
        const triH = h / 4;
        ctx.beginPath();
        ctx.moveTo(halfW + 2, -triH / 2);
        ctx.lineTo(halfW + triH + 2, 0);
        ctx.lineTo(halfW + 2, triH / 2);
        ctx.fill();
    } else if (catalogId === 'mirror_curved') {
        // Curved (concave) OC: left semicircle in local space, opening toward +local X.
        // Earlier code used ±PI/4 extra on arc angles, which rotated the opening ~45° and made
        // e.g. 270° look like ~225° on the canvas. Use clean -π/2 → +π/2 sweeps instead.
        const radius = Math.min(w, h) / 2;
        const startA = -Math.PI / 2;
        const endA = Math.PI / 2;

        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(0, 0, radius, startA, endA, true);
        ctx.stroke();

        ctx.fillStyle = '#444';
        ctx.beginPath();
        ctx.arc(0, 0, radius + 4, startA, endA, true);
        ctx.arc(0, 0, radius, endA, startA, false);
        ctx.closePath();
        ctx.fill();

        ctx.strokeStyle = 'rgba(255,255,255,0.6)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(0, 0, radius - 2, startA, endA, true);
        ctx.stroke();
    } else if (catalogId === 'mirror_planar' || type === 'OPTICAL_MIRROR') {
        // Planar mirror: thin reflective face along local Y axis with a mount backing on local -X.
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(0, -halfH);
        ctx.lineTo(0, halfH);
        ctx.stroke();
        ctx.fillStyle = '#444';
        ctx.fillRect(-halfW / 2, -halfH, halfW / 2, h);
        ctx.strokeStyle = 'rgba(255,255,255,0.5)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(2, -halfH + 5);
        ctx.lineTo(2, halfH - 5);
        ctx.stroke();
    } else if (catalogId === 'beam_block') {
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-halfW, -halfH);
        ctx.lineTo(halfW, halfH);
        ctx.moveTo(halfW, -halfH);
        ctx.lineTo(-halfW, halfH);
        ctx.stroke();
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
    } else if (catalogId === 'beam_splitter' || type === 'OPTICAL_BEAMSPLITTER') {
        ctx.fillStyle = 'rgba(200, 200, 200, 0.1)';
        ctx.strokeStyle = '#888';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = 'rgba(100, 200, 255, 0.8)';
        ctx.beginPath();
        ctx.moveTo(-halfW, -halfH);
        ctx.lineTo(halfW, halfH);
        ctx.stroke();
    } else if (catalogId === 'lens_main' || type === 'OPTICAL_LENS') {
        ctx.fillStyle = 'rgba(100, 200, 255, 0.3)';
        ctx.strokeStyle = 'rgba(150, 220, 255, 0.9)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.ellipse(0, 0, halfW / 3, halfH, 0, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
    } else if (catalogId === 'crystal_main' || type === 'OPTICAL_CRYSTAL') {
        ctx.fillStyle = 'rgba(236, 72, 153, 0.3)';
        ctx.strokeStyle = '#ec4899';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-halfW / 2, -halfH);
        ctx.lineTo(halfW / 2, -halfH);
        ctx.lineTo(halfW, 0);
        ctx.lineTo(halfW / 2, halfH);
        ctx.lineTo(-halfW / 2, halfH);
        ctx.lineTo(-halfW, 0);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
    } else {
        // Default / unknown: silver disk with a "?" so the operator immediately sees that the
        // catalog is missing this id.
        ctx.fillStyle = '#C0C0C0';
        const r = Math.min(halfW, halfH);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#000';
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('?', 0, 4);
    }

    ctx.restore();

    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = (mode === 'GHOST' || mode === 'HOLDING') ? 'rgba(255, 255, 255, 0.5)' : 'rgba(255, 255, 255, 0.9)';
    ctx.font = '500 11px Inter, sans-serif';
    ctx.textAlign = 'center';

    let displayName = name;
    if (store.catalogMap[name]) {
        displayName = store.catalogMap[name].name;
    }

    ctx.fillText(displayName, 0, -halfH - 10);
    const stLab = store.labState && store.labState.components[name] && placementUiLabel(store.labState.components[name]);
    const previewComp = store.control?.previewConfig?.components?.[name];
    const showStorageBadge =
        (previewComp && isStoredComponent(previewComp)) || stLab === 'STORED';
    if (mode === 'SOLID' && showStorageBadge) {
        ctx.fillStyle = 'rgba(165, 180, 252, 0.95)';
        ctx.font = '600 9px Inter, sans-serif';
        ctx.fillText('STORAGE', 0, -halfH - 24);
    }

    if (mode === 'PENDING') {
        const style = pendingOverlayStyle(name);
        ctx.fillStyle = style.color;
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText(style.label, 0, halfH + 15);
    }
    if (mode === 'HOLDING') {
        ctx.fillStyle = HOLDING_STEADY_COLOR;
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText('HOLDING', 0, halfH + 15);
    }
    if (store.isOptimizing && store.pendingCommands.has(name)) {
        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText('OPTIMIZING...', 0, halfH + 15);
    } else if (optRole && optRole !== 'scope') {
        const optColor = optimizationHighlightColor(optRole);
        ctx.fillStyle = optColor;
        ctx.font = 'bold 9px Inter, sans-serif';
        const label =
            optRole === 'both' ? 'OBJ · VAR' : optRole === 'objective' ? 'OBJECTIVE' : 'VARIABLE';
        ctx.fillText(label, 0, halfH + 15);
    }

    ctx.restore();
}

function drawOptimizationGraph() {
    if (!store.isOptimizing || store.optimizationData.length === 0) return;
    // Fake beam-intensity values are mock-only; the real lab has no such metric in state.
    if (!store.labState || store.labState.lab_mode !== 'MOCK') return;

    const ctx = _ctx;
    const w = 300;
    const h = 150;
    const x = CANVAS_WIDTH - w - 20;
    const y = CANVAS_HEIGHT - h - 20;

    ctx.fillStyle = 'rgba(24, 27, 33, 0.9)';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#2a2e36';
    ctx.strokeRect(x, y, w, h);

    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px Inter';
    ctx.fillText('Optimization Metric (Beam Intensity)', x + 10, y + 20);

    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;

    const maxSteps = 20;
    const xScale = (w - 20) / maxSteps;
    const yScale = (h - 40);

    store.optimizationData.forEach((point, i) => {
        const px = x + 10 + point.step * xScale;
        const py = y + h - 10 - point.value * yScale;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
    });
    ctx.stroke();
}

/**
 * Render a VIEWED node's configuration (read-only preview / "replace" mode).
 *
 * The previewed config lists breadboard parts and storage inventory at their
 * nominal poses. Live-only stored tags that are absent from the node are still
 * shown faintly so the operator can see ambient inventory during preview.
 *
 * @param {{ components?: Record<string, any> }} preview
 */
function drawPreviewConfiguration(preview) {
    const comps = (preview && preview.components) || {};
    Object.entries(comps).forEach(([name, c]) => {
        const tun = (c && c.statecontrol && c.statecontrol.tunables) || {};
        let pose = tun.nominal_pose;
        // Stored inventory is slot-only on nodes — derive cell center for draw.
        if (
            (!pose || typeof pose.x !== 'number' || typeof pose.y !== 'number') &&
            isStoredComponent(c)
        ) {
            pose = storageSlotCenterPose(tun.storage && tun.storage.slot);
        }
        if (!pose || typeof pose.x !== 'number' || typeof pose.y !== 'number') return;
        // Slot-derived poses skip shouldRenderOnCanvas (no nominal_pose on the node).
        if (!isStoredComponent(c) && !shouldRenderOnCanvas(name, c)) return;
        if (isStoredComponent(c) && !catalogDeclaresTablePose(name)) return;
        drawComponent(name, pose, c.type, 'SOLID');
    });

    // Ambient live inventory not listed on this node (legacy nodes / extras).
    const live = store.labState && store.labState.components;
    if (live) {
        Object.entries(live).forEach(([name, comp]) => {
            if (comps[name]) return;
            if (isStoredComponent(comp) && shouldRenderOnCanvas(name, comp)) {
                drawComponent(name, drawPose(comp), comp.type, 'SOLID');
            }
        });
    }
}

export function render() {
    if (!_ctx) return;
    const ctx = _ctx;
    clearCanvas();
    drawStorageZone();

    // Read-only node preview: render the VIEWED node's configuration as an
    // overlay instead of the live bench. The live bench (store.labState) is
    // never mutated by preview, so this is a pure projection — and live overlays
    // (ghost edits, TeleOp) are intentionally skipped while viewing. The lines
    // (laser + alignment guides) are versioned too, so draw the node's own lines
    // rather than whatever is currently on the bench.
    const preview = store.control?.previewConfig || null;
    if (preview) {
        drawLaserPath(preview.laser_lines);
        drawAlignmentGuides(preview.alignment_guides || []);
        if (!store.labState) return;
        drawPreviewConfiguration(preview);
        return;
    }

    drawLaserPath();
    drawAlignmentGuides();
    drawAlignmentIntersectionMarkers();

    if (!store.labState) return;

    // 1. Committed components (frozen tunables — skip tags under active TeleOp).
    Object.entries(store.labState.components).forEach(([name, comp]) => {
        if (shouldRenderOnCanvas(name, comp) && !isTeleopReady(comp)) {
            drawComponent(name, drawPose(comp), comp.type, 'SOLID');
        }
    });

    // 1b. TeleOp LIVE pose (hardware truth from fast poll / WS).
    Object.entries(store.labState.components).forEach(([name, comp]) => {
        if (!shouldRenderOnCanvas(name, comp) || !isTeleopReady(comp)) return;
        const live = store.teleopLivePose[name];
        if (!live) return;
        const target = store.teleopTarget[name];
        // Some samples may only carry rotation briefly — keep xy from target/ghost.
        const x = Number.isFinite(live.x) ? live.x
            : (target && Number.isFinite(target.x) ? target.x : NaN);
        const y = Number.isFinite(live.y) ? live.y
            : (target && Number.isFinite(target.y) ? target.y : NaN);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        const drawLive = { ...live, x, y };
        drawComponent(name, drawLive, comp.type, 'SOLID');
        const p = mmToPx(x, y);
        _ctx.fillStyle = TELEOP_LIVE_COLOR;
        _ctx.font = '9px Inter, sans-serif';
        _ctx.fillText('LIVE', p.x + 8, p.y - 8);
    });

    // 1c. TeleOp TARGET preview (client planning layer).
    Object.entries(store.teleopTarget || {}).forEach(([name, pose]) => {
        const comp = store.labState.components[name];
        if (!comp || !isTeleopReady(comp) || !shouldRenderOnCanvas(name, comp)) return;
        drawComponent(name, pose, comp.type, 'GHOST');
        const p = mmToPx(pose.x, pose.y);
        _ctx.fillStyle = TELEOP_TARGET_COLOR;
        _ctx.font = '9px Inter, sans-serif';
        _ctx.fillText('TARGET', p.x + 8, p.y + 14);
        const live = store.teleopLivePose[name];
        if (live && Number.isFinite(live.x)) {
            const from = mmToPx(live.x, live.y);
            const to = mmToPx(pose.x, pose.y);
            _ctx.strokeStyle = TELEOP_TARGET_COLOR;
            _ctx.setLineDash([4, 4]);
            _ctx.beginPath();
            _ctx.moveTo(from.x, from.y);
            _ctx.lineTo(to.x, to.y);
            _ctx.stroke();
            _ctx.setLineDash([]);
        }
    });

    // 2. Ghost components (non-TeleOp intent editing).
    Object.entries(store.ghostState).forEach(([name, pose]) => {
        const comp = store.labState.components[name];
        if (!comp || !shouldRenderOnCanvas(name, comp)) return;
        if (isTeleopReady(comp)) return;
        const type = comp.type || 'UNKNOWN';
        const isPending = store.pendingCommands.has(name);
        // Steady-state HOLDING: system reports HOLDING and this tag is the one in the gripper,
        // with no primitive currently in flight. We draw a distinct "HOLDING" ghost (solid
        // purple halo) so the label oscillates naturally:
        //   PICKING UP... → HOLDING → HOVERING... → HOLDING → PLACING...
        const isHeldSteady = !isPending && isHeldTag(name, store.labState);
        const mode = isPending ? 'PENDING' : (isHeldSteady ? 'HOLDING' : 'GHOST');
        drawComponent(name, pose, type, mode);

        // Drift line: dashed segment from committed intent to ghost — makes it obvious when
        // the operator has un-applied edits in the context-panel inputs.
        const physical = store.labState.components[name];
        if (physical && shouldRenderOnCanvas(name, physical)) {
            const mp = drawPose(physical);
            const from = mmToPx(mp.x, mp.y);
            const to = mmToPx(pose.x, pose.y);
            let driftColor = 'rgba(255, 255, 255, 0.2)';
            if (isPending) driftColor = pendingOverlayStyle(name).color;
            else if (isHeldSteady) driftColor = HOLDING_STEADY_COLOR;
            ctx.strokeStyle = driftColor;
            ctx.setLineDash([5, 5]);
            ctx.beginPath();
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
            ctx.stroke();
            ctx.setLineDash([]);
        }
    });

    drawOptimizationGraph();
}

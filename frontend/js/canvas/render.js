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
import { getViewPrefs, isTwinStyleV3 } from '../ui/view-prefs.js';
import { getRenderTheme } from './render-theme.js';
import { drawComponentBodyV2 } from './render-shapes-v2.js';
import { drawComponentBodyV3 } from './render-shapes-v3.js';

let _ctx = null;

/** Canvas label face — matches tokens `--font-canvas` / Source Sans 3. */
const CANVAS_FONT = '"Source Sans 3", system-ui, sans-serif';

/** Hide-storage: skip inventory zone parts (and STORAGE badges). */
function isHiddenByStoragePref(comp) {
    if (!getViewPrefs().hideStorage) return false;
    if (!comp) return false;
    if (isStoredComponent(comp)) return true;
    try {
        return placementUiLabel(comp) === 'STORED';
    } catch {
        return false;
    }
}

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
    const theme = getRenderTheme();

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
    ctx.fillStyle = theme.usableAreaFill;
    ctx.fill('evenodd');

    ctx.beginPath();
    points.forEach(([x, y], index) => {
        const p = mmToPx(x, y);
        if (index === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
    });
    ctx.closePath();
    ctx.strokeStyle = theme.usableAreaStroke;
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
}

function clearCanvas() {
    const ctx = _ctx;
    const theme = getRenderTheme();
    const gradient = ctx.createLinearGradient(0, 0, 0, CANVAS_HEIGHT);
    gradient.addColorStop(0, theme.benchGradientTop);
    gradient.addColorStop(1, theme.benchGradientBottom);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

    drawApproximateUsableArea();

    // Danger zone: R ≈ 63 mm circle around the robot origin, drawn as a reddish disk + dashed
    // outline so the operator instinctively avoids it during manual drags.
    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, DANGER_RADIUS_MM * LAB_SCALE, 0, Math.PI * 2);
    ctx.fillStyle = theme.dangerFill;
    ctx.fill();
    ctx.strokeStyle = theme.dangerStroke;
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = theme.dangerLabel;
    ctx.font = `11px ${CANVAS_FONT}`;
    ctx.fillText('DANGER ZONE', LAB_CENTER_PX.x - 30, LAB_CENTER_PX.y - 10);

    // X axis (red, right) and Y axis (green, up). Canvas Y is inverted relative to lab Y but
    // `mmToPx` already accounts for that — +labY maps to -canvasY (up on screen).
    ctx.beginPath();
    ctx.strokeStyle = theme.axisX;
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x + 50, LAB_CENTER_PX.y);
    ctx.stroke();
    ctx.fillStyle = theme.axisX;
    ctx.fillText('X', LAB_CENTER_PX.x + 55, LAB_CENTER_PX.y + 4);

    ctx.beginPath();
    ctx.strokeStyle = theme.axisY;
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y - 50);
    ctx.stroke();
    ctx.fillStyle = theme.axisY;
    ctx.fillText('Y', LAB_CENTER_PX.x - 4, LAB_CENTER_PX.y - 55);

    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, 3, 0, Math.PI * 2);
    ctx.fillStyle = theme.originDot;
    ctx.fill();

    // Breadboard hole grid (25 mm spacing). The X offset comes from
    // `BREADBOARD_GRID_OFFSET_X_MM` so the rendered dots line up with the physical column of
    // holes — without this the grid drifts visibly on real hardware.
    ctx.fillStyle = theme.gridDot;
    for (let xMm = LAB_X_MIN; xMm <= LAB_X_MAX; xMm += BREADBOARD_GRID_SPACING_MM) {
        for (let yMm = LAB_Y_MIN; yMm <= LAB_Y_MAX; yMm += BREADBOARD_GRID_SPACING_MM) {
            const p = mmToPx(xMm + BREADBOARD_GRID_OFFSET_X_MM, yMm + BREADBOARD_GRID_OFFSET_Y_MM);
            if (p.x >= 0 && p.x <= CANVAS_WIDTH && p.y >= 0 && p.y <= CANVAS_HEIGHT) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, theme.gridDotRadius, 0, Math.PI * 2);
                ctx.fill();
            }
        }
    }
}

/** Visual inventory region: rectangle toward table center (from layout storage_grid.q3). */
function drawStorageZone() {
    const ctx = _ctx;
    const theme = getRenderTheme();
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
    ctx.fillStyle = theme.storageFill;
    ctx.fill();
    ctx.strokeStyle = theme.storageStroke;
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
    if (!getViewPrefs().hideLabels) {
        ctx.fillStyle = theme.storageLabel;
        ctx.font = `12px ${CANVAS_FONT}`;
        ctx.fillText('Storage', pSw.x + 10, pSw.y - 10);
    }

    const spec = store.storageGridSpec;
    if (!spec || !spec.nx || !spec.ny) return;
    const { nx, ny, cell_width_mm: cw, cell_height_mm: ch, q3 } = spec;
    const x0 = q3.x_min;
    const x1 = q3.x_max;
    const y0 = q3.y_min;
    const y1 = q3.y_max;
    ctx.strokeStyle = theme.storageGridStroke;
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
        ctx.fillStyle = theme.storagePickHint;
        ctx.font = `12px ${CANVAS_FONT}`;
        ctx.fillText('Pick a free cell', pSw.x + 10, pSe.y + 14);
    }
}

function drawLaserPath(docOverride) {
    const ctx = _ctx;
    const theme = getRenderTheme();
    const doc = docOverride || store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines)) return;
    doc.lines.forEach((line) => {
        if (!line || line.enabled === false || !line.p1 || !line.p2) return;
        const col = line.color || theme.laserDefault;
        const seg = clipTwoPointLineToLabBounds(line.p1, line.p2);
        if (!seg) return;
        const p1 = mmToPx(seg[0].x, seg[0].y);
        const p2 = mmToPx(seg[1].x, seg[1].y);
        ctx.shadowBlur = theme.laserGlow;
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
    const theme = getRenderTheme();
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
    ctx.shadowColor = isTranslucent ? 'transparent' : theme.componentShadow;
    ctx.shadowBlur = isTranslucent ? 0 : theme.componentShadowBlur;

    // Multi-panel selection halo:
    //   - any component with an OPEN panel gets a soft secondary outline so
    //     the operator can see at a glance which parts have a dock window.
    //   - the FOCUSED panel's component gets the bright primary outline
    //     (matches the historical single-selection look exactly).
    // Circumscribed circle covers oblong shapes too.
    const haloR = Math.sqrt(halfW * halfW + halfH * halfH) + 5;
    if (store.openPanels.includes(name) && name !== store.focusedPanel) {
        ctx.strokeStyle = theme.haloSecondary;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.arc(0, 0, haloR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
    }
    if (name === store.focusedPanel) {
        ctx.strokeStyle = theme.haloPrimary;
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
        ctx.strokeStyle = theme.dragFromStorage;
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
    const shapeArgs = { catalogId, type, halfW, halfH, w, h };
    if (isTwinStyleV3()) {
        drawComponentBodyV3(ctx, shapeArgs);
    } else {
        drawComponentBodyV2(ctx, shapeArgs);
    }

    ctx.restore();

    ctx.save();
    ctx.translate(x, y);
    const prefs = getViewPrefs();
    const labelR = Math.sqrt(halfW * halfW + halfH * halfH) + 5;
    const NAME_PX = 14;
    const STORAGE_PX = 12;
    const GAP_ABOVE_CIRCLE = 6;
    const GAP_BETWEEN_LINES = 4;
    if (!prefs.hideLabels) {
        ctx.fillStyle = (mode === 'GHOST' || mode === 'HOLDING') ? theme.labelGhost : theme.labelSolid;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';

        let displayName = name;
        if (store.catalogMap[name]) {
            displayName = store.catalogMap[name].name;
        }

        const nameY = -labelR - GAP_ABOVE_CIRCLE;
        ctx.font = `500 ${NAME_PX}px ${CANVAS_FONT}`;
        ctx.fillText(displayName, 0, nameY);
        const stLab = store.labState && store.labState.components[name] && placementUiLabel(store.labState.components[name]);
        const previewComp = store.control?.previewConfig?.components?.[name];
        const showStorageBadge =
            (previewComp && isStoredComponent(previewComp)) || stLab === 'STORED';
        if (mode === 'SOLID' && showStorageBadge && !prefs.hideStorage) {
            ctx.fillStyle = theme.storageBadge;
            ctx.font = `600 ${STORAGE_PX}px ${CANVAS_FONT}`;
            ctx.fillText('STORAGE', 0, nameY - NAME_PX - GAP_BETWEEN_LINES);
        }
    } else {
        ctx.textAlign = 'center';
    }

    if (mode === 'PENDING') {
        const style = pendingOverlayStyle(name);
        ctx.fillStyle = style.color;
        ctx.font = `bold 11px ${CANVAS_FONT}`;
        ctx.fillText(style.label, 0, halfH + 15);
    }
    if (mode === 'HOLDING') {
        ctx.fillStyle = HOLDING_STEADY_COLOR;
        ctx.font = `bold 11px ${CANVAS_FONT}`;
        ctx.fillText('HOLDING', 0, halfH + 15);
    }
    if (store.isOptimizing && store.pendingCommands.has(name)) {
        ctx.fillStyle = '#10b981';
        ctx.font = `bold 11px ${CANVAS_FONT}`;
        ctx.fillText('OPTIMIZING...', 0, halfH + 15);
    } else if (optRole && optRole !== 'scope') {
        const optColor = optimizationHighlightColor(optRole);
        ctx.fillStyle = optColor;
        ctx.font = `bold 11px ${CANVAS_FONT}`;
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
    const theme = getRenderTheme();
    const w = 300;
    const h = 150;
    const x = CANVAS_WIDTH - w - 20;
    const y = CANVAS_HEIGHT - h - 20;

    ctx.fillStyle = theme.optGraphBg;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = theme.optGraphBorder;
    ctx.strokeRect(x, y, w, h);

    ctx.fillStyle = theme.optGraphText;
    ctx.font = `12px ${CANVAS_FONT}`;
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
        if (isHiddenByStoragePref(c)) return;
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
    if (getViewPrefs().hideStorage) return;
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
    const prefs = getViewPrefs();
    const theme = getRenderTheme();
    clearCanvas();
    if (!prefs.hideStorage) {
        drawStorageZone();
    }

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
        if (isHiddenByStoragePref(comp)) return;
        if (shouldRenderOnCanvas(name, comp) && !isTeleopReady(comp)) {
            drawComponent(name, drawPose(comp), comp.type, 'SOLID');
        }
    });

    // 1b. TeleOp LIVE pose (hardware truth from fast poll / WS).
    Object.entries(store.labState.components).forEach(([name, comp]) => {
        if (isHiddenByStoragePref(comp)) return;
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
        if (!prefs.hideLabels) {
            const p = mmToPx(x, y);
            _ctx.fillStyle = theme.teleopLive;
            _ctx.font = `11px ${CANVAS_FONT}`;
            _ctx.fillText('LIVE', p.x + 8, p.y - 8);
        }
    });

    // 1c. TeleOp TARGET preview (client planning layer).
    Object.entries(store.teleopTarget || {}).forEach(([name, pose]) => {
        const comp = store.labState.components[name];
        if (!comp || !isTeleopReady(comp) || !shouldRenderOnCanvas(name, comp)) return;
        if (isHiddenByStoragePref(comp)) return;
        drawComponent(name, pose, comp.type, 'GHOST');
        if (!prefs.hideLabels) {
            const p = mmToPx(pose.x, pose.y);
            _ctx.fillStyle = theme.teleopTarget;
            _ctx.font = `11px ${CANVAS_FONT}`;
            _ctx.fillText('TARGET', p.x + 8, p.y + 14);
        }
        const live = store.teleopLivePose[name];
        if (live && Number.isFinite(live.x)) {
            const from = mmToPx(live.x, live.y);
            const to = mmToPx(pose.x, pose.y);
            _ctx.strokeStyle = theme.teleopTarget;
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
        if (isHiddenByStoragePref(comp)) return;
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
            let driftColor = theme.driftLine;
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

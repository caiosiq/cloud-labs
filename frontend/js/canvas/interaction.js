/**
 * Canvas pointer interaction — collisions, drag, wheel-rotation, drop.
 *
 * **Pose surface #1 (canvas ghost):** drag translates `store.ghostState[tag]`;
 * wheel rotates it. Commits via context-panel Move or MOVE_COMPONENT primitive.
 * During held-object TeleOp, drag updates `store.teleopTarget` instead — see
 * `component-model.js` (pose editing surfaces).
 *
 * Three layers of behavior, in order of selector priority on `mousedown`:
 *   1. Hit-test ghost components → start a drag (or transition into STORED drag-from-storage).
 *   2. Empty space + pencil tool active → begin a guide-line draw (delegated to `guides`).
 *   3. Empty space → clear selection.
 *
 * The wheel handler snaps rotation to the nearest 90° cardinal so users can quickly orient
 * mirrors/lenses without typing. Drop handler creates a new ghost from an inventory drag.
 *
 * `render` is injected at boot via `initCanvasInteraction`.
 */
import {
    DANGER_RADIUS_MM,
    FRAME_SAFETY_CLEARANCE_IN,
    FRAME_SAFETY_CLEARANCE_MM,
    FRAME_SAFETY_MIN_HEIGHT_MM,
    FRAME_SAFETY_MIN_WIDTH_MM,
    LAB_X_MAX,
    LAB_X_MIN,
    LAB_Y_MAX,
    LAB_Y_MIN,
    MANUAL_MOTION_CORNER_CUTOFF_MM,
} from '../config.js';
import { mmToPx, pxToMm } from './coordinates.js';
import { isConfigViewMode } from '../ui/config-view-mode.js';
import { isDetached } from '../control/control-state.js';
import { runtimeEditableOrMessage } from '../control/control-state.js';
import { isOptimizationPlanningActive } from '../ui/optimization-mode.js';
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import {
    drawPose,
    getHolding,
    isBreadboardIntent,
    isHeldTag,
    isStoredComponent,
    shouldRenderOnCanvas,
} from '../component-model.js';
import { isTeleopActive, isTeleopReady } from '../component-state.js';
import { isPlacedRegion, regionMoveBlocked } from '../storage-region.js';
import {
    beginGuideDrag,
    beginGuideEndpointDrag,
    bindGuideDrawListeners,
    clearGuideSelection,
    deleteSelectedGuide,
    finishGuideDrag,
    finishGuideEndpointDrag,
    hitTestGuide,
    hitTestGuideEndpoint,
    requestGuideRedo,
    requestGuideUndo,
    selectGuide,
    updateGuideDrag,
    updateGuideEndpointDrag,
} from './guides.js';
import {
    getDragAlignmentStickyIntersection,
    getDragAlignmentStickySegIdx,
    resetDragAlignmentSticky,
    setDragAlignmentStickyIntersection,
    setDragAlignmentStickySegIdx,
    snapLabPointToAlignmentGuides,
    snapLabPointWithOptionalShiftAxis,
} from './alignment-snap.js';
import {
    clearSelectionAndHideContextPanel,
    openPanel,
    placementUiLabel,
    updateContextPanel,
} from '../ui/context-panel.js';
import { confirmPlaceFromStorageDrag, sendCommand } from '../api/commands.js';
import {
    ensureTeleopTargetPose,
} from '../teleop-pose.js';

/** Degrees per wheel tick while dragging a component. */
const ROTATION_WHEEL_STEP_DEG = 2.5;

function isTagInTeleop(tagId) {
    if (!tagId || !store.labState || !store.labState.components) return false;
    const comp = store.labState.components[tagId];
    return isTeleopReady(comp);
}

/** When TeleOp was started from the panel, canvas selection may be unset. */
function singleTeleopReadyTag() {
    if (!store.labState?.components) return null;
    const tags = Object.entries(store.labState.components)
        .filter(([, comp]) => isTeleopReady(comp))
        .map(([tagId]) => tagId);
    return tags.length === 1 ? tags[0] : null;
}

function resolveWheelTag() {
    if (store.isDragging && store.draggingComponent) return store.draggingComponent;
    if (store.selectedComponent) return store.selectedComponent;
    return singleTeleopReadyTag();
}

function teleopAllowsCanvasXY(tagId) {
    return isTagInTeleop(tagId) && isHeldTag(tagId, store.labState);
}

function ensureTeleopTarget(tagId) {
    return ensureTeleopTargetPose(tagId, store.labState);
}

let _render = () => {};

/** @param {{ render: () => void }} deps */
export function initCanvasInteraction(deps) {
    if (deps && typeof deps.render === 'function') _render = deps.render;

    const canvas = document.getElementById('optical-table');
    if (!canvas) return;

    canvas.addEventListener('mousedown', (e) => onMouseDown(canvas, e));
    canvas.addEventListener('dblclick', (e) => onDoubleClick(canvas, e));
    canvas.addEventListener('mousemove', (e) => onMouseMove(canvas, e));
    canvas.addEventListener('wheel', (e) => onWheel(e), { passive: false });
    canvas.addEventListener('mouseup', (e) => onMouseUp(canvas, e));
    canvas.addEventListener('dragover', (e) => e.preventDefault());
    canvas.addEventListener('drop', (e) => onDrop(canvas, e));

    // Guide editing shortcuts (Delete, Ctrl+Z undo, Ctrl+Y redo) when a guide
    // is selected and focus is not in a text field.
    window.addEventListener('keydown', (e) => {
        const tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) {
            return;
        }

        if (e.ctrlKey || e.metaKey) {
            if ((e.key === 'z' || e.key === 'Z') && !e.shiftKey) {
                if (!store.selectedGuideId) return;
                e.preventDefault();
                requestGuideUndo();
                return;
            }
            if (
                e.key === 'y' ||
                e.key === 'Y' ||
                ((e.key === 'z' || e.key === 'Z') && e.shiftKey)
            ) {
                if (!store.selectedGuideId) return;
                e.preventDefault();
                requestGuideRedo();
                return;
            }
        }

        if (e.key !== 'Delete' && e.key !== 'Backspace') return;
        if (!store.selectedGuideId) return;
        const blocked = runtimeEditableOrMessage();
        if (blocked) {
            log(blocked, 'warn');
            return;
        }
        e.preventDefault();
        void deleteSelectedGuide();
    });
}

// --- Collision + hit-test ---

export function getComponentSize(name) {
    let size = { width: 90, height: 90 }; // Default 90×90 mm.

    // Case 1: Existing component in Lab State — use catalog by tag id.
    if (store.labState && store.labState.components && store.labState.components[name]) {
        const tagId = store.labState.components[name].id;
        if (store.catalogMap[tagId] && store.catalogMap[tagId].size) {
            size = store.catalogMap[tagId].size;
        }
    } else if (store.catalogMap[name] && store.catalogMap[name].size) {
        // Case 2: Direct tag id (e.g. inventory drag-and-drop creation).
        size = store.catalogMap[name].size;
    }
    return size;
}

export function getComponentRadius(name) {
    const size = getComponentSize(name);
    // Circumscribed radius = √(w² + h²) / 2. Conservative for collisions; we trade some false
    // positives for the simplicity of pairwise circle-vs-circle checks below.
    return Math.sqrt(size.width * size.width + size.height * size.height) / 2;
}

export function checkCollision(targetId, x, y, opts = {}) {
    const { forPlaceFromStorageDrag = false } = opts;
    const comp = store.labState && store.labState.components && store.labState.components[targetId];
    const breadboardIntent = comp ? isBreadboardIntent(comp) : true;
    if (forPlaceFromStorageDrag) {
        // Storage drag-and-drop releases *must* land on the breadboard region.
        if (!isPlacedRegion(x, y)) {
            return {
                detected: true,
                other: 'BREADBOARD AREA (release outside the shaded storage region)',
            };
        }
    } else {
        const reg = regionMoveBlocked(breadboardIntent, x, y);
        if (reg.blocked) {
            return { detected: true, other: reg.reason };
        }
    }

    const PADDING_MM = 5; // Min padding between circumscribed circles of two components.
    const r1 = getComponentRadius(targetId);

    if (FRAME_SAFETY_CLEARANCE_MM > 0) {
        const size = getComponentSize(targetId);
        const width = Math.max(Number(size.width) || 0, FRAME_SAFETY_MIN_WIDTH_MM);
        const height = Math.max(Number(size.height) || 0, FRAME_SAFETY_MIN_HEIGHT_MM);
        const rotation = Number.isFinite(Number(opts.rotation))
            ? Number(opts.rotation)
            : Number(store.ghostState?.[targetId]?.rotation || 0);
        const angle = rotation * Math.PI / 180;
        const c = Math.abs(Math.cos(angle));
        const s = Math.abs(Math.sin(angle));
        const halfX = c * width / 2 + s * height / 2;
        const halfY = s * width / 2 + c * height / 2;
        const safeBounds = {
            xMin: LAB_X_MIN + FRAME_SAFETY_CLEARANCE_MM,
            xMax: LAB_X_MAX - FRAME_SAFETY_CLEARANCE_MM,
            yMin: LAB_Y_MIN + FRAME_SAFETY_CLEARANCE_MM,
            yMax: LAB_Y_MAX - FRAME_SAFETY_CLEARANCE_MM,
        };
        let side = null;
        if (x - halfX < safeBounds.xMin) side = 'left';
        else if (x + halfX > safeBounds.xMax) side = 'right';
        else if (y - halfY < safeBounds.yMin) side = 'bottom';
        else if (y + halfY > safeBounds.yMax) side = 'top';
        if (side) {
            const inches = FRAME_SAFETY_CLEARANCE_IN || FRAME_SAFETY_CLEARANCE_MM / 25.4;
            return {
                detected: true,
                other: `FRAME SAFETY BOUNDARY (${side}): keep the entire component at least ${inches.toFixed(0)} in (${FRAME_SAFETY_CLEARANCE_MM.toFixed(1)} mm) inside the frame`,
            };
        }
    }

    if (
        (breadboardIntent || forPlaceFromStorageDrag) &&
        MANUAL_MOTION_CORNER_CUTOFF_MM > 0 &&
        Math.abs(x) > MANUAL_MOTION_CORNER_CUTOFF_MM &&
        Math.abs(y) > MANUAL_MOTION_CORNER_CUTOFF_MM
    ) {
        return {
            detected: true,
            other: `MANUAL MOTION CORNER LIMIT: keep at least one component-center coordinate within +/-${MANUAL_MOTION_CORNER_CUTOFF_MM.toFixed(0)} mm so the camera-guided pickup and placement remain reachable`,
        };
    }

    for (const [id, pose] of Object.entries(store.ghostState)) {
        if (id === targetId) continue;
        const r2 = getComponentRadius(id);
        const minDist = r1 + r2 + PADDING_MM;
        const dx = x - pose.x;
        const dy = y - pose.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < minDist) {
            return { detected: true, other: id };
        }
    }

    // Robot-base danger zone — never let a part end up inside R ≈ 63 mm of the origin.
    const distOrigin = Math.sqrt(x * x + y * y);
    if (distOrigin < DANGER_RADIUS_MM + r1) {
        return { detected: true, other: 'DANGER ZONE (Robot Base)' };
    }

    return { detected: false };
}

function getComponentAtPosition(canvasX, canvasY) {
    for (const [name, pose] of Object.entries(store.ghostState)) {
        const physical = store.labState?.components?.[name];
        if (!physical || !shouldRenderOnCanvas(name, physical)) continue;
        const p = mmToPx(pose.x, pose.y);
        const dx = canvasX - p.x;
        const dy = canvasY - p.y;
        if (Math.sqrt(dx * dx + dy * dy) < 20) return { name, type: 'GHOST' };
    }
    return null;
}

// --- Wheel rotation helper ---

/**
 * One wheel tick: move toward current ± ROTATION_WHEEL_STEP_DEG. If that segment crosses a
 * cardinal angle (any multiple of 90°), land on that cardinal first — e.g. 88.7° + 2.5° would
 * reach 91.2° normally but stops at 90°; the next tick then goes 90° → 92.5°.
 */
function nextWheelRotationDeg(current, directionSign) {
    const cur = typeof current === 'number' && Number.isFinite(current) ? current : 0;
    if (directionSign === 0) return cur;
    const step = ROTATION_WHEEL_STEP_DEG * directionSign;
    const target = cur + step;
    const low = Math.min(cur, target);
    const high = Math.max(cur, target);
    const EPS = 1e-6;
    const cardinals = [];
    const kMin = Math.floor(low / 90) - 5;
    const kMax = Math.ceil(high / 90) + 5;
    for (let k = kMin; k <= kMax; k++) {
        const c = k * 90;
        if (c > low + EPS && c < high - EPS) cardinals.push(c);
    }
    if (cardinals.length === 0) return target;
    if (directionSign > 0) {
        const forward = cardinals.filter((c) => c > cur + EPS);
        return forward.length ? Math.min(...forward) : target;
    }
    const backward = cardinals.filter((c) => c < cur - EPS);
    return backward.length ? Math.max(...backward) : target;
}

// --- Event handlers ---

function onDoubleClick(canvas, e) {
    if (isConfigViewMode() || store.pencilToolActive || store.isDragging) return;

    const rect = canvas.getBoundingClientRect();
    const canvasX = e.clientX - rect.left;
    const canvasY = e.clientY - rect.top;
    const lab = pxToMm(canvasX, canvasY);
    const insideTable =
        lab.x >= LAB_X_MIN && lab.x <= LAB_X_MAX &&
        lab.y >= LAB_Y_MIN && lab.y <= LAB_Y_MAX;
    if (!insideTable) return;
    if (getComponentAtPosition(canvasX, canvasY) || hitTestGuide(canvasX, canvasY)) return;

    store.showUsableAreaOverlay = !store.showUsableAreaOverlay;
    _render();
}

function onMouseDown(canvas, e) {
    if (isConfigViewMode()) return;
    // The lab being non-IDLE (BUSY / OPTIMIZING / HOLDING) used to block this
    // entire handler so the operator couldn't accidentally start a drag
    // mid-command. Multi-panel mode loosens that: *opening* a panel (e.g. to
    // watch a camera feed while another component is in TeleOp) is always
    // allowed; only the drag-to-move path is gated below.
    const labBusy =
        !!store.labState && store.labState.system_status !== 'IDLE';

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = getComponentAtPosition(mouseX, mouseY);
    if (hit) {
        // Drag-from-storage is "sticky" to a single tag. If the user clicks any other component
        // while drag-from-storage mode is active, exit drag mode and open the new component's panel.
        if (store.dragFromStorageTag && hit.name !== store.dragFromStorageTag) {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            openPanel(hit.name);
            log('Drag from storage cancelled (another part was selected).', 'info');
            return;
        }
        // Two-stage interaction (preserved from single-panel UX):
        //   1. First click on a component that is not currently the focused
        //      one: open / focus its panel and stop. No drag.
        //   2. Second click on the same (already-focused) component: drag.
        // Ctrl+click is always pure panel-management — never starts a drag.
        const alreadyFocused =
            store.focusedPanel === hit.name && store.openPanels.includes(hit.name);
        if (!alreadyFocused) {
            openPanel(hit.name);
            log(`Selected ${hit.name}`, 'info');
            return;
        }
        if (isOptimizationPlanningActive()) {
            return;
        }
        {
            if (labBusy) return;
            // Detached HEAD: opening the panel above is fine (read-only
            // inspection), but moving parts is not — fork a branch first.
            if (isDetached()) {
                log('You are on an older commit (detached). Fork a branch here before moving parts.', 'warn');
                return;
            }
            const stComp = store.labState.components[hit.name];
            if (isStoredComponent(stComp)) {
                // STORED parts can only be dragged once the user explicitly opts into drag mode
                // via the panel button — guards against accidental moves out of storage.
                if (store.dragFromStorageTag === hit.name) {
                    store.isDragging = true;
                    resetDragAlignmentSticky();
                    store.draggingComponent = hit.name;
                    const g = store.ghostState[hit.name];
                    store.dragFromStorageStartPose = {
                        x: g.x,
                        y: g.y,
                        rotation: typeof g.rotation === 'number' ? g.rotation : 0,
                    };
                    store.dragComponentStartLab = { x: g.x, y: g.y };
                    const p = mmToPx(g.x, g.y);
                    store.dragOffset = { x: mouseX - p.x, y: mouseY - p.y };
                    return;
                }
                log('Stored parts cannot be dragged; use Drag from storage in the panel, or type a pose.', 'warn');
                return;
            }
            if (
                isTeleopReady(stComp)
                && isBreadboardIntent(stComp)
                && !isHeldTag(hit.name, store.labState)
            ) {
                log(
                    'Table TeleOp is rotation-only — use Rz controls (canvas XY drag disabled).',
                    'warn',
                );
                return;
            }
            store.isDragging = true;
            resetDragAlignmentSticky();
            store.draggingComponent = hit.name;
            if (teleopAllowsCanvasXY(hit.name)) {
                ensureTeleopTarget(hit.name);
            }
            const g0 = teleopAllowsCanvasXY(hit.name)
                ? store.teleopTarget[hit.name]
                : store.ghostState[hit.name];
            store.dragComponentStartLab = {
                x: g0.x,
                y: g0.y,
                rotation: typeof g0.rotation === 'number' ? g0.rotation : 0,
            };
            const p = mmToPx(store.ghostState[hit.name].x, store.ghostState[hit.name].y);
            store.dragOffset = { x: mouseX - p.x, y: mouseY - p.y };
        }
    } else {
        if (store.pencilToolActive) {
            const lab = pxToMm(mouseX, mouseY);
            store.guideDraw = {
                startLab: { x: lab.x, y: lab.y },
                currentLab: { x: lab.x, y: lab.y },
            };
            bindGuideDrawListeners();
            _render();
            return;
        }
        // Click an existing alignment guide → select it (and, when editable,
        // start dragging the whole segment; move = uncommitted change).
        const guideId = hitTestGuide(mouseX, mouseY);
        if (guideId) {
            selectGuide(guideId);
            if (!labBusy && !isDetached()) {
                const blocked = runtimeEditableOrMessage();
                if (!blocked) {
                    const lab = pxToMm(mouseX, mouseY);
                    const endpoint = hitTestGuideEndpoint(mouseX, mouseY);
                    if (endpoint) {
                        beginGuideEndpointDrag(guideId, endpoint, lab);
                    } else {
                        beginGuideDrag(guideId, lab);
                    }
                }
            }
            return;
        }
        clearGuideSelection();
        clearSelectionAndHideContextPanel();
    }
}

function onMouseMove(canvas, e) {
    if (isConfigViewMode()) return;

    if (store.guideEndpointDrag) {
        const rect = canvas.getBoundingClientRect();
        const lab = pxToMm(e.clientX - rect.left, e.clientY - rect.top);
        updateGuideEndpointDrag(lab, e.shiftKey);
        return;
    }

    if (store.guideDrag) {
        const rect = canvas.getBoundingClientRect();
        const lab = pxToMm(e.clientX - rect.left, e.clientY - rect.top);
        updateGuideDrag(lab);
        return;
    }

    if (!store.isDragging || !store.draggingComponent) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const lab = pxToMm(mouseX - store.dragOffset.x, mouseY - store.dragOffset.y);

    const o = store.dragComponentStartLab;
    const useShiftAxis = e.shiftKey && o;
    const dc = store.draggingComponent;
    const teleopDrag = teleopAllowsCanvasXY(dc);
    const prevGh = teleopDrag
        ? (store.teleopTarget[dc] || null)
        : (dc && store.ghostState[dc] ? store.ghostState[dc] : null);
    const snapped = snapLabPointWithOptionalShiftAxis(
        useShiftAxis ? o.x : lab.x,
        useShiftAxis ? o.y : lab.y,
        lab.x,
        lab.y,
        useShiftAxis,
        {
            prevGhost:
                prevGh && Number.isFinite(prevGh.x) && Number.isFinite(prevGh.y)
                    ? { x: prevGh.x, y: prevGh.y }
                    : null,
            stickySegIdx: getDragAlignmentStickySegIdx(),
            stickyIntersection: getDragAlignmentStickyIntersection(),
        },
    );
    if (snapped.snapKind === 'intersection' && snapped.intersection) {
        setDragAlignmentStickyIntersection(snapped.intersection);
        setDragAlignmentStickySegIdx(snapped.segIdx);
    } else if (snapped.snapKind === 'segment') {
        setDragAlignmentStickyIntersection(null);
        setDragAlignmentStickySegIdx(snapped.segIdx);
    } else {
        resetDragAlignmentSticky();
    }
    if (teleopDrag) {
        const tgt = ensureTeleopTarget(dc);
        tgt.x = snapped.x;
        tgt.y = snapped.y;
    } else {
        store.ghostState[store.draggingComponent].x = snapped.x;
        store.ghostState[store.draggingComponent].y = snapped.y;
    }

    _render();
}

function onWheel(e) {
    if (isConfigViewMode()) return;
    if (isOptimizationPlanningActive()) return;
    const tag = resolveWheelTag();
    if (!tag || !store.labState?.components?.[tag]) return;

    const inTeleop = isTagInTeleop(tag);
    if (store.isDragging) {
        // Wheel while dragging: always allowed (TeleOp target or tunables ghost).
    } else if (inTeleop) {
        // Plan rotation on canvas without drag (table Rz or held TeleOp).
    } else {
        return;
    }

    e.preventDefault();

    const direction = Math.sign(e.deltaY);
    if (inTeleop) {
        const tgt = ensureTeleopTarget(tag);
        if (typeof tgt.rotation !== 'number') tgt.rotation = 0;
        tgt.rotation = nextWheelRotationDeg(tgt.rotation, direction);
    } else {
        const ghost = store.ghostState[tag];
        if (!ghost) return;
        if (typeof ghost.rotation !== 'number') ghost.rotation = 0;
        ghost.rotation = nextWheelRotationDeg(ghost.rotation, direction);
    }

    _render();
    // Multi-panel: refresh whichever open panel matches the wheel target,
    // not just the focused one — the dragging component may differ from the
    // focused panel.
    if (store.openPanels.includes(tag)) {
        updateContextPanel(tag);
    }
}

async function onMouseUp(_canvas, _e) {
    if (store.guideEndpointDrag) {
        await finishGuideEndpointDrag();
        return;
    }
    if (store.guideDrag) {
        finishGuideDrag();
        return;
    }
    if (store.isDragging && store.draggingComponent) {
        store.isDragging = false;
        resetDragAlignmentSticky();
        const dc = store.draggingComponent;
        const current = store.ghostState[dc];
        const start = store.dragComponentStartLab;
        const poseChanged =
            !start ||
            Math.hypot(current.x - start.x, current.y - start.y) > 0.5 ||
            Math.abs((current.rotation || 0) - (start.rotation || 0)) > 0.1;
        if (!poseChanged) {
            store.draggingComponent = null;
            store.dragComponentStartLab = null;
            _render();
            return;
        }

        if (isTagInTeleop(dc)) {
            // TeleOp: drag only plans TARGET — operator commits with Go.
            store.draggingComponent = null;
            store.dragComponentStartLab = null;
            _render();
            return;
        }

        const labSt = placementUiLabel(store.labState.components[dc]);
        const isDragFromStoragePlace =
            store.dragFromStorageTag === dc && labSt === 'STORED';

        const collision = isDragFromStoragePlace
            ? checkCollision(dc, current.x, current.y, { forPlaceFromStorageDrag: true })
            : checkCollision(dc, current.x, current.y);

        if (collision.detected) {
            log(`Move cancelled: ${collision.other}`, 'error');

            // Revert ghost back to its pre-drag pose: committed intent for PLACED parts; the
            // drag-from-storage start pose for the STORED → BREADBOARD case.
            if (labSt === 'PLACED') {
                const original = drawPose(store.labState.components[dc]);
                store.ghostState[dc].x = original.x;
                store.ghostState[dc].y = original.y;
                store.ghostState[dc].rotation = original.rotation;
            } else if (
                labSt === 'STORED' &&
                store.dragFromStorageStartPose &&
                store.dragFromStorageTag === dc
            ) {
                const o = store.dragFromStorageStartPose;
                store.ghostState[dc].x = o.x;
                store.ghostState[dc].y = o.y;
                store.ghostState[dc].rotation = o.rotation;
            }
            _render();
            store.draggingComponent = null;
            store.dragComponentStartLab = null;
            return;
        }

        if (isDragFromStoragePlace) {
            const g = store.ghostState[dc];
            await confirmPlaceFromStorageDrag(dc, {
                target_x: g.x,
                target_y: g.y,
                rotation: typeof g.rotation === 'number' ? g.rotation : 0,
            });
        } else {
            await sendCommand({
                action: 'MOVE_COMPONENT',
                target_id: dc,
                parameters: {
                    target_x: store.ghostState[dc].x,
                    target_y: store.ghostState[dc].y,
                    rotation: store.ghostState[dc].rotation,
                },
            });
        }

        store.draggingComponent = null;
        store.dragComponentStartLab = null;
    }
}

function onDrop(canvas, e) {
    if (isConfigViewMode()) return;
    e.preventDefault();
    if (store.labState && store.labState.system_status !== 'IDLE') return;

    const componentName = e.dataTransfer.getData('text/plain');

    // Allow dropping ANY component id, even if not present in lab state yet — the backend will
    // create it as part of the resulting MOVE_COMPONENT.
    if (componentName) {
        const rect = canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const type = e.dataTransfer.getData('application/type') || 'OPTICAL_MIRROR';
        const lab = pxToMm(mouseX, mouseY);
        const snapped = snapLabPointToAlignmentGuides(lab.x, lab.y);
        // Initialize ghost state for the new component immediately so the user sees the drop land.
        store.ghostState[componentName] = {
            x: snapped.x,
            y: snapped.y,
            rotation: 0,
        };

        const collision = checkCollision(componentName, lab.x, lab.y);
        if (collision.detected) {
            log(`Placement cancelled: Collision with ${collision.other}`, 'error');
            delete store.ghostState[componentName];
            _render();
            return;
        }

        // MOVE_COMPONENT with a `type` parameter signals "create + position" to the backend.
        sendCommand({
            action: 'MOVE_COMPONENT',
            target_id: componentName,
            parameters: {
                target_x: store.ghostState[componentName].x,
                target_y: store.ghostState[componentName].y,
                rotation: 0,
                type,
            },
        });

        log(`Placed ${componentName}`, 'info');
        _render();
    }
}

// `handleInventoryDragStart` exists for completeness — currently bound directly to sidebar items
// elsewhere; kept here so it lives with the rest of the canvas drag logic.
export function handleInventoryDragStart(e, componentName) {
    e.dataTransfer.setData('text/plain', componentName);
}

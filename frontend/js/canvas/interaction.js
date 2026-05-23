/**
 * Canvas pointer interaction — collisions, drag, wheel-rotation, drop.
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
import { DANGER_RADIUS_MM } from '../config.js';
import { mmToPx, pxToMm } from './coordinates.js';
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import {
    drawPose,
    isBreadboardIntent,
    isStoredComponent,
    shouldRenderOnCanvas,
} from '../component-model.js';
import { isPlacedRegion, regionMoveBlocked } from '../storage-region.js';
import { bindGuideDrawListeners } from './guides.js';
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
    placementUiLabel,
    updateContextPanel,
} from '../ui/context-panel.js';
import { confirmPlaceFromStorageDrag, sendCommand } from '../api/commands.js';
import { teleopJog } from '../api/teleop.js';

/** Degrees per wheel tick while dragging a component. */
const ROTATION_WHEEL_STEP_DEG = 2.5;

// Phase 8b: TELEOP drag throttle. The backend stale-lease sweeper TTL
// defaults to 3000 ms so any rate above ~1 Hz keeps the lease alive; we
// aim for a smooth-feeling ~15 fps. Frames are absolute (not deltas) so
// drops between throttled sends are self-healing — the next frame just
// carries the latest pose.
const TELEOP_DRAG_FRAME_MIN_INTERVAL_MS = 1000 / 15;

/**
 * Per-tag throttle state for canvas-driven TELEOP jogs.
 *
 *   tagId -> { lastSentMs: number, frameId: number }
 */
const _teleopDragThrottle = new Map();

/**
 * True when the lab state says ``tagId`` currently holds the TELEOP lease.
 * Centralized so the drag handlers can stay readable.
 */
function isTagInTeleop(tagId) {
    if (!tagId || !store.labState || !store.labState.components) return false;
    const comp = store.labState.components[tagId];
    return !!(comp && comp.tunables && comp.tunables.teleop_active);
}

/**
 * Emit one TELEOP_JOG frame for ``tagId`` at the *current* ghost pose,
 * respecting the throttle. Returns the in-flight promise (or ``null`` if
 * the throttle skipped this frame).
 */
function emitTeleopJogFrame(tagId, { force = false } = {}) {
    const ghost = store.ghostState[tagId];
    if (!ghost) return null;
    const now = (typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now();
    const state = _teleopDragThrottle.get(tagId) || { lastSentMs: 0, frameId: 0 };
    if (!force && now - state.lastSentMs < TELEOP_DRAG_FRAME_MIN_INTERVAL_MS) {
        return null;
    }
    state.lastSentMs = now;
    state.frameId += 1;
    _teleopDragThrottle.set(tagId, state);
    return teleopJog(
        tagId,
        {
            nominal_pose: {
                x: Number(ghost.x) || 0,
                y: Number(ghost.y) || 0,
                rotation: Number(ghost.rotation) || 0,
            },
            frame_id: state.frameId,
        },
    );
}

/** Drop the throttle bookkeeping for ``tagId`` (called on mouseup / end). */
function resetTeleopDragThrottle(tagId) {
    if (tagId) _teleopDragThrottle.delete(tagId);
}

let _render = () => {};

/** @param {{ render: () => void }} deps */
export function initCanvasInteraction(deps) {
    if (deps && typeof deps.render === 'function') _render = deps.render;

    const canvas = document.getElementById('optical-table');
    if (!canvas) return;

    canvas.addEventListener('mousedown', (e) => onMouseDown(canvas, e));
    canvas.addEventListener('mousemove', (e) => onMouseMove(canvas, e));
    canvas.addEventListener('wheel', (e) => onWheel(e), { passive: false });
    canvas.addEventListener('mouseup', (e) => onMouseUp(canvas, e));
    canvas.addEventListener('dragover', (e) => e.preventDefault());
    canvas.addEventListener('drop', (e) => onDrop(canvas, e));
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

function onMouseDown(canvas, e) {
    if (store.labState && store.labState.system_status !== 'IDLE') return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = getComponentAtPosition(mouseX, mouseY);

    if (hit) {
        // Drag-from-storage is "sticky" to a single tag. If the user clicks any other component
        // while drag-from-storage mode is active, exit drag mode and select the new component.
        if (store.dragFromStorageTag && hit.name !== store.dragFromStorageTag) {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            store.selectedComponent = hit.name;
            updateContextPanel(hit.name);
            _render();
            log('Drag from storage cancelled (another part was selected).', 'info');
            return;
        }
        if (store.selectedComponent !== hit.name) {
            store.selectedComponent = hit.name;
            updateContextPanel(hit.name);
            _render();
            log(`Selected ${hit.name}`, 'info');
        } else {
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
            store.isDragging = true;
            resetDragAlignmentSticky();
            store.draggingComponent = hit.name;
            const g0 = store.ghostState[hit.name];
            store.dragComponentStartLab = { x: g0.x, y: g0.y };
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
        clearSelectionAndHideContextPanel();
    }
}

function onMouseMove(canvas, e) {
    if (!store.isDragging || !store.draggingComponent) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const lab = pxToMm(mouseX - store.dragOffset.x, mouseY - store.dragOffset.y);

    const o = store.dragComponentStartLab;
    const useShiftAxis = e.shiftKey && o;
    const dc = store.draggingComponent;
    const prevGh = dc && store.ghostState[dc] ? store.ghostState[dc] : null;
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
    store.ghostState[store.draggingComponent].x = snapped.x;
    store.ghostState[store.draggingComponent].y = snapped.y;

    _render();

    // Phase 8b: while teleop owns the lease for this tag, stream the drag
    // as absolute jog frames (throttled to ~15 fps). The MOVE_COMPONENT
    // path in onMouseUp is the non-teleop "commit at release" UX; during
    // teleop the operator gets continuous updates instead.
    if (isTagInTeleop(store.draggingComponent)) {
        emitTeleopJogFrame(store.draggingComponent);
    }
}

function onWheel(e) {
    if (store.isDragging && store.draggingComponent && store.ghostState[store.draggingComponent]) {
        e.preventDefault();

        // deltaY > 0 (scroll down) → +step°; deltaY < 0 (scroll up) → -step°.
        const direction = Math.sign(e.deltaY);

        if (typeof store.ghostState[store.draggingComponent].rotation !== 'number') {
            store.ghostState[store.draggingComponent].rotation = 0;
        }

        const r = store.ghostState[store.draggingComponent].rotation;
        store.ghostState[store.draggingComponent].rotation = nextWheelRotationDeg(r, direction);

        _render();
        updateContextPanel(store.draggingComponent);

        // Phase 8b: wheel ticks during a teleop drag also push absolute
        // jog frames (same throttle as mousemove).
        if (isTagInTeleop(store.draggingComponent)) {
            emitTeleopJogFrame(store.draggingComponent);
        }
    }
}

async function onMouseUp(_canvas, _e) {
    if (store.isDragging && store.draggingComponent) {
        store.isDragging = false;
        resetDragAlignmentSticky();
        const dc = store.draggingComponent;
        const current = store.ghostState[dc];

        // Phase 8b TELEOP path: drag releases send one final force-flush
        // jog (in case the throttle skipped the last few frames) and
        // skip the MOVE_COMPONENT confirm modal entirely. Collision
        // detection is also bypassed here -- the operator "live drove"
        // every pixel of the trajectory and owns avoidance through
        // their eyes; reverting at release would discard their work.
        if (isTagInTeleop(dc)) {
            await emitTeleopJogFrame(dc, { force: true });
            resetTeleopDragThrottle(dc);
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

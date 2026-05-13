// Optical Digital Twin — ES module bundle (see js/bootstrap.js)
import {
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    LAB_X_MIN,
    LAB_X_MAX,
    LAB_Y_MIN,
    LAB_Y_MAX,
    LAB_SCALE,
    LAB_CENTER_PX,
    BREADBOARD_GRID_OFFSET_X_MM,
    POLLING_INTERVAL,
} from './config.js';
import { mmToPx, pxToMm } from './canvas/coordinates.js';
import { store } from './state/store.js';
import {
    collectLayoutWarnings,
    regionMoveBlocked,
    isStorageRegion,
    isPlacedRegion,
} from './storage-region.js';
import {
    measPose,
    nominalPose,
    componentPresence,
    isStoredComponent,
    isOnTableComponent,
    isBreadboardIntent,
    isOffTableComponent,
    hasOptimizationOutcome,
    isOptimizedPlacement,
    getHolding,
    isHoldingState,
    isHeldTag,
    isHoldingUnconfirmed,
} from './component-model.js';

/** Degrees per wheel tick while dragging a component (was 5°). */
const ROTATION_WHEEL_STEP_DEG = 2.5;

/**
 * One wheel tick: move toward current ± ROTATION_WHEEL_STEP_DEG. If that segment crosses a
 * cardinal angle (any multiple of 90°, i.e. … -180, -90, 0, 90, 180, 270, 360 …), land on that
 * cardinal first (e.g. 88.7° +2.5 would reach 91.2, but stops at 90°; next tick goes 90° → 92.5°).
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

console.log('App main module loading...');

/** Legacy-style label for context panel / drag rules (PLACED | STORED | INVENTORY). */
function placementUiLabel(comp) {
    if (!comp) return 'PLACED';
    if (isStoredComponent(comp)) return 'STORED';
    if (isOffTableComponent(comp)) return 'INVENTORY';
    return 'PLACED';
}

// DOM Elements
const canvas = document.getElementById('optical-table');
const ctx = canvas.getContext('2d');
const logOutput = document.getElementById('log-output');
const statusBadge = document.getElementById('system-status-badge');
const componentList = document.getElementById('component-list');
const refreshBtn = document.getElementById('refresh-btn');
const saveStateBtn = document.getElementById('save-state-btn');
const loadStateBtn = document.getElementById('load-state-btn');
const sidebar = document.getElementById('sidebar');
const recipeList = document.getElementById('recipe-list');
// const runExpBtn = document.getElementById('run-exp-btn'); // Removed

// Context Panel Elements (Left Sidebar)
const contextPanel = document.getElementById('context-panel');
const selectedCompName = document.getElementById('selected-comp-name');
const selectedCompTag = document.getElementById('selected-comp-tag');
const selectedCompProperties = document.getElementById('selected-comp-properties');
const ctxX = document.getElementById('ctx-x');
const ctxY = document.getElementById('ctx-y');
const ctxRot = document.getElementById('ctx-rot');
const ctxMoveBtn = document.getElementById('ctx-move-btn');
const ctxStrategies = document.getElementById('ctx-strategies');
const ctxPanelCloseBtn = document.getElementById('ctx-panel-close');
const ctxObserveSlot = document.getElementById('ctx-observe-slot');
const PRIMITIVE_DEV_HINTS =
    typeof URLSearchParams !== 'undefined' &&
    new URLSearchParams(window.location.search).get('debug') === '1';

// Recipe UI Elements (Right Sidebar)
const recordBtn = document.getElementById('record-btn');
const recipePanel = document.getElementById('recipe-panel');
const recipeEditorName = document.getElementById('recipe-editor-name');
const recipeStepsContainer = document.getElementById('recipe-steps-container');
const recipeEditorSave = document.getElementById('recipe-editor-save');
const recipeEditorCancel = document.getElementById('recipe-editor-cancel');
const recIndicator = document.getElementById('rec-indicator');

// Unified Panel Elements (Right Sidebar - now static)
const videoImg = document.getElementById('live-video-img');
const videoPlaceholder = document.getElementById('video-placeholder');
const videoStatus = document.getElementById('video-status');


// --- 1. Networking ---

// We need the catalog to map IDs to Names (store.catalogMap)

async function fetchCatalogMap() {
    try {
        const response = await fetch('/api/catalog');
        if (response.ok) {
            const catalog = await response.json();
            catalog.forEach(item => {
                store.catalogMap[item.tag_id] = item;
            });
            console.log("Catalog Loaded:", store.catalogMap);
            // Re-render UI once catalog is loaded to update names
            updateUI();
        }
    } catch (e) { console.error("Catalog fetch failed", e); }
}

// Call this early
fetchCatalogMap();
fetchLaserLines();

async function fetchStrategies() {
    store.availableStrategies = {
      "NEWTON": {
        "name": "Newton Strategy",
        "description": "Aligns a component by minimizing beam deviation.",
        "parameters": {
          "camera_number": { "type": "integer", "default": 2, "description": "Target Camera ID" },
          "target_x_pixel": { "type": "integer", "default": 2744, "description": "Target X (pixel)" },
          "axis": { "type": "string", "enum": ["x", "y"], "default": "x", "description": "Axis" },
          "tolerance_ratio": { "type": "float", "default": 0.1, "description": "Tolerance" },
          "exposure": { "type": "float", "default": 0.2, "description": "Exposure (s) for strategy camera captures" }
        }
      },
      "COBYLA": {
        "name": "Cobyla Alignment",
        "description": "Constrained Optimization by Linear Approximation.",
        "parameters": {
          "objective_threshold": { "type": "float", "default": 100.0, "description": "Threshold" },
          "exposure": { "type": "float", "default": 0.2, "description": "Exposure (s) for strategy camera video + capture" }
        }
      }
    };
}

async function fetchStorageGridSpec() {
    try {
        const response = await fetch('/api/storage-grid');
        if (response.ok) {
            store.storageGridSpec = await response.json();
        }
    } catch (e) {
        console.warn('storage-grid fetch failed', e);
    }
}

async function fetchLayoutConflicts() {
    try {
        const response = await fetch('/api/layout-conflicts');
        if (!response.ok) {
            store.layoutIssues = [];
            return;
        }
        const data = await response.json();
        store.layoutIssues = data.issues || [];
    } catch (e) {
        console.warn('layout-conflicts fetch failed', e);
        store.layoutIssues = [];
    }
}

// --- Laser line geometry (two-point definition; clip + snap) ---

function twoPointsToLineModel(p1, p2) {
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

function clipTwoPointLineToLabBounds(p1, p2) {
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

function distancePointToLineModel(x, y, m) {
    if (!m) return Infinity;
    if (m.kind === 'vertical') return Math.abs(x - m.x0);
    if (m.kind === 'horizontal') return Math.abs(y - m.y0);
    const val = x - m.a * y - m.b;
    return Math.abs(val) / Math.sqrt(1 + m.a * m.a);
}

function projectPointToLineModel(x, y, m) {
    if (!m) return { x, y };
    if (m.kind === 'vertical') return { x: m.x0, y };
    if (m.kind === 'horizontal') return { x, y: m.y0 };
    const val = x - m.a * y - m.b;
    const denom = 1 + m.a * m.a;
    const k = val / denom;
    return { x: x - k, y: y + m.a * k };
}

function coeffsFromLaserLinesDoc(doc) {
    if (!doc || !Array.isArray(doc.lines)) {
        return { a: 0, b: 0, source: 'none', loaded: false };
    }
    const snapId = doc.snap_line_id;
    const byId = {};
    doc.lines.forEach((ln) => {
        if (ln && ln.id) byId[ln.id] = ln;
    });
    let chosen = null;
    if (snapId && byId[snapId] && byId[snapId].enabled !== false) {
        chosen = byId[snapId];
    }
    if (!chosen) {
        chosen = doc.lines.find((l) => l && l.enabled !== false && l.p1 && l.p2) || null;
    }
    if (!chosen || !chosen.p1 || !chosen.p2) {
        return { a: 0, b: 0, source: 'schema', loaded: false, lab_mode: doc.lab_mode };
    }
    const m = twoPointsToLineModel(chosen.p1, chosen.p2);
    if (!m) {
        return { a: 0, b: 0, source: 'schema', loaded: false, lab_mode: doc.lab_mode };
    }
    if (m.kind === 'ab') {
        return {
            a: m.a,
            b: m.b,
            source: 'schema',
            loaded: true,
            snap_line_id: chosen.id,
            lab_mode: doc.lab_mode,
        };
    }
    if (m.kind === 'vertical') {
        return {
            a: 0,
            b: m.x0,
            source: 'schema',
            loaded: true,
            snap_line_id: chosen.id,
            lab_mode: doc.lab_mode,
        };
    }
    return { a: 0, b: 0, source: 'schema', loaded: false, lab_mode: doc.lab_mode };
}

async function fetchLaserLines() {
    try {
        const response = await fetch('/api/laser-lines');
        if (response.ok) {
            store.laserLinesDoc = await response.json();
            store.laserLineCoeffs = coeffsFromLaserLinesDoc(store.laserLinesDoc);
            renderLaserLinesPanel();
            const n = (store.laserLinesDoc.lines || []).length;
            console.log(
                `Laser lines (${store.laserLinesDoc.lab_mode || '?'}, ${n}):`,
                store.laserLineCoeffs.loaded !== false
                    ? `snap a=${store.laserLineCoeffs.a} b=${store.laserLineCoeffs.b}`
                    : 'no snap line',
            );
        }
    } catch (e) {
        console.error('Laser lines fetch failed', e);
    }
}

async function fetchRecipes() {
    try {
        const response = await fetch('/api/recipes');
        if (response.ok) {
            store.availableRecipes = await response.json();
            renderRecipes();
        }
    } catch (e) {
        console.error("Failed to fetch recipes", e);
    }
}

async function fetchLabState() {
    try {
        console.log(`[${new Date().toLocaleTimeString()}] Requesting Lab State...`);
        const response = await fetch('/api/lab-state');
        if (!response.ok) {
            // Try to parse error detail from backend
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errData = await response.json();
                if (errData.detail) errorMsg = errData.detail;
            } catch (e) { /* ignore JSON parse error */ }
            console.error(`[${new Date().toLocaleTimeString()}] Error receiving Lab State: ${errorMsg}`);
            throw new Error(errorMsg);
        }
        
        store.labState = await response.json();
        console.log(`[${new Date().toLocaleTimeString()}] Received Lab State successfully.`);

        const _dfs = store.dragFromStorageTag;
        if (
            _dfs &&
            store.labState.components &&
            store.labState.components[_dfs] &&
            isBreadboardIntent(store.labState.components[_dfs])
        ) {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
        }

        // Clear error modal if it was open (recovery)
        const existingError = document.getElementById('error-modal');
        if (existingError) existingError.remove();

        // Initial Sync: Use INTENT if available, else Physical Pose
        if (store.labState.components) {
            // Determine if we should sync ghost state from backend
            // 1. Force Sync (Refresh button)
            // 2. System status transitioned from BUSY/OPTIMIZING to IDLE (Command finished)
            // 3. Initial Load (handled by !store.ghostState check)
            
            const justFinishedCommand = (store.previousSystemStatus !== 'IDLE' && store.labState.system_status === 'IDLE');
            const shouldSync = store.forceGhostSync || justFinishedCommand;

            if (shouldSync) {
                 log("Syncing ghost state with lab state...", "info");
            }

            Object.entries(store.labState.components).forEach(([name, comp]) => {
                if (isOnTableComponent(comp)) {
                    const np = nominalPose(comp);
                    const mp = measPose(comp);
                    // Always initialize if missing (first load)
                    if (!store.ghostState[name]) {
                        if (np && Object.keys(np).length) {
                            store.ghostState[name] = { ...np };
                        } else {
                            store.ghostState[name] = { ...mp };
                        }
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = mp.rotation || 0;
                        }
                    }
                    else if (store.labState.system_status === 'OPTIMIZING' && !store.isDragging && np && Object.keys(np).length) {
                        store.ghostState[name] = { ...np };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = mp.rotation || 0;
                        }
                        if (store.selectedComponent === name && document.getElementById('ctx-x')) {
                            ctxX.value = store.ghostState[name].x.toFixed(1);
                            ctxY.value = store.ghostState[name].y.toFixed(1);
                            ctxRot.value = store.ghostState[name].rotation.toFixed(1);
                        }
                    }
                    // HOLDING: only the held tag's ghost follows live nominal_pose (incl. z)
                    // -- all other components stay on the user's last intent.
                    //
                    // IMPORTANT: we deliberately do NOT overwrite the ctx-x/y/rot/z
                    // input fields here. Those inputs represent the operator's
                    // *intent* for the next HOVER / PLACE_FROM_HOVER and must
                    // stay editable. Their initial values are set once by
                    // ``renderInAirControlsForContext`` when the HOLDING panel
                    // is rebuilt (see contextPanelStatusSnapshot logic below).
                    else if (
                        isHoldingState(store.labState) &&
                        isHeldTag(name, store.labState) &&
                        !store.isDragging &&
                        np &&
                        Object.keys(np).length
                    ) {
                        store.ghostState[name] = { ...np };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = mp.rotation || 0;
                        }
                    }
                    else if (shouldSync && !store.isDragging) {
                        if (np && Object.keys(np).length) {
                            store.ghostState[name] = { ...np };
                        } else {
                            store.ghostState[name] = { ...mp };
                        }
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = mp.rotation || 0;
                        }
                        if (store.selectedComponent === name) {
                            if (document.getElementById('ctx-x')) {
                                ctxX.value = store.ghostState[name].x.toFixed(1);
                                ctxY.value = store.ghostState[name].y.toFixed(1);
                                ctxRot.value = store.ghostState[name].rotation.toFixed(1);
                            }
                        }
                    }
                }
            });

            // Reset flags
            if (shouldSync) store.forceGhostSync = false;
        }

        // Rebuild context panel when EITHER the selected component's placement
        // label OR the top-level (system_status, holding) snapshot changes.
        // The latter is what flips IDLE -> HOLDING / HOLDING -> IDLE so the
        // Pick / Hover / Place buttons appear or disappear without the user
        // having to click the sidebar card again. We intentionally ignore
        // transient BUSY states so the panel doesn't briefly revert mid-
        // command -- the pending overlay on canvas signals "in flight".
        const selCtx = store.selectedComponent;
        const hldCtx = getHolding(store.labState);
        const rawStatus = store.labState.system_status || 'IDLE';
        const statusKey = `${rawStatus}|${hldCtx.tag_id || ''}|${hldCtx.requires_operator_confirm ? '1' : '0'}`;
        if (selCtx && store.labState.components && store.labState.components[selCtx]) {
            const compCtx = store.labState.components[selCtx];
            const stCtx = placementUiLabel(compCtx);
            const placementChanged =
                store.contextPanelStateSnapshot != null &&
                store.contextPanelStateSnapshot !== stCtx;
            const statusChanged =
                store.contextPanelStatusSnapshot != null &&
                store.contextPanelStatusSnapshot !== statusKey &&
                rawStatus !== 'BUSY';
            if (placementChanged || statusChanged) {
                if (placementChanged && isOnTableComponent(compCtx) && !store.isDragging) {
                    const np = nominalPose(compCtx);
                    const mp = measPose(compCtx);
                    if (np && Object.keys(np).length) {
                        store.ghostState[selCtx] = { ...np };
                    } else {
                        store.ghostState[selCtx] = { ...mp };
                    }
                    if (typeof store.ghostState[selCtx].rotation !== 'number') {
                        store.ghostState[selCtx].rotation = mp.rotation || 0;
                    }
                }
                updateContextPanel(selCtx);
                updateMotorAngleLabels(selCtx);
            }
        }
        // Only snapshot stable states so a transient BUSY in-between doesn't
        // "use up" the real transition (IDLE -> BUSY -> HOLDING should still
        // rebuild once on the HOLDING edge).
        if (rawStatus !== 'BUSY') {
            store.contextPanelStatusSnapshot = statusKey;
        }

        store.previousSystemStatus = store.labState.system_status;
        
        // --- ADDED: Auto-refresh available components list for sidebar ---
        if (!store.labState.components || Object.keys(store.labState.components).length === 0) {
             // If lab state is empty, we should still show something if it's just initialized
             // but 'components' in store.labState might be empty if the file is empty.
             // We rely on 'updateUI' to handle rendering.
        }
        
        // Clear in-flight overlays once the system has settled into any
        // stable (non-BUSY, non-OPTIMIZING) state. PICK_COMPONENT and HOVER
        // land in HOLDING -- not IDLE -- so gating this on IDLE only would
        // leave the amber/purple "PICKING UP..." / "HOVERING..." label on
        // the ghost forever and prevent render() from swapping in the
        // steady-state "HOLDING" overlay.
        const stableStatus =
            store.labState.system_status === 'IDLE' ||
            store.labState.system_status === 'HOLDING';
        if (stableStatus) {
            store.pendingCommands.clear();
            store.pendingActions.clear();
        }
        if (store.labState.system_status === 'IDLE') {
            if (store.isOptimizing) {
                store.isOptimizing = false; 
                log("Optimization sequence complete.", "info");
                
                // Hide optimization feed overlay
                const optOverlay = document.getElementById('optimization-overlay');
                if (optOverlay) optOverlay.style.display = 'none';
                store.isOptimizingFeedActive = false;

                // Revert table-cam highlight
                const tableCamPreview = document.getElementById('table-cam-preview');
                if (tableCamPreview) {
                    tableCamPreview.style.border = '1px solid var(--border-color)';
                    tableCamPreview.style.backgroundColor = '#0f1115';
                    tableCamPreview.style.boxShadow = '';
                }
                
                // Stop optimization stream to save bandwidth
                const tableCamImg = document.getElementById('table-cam-img');
                const tableCamPlaceholder = document.getElementById('table-cam-placeholder');
                if (tableCamImg) {
                    tableCamImg.src = ""; 
                    tableCamImg.style.display = 'none';
                }
                if (tableCamPlaceholder) {
                    tableCamPlaceholder.style.display = 'flex';
                    tableCamPlaceholder.innerHTML = '<span class="material-icons-round" style="font-size: 24px; margin-bottom: 8px;">camera_alt</span><span style="font-size: 11px;">Table Cam Capture</span>';
                }
            }
        } else if (store.labState.system_status === 'OPTIMIZING') {
            store.isOptimizing = true;
            
            // Show optimization feed overlay
            const optOverlay = document.getElementById('optimization-overlay');
            const optStepText = document.getElementById('optimization-step-text');
            if (optOverlay && optStepText) {
                optOverlay.style.display = 'flex';
                const runBit = store.labState.optimization_run_dir
                    ? ` · ${store.labState.optimization_run_dir}`
                    : '';
                optStepText.innerText = `OPTIMIZING (Step ${store.labState.optimization_step || 0})${runBit}`;
            }

            // Highlight the table cam preview while optimizing
            const tableCamPreview = document.getElementById('table-cam-preview');
            if (tableCamPreview) {
                console.log(`[UI] OPTIMIZING -> highlighting table-cam-preview (step=${store.labState.optimization_step || 0})`);
                tableCamPreview.style.border = '2px solid #22c55e';
                tableCamPreview.style.backgroundColor = '#0b2a19';
                tableCamPreview.style.boxShadow = '0 0 0 3px rgba(34,197,94,0.25)';
            }

            if (!store.isOptimizingFeedActive) {
                store.isOptimizingFeedActive = true;
                const tableCamImg = document.getElementById('table-cam-img');
                const tableCamPlaceholder = document.getElementById('table-cam-placeholder');
                const tableCamError = document.getElementById('table-cam-error');
                
                if (tableCamImg) {
                    // Remove the old captured image immediately when optimization starts
                    tableCamImg.src = "";
                    tableCamImg.style.display = 'none';
                    console.log(`[UI] switching table-cam to optimization-feed stream...`);
                    if (tableCamPlaceholder) {
                        tableCamPlaceholder.style.display = 'flex';
                        const runBit2 = store.labState.optimization_run_dir
                            ? `<br><span style="font-size:9px;opacity:0.85">${store.labState.optimization_run_dir}</span>`
                            : '';
                        tableCamPlaceholder.innerHTML = `<span class="material-icons-round" style="font-size: 18px; margin-bottom: 2px;">auto_awesome</span><div>Optimizing... (Step ${store.labState.optimization_step || 0})${runBit2}</div>`;
                    }

                    tableCamImg.src = `/api/optimization-feed/stream?t=${Date.now()}`;
                    tableCamImg.style.display = 'block';
                    if (tableCamPlaceholder) tableCamPlaceholder.style.display = 'none';
                    if (tableCamError) tableCamError.style.display = 'none';
                }
            }

            // Fake beam-intensity plot is mock-only; real lab has no such metric in state.
            if (store.labState.lab_mode === 'MOCK' && Math.random() > 0.5) {
                store.optimizationData.push({
                    step: store.optimizationData.length, 
                    value: Math.min(1.0, 0.2 + store.optimizationData.length * 0.05 + Math.random() * 0.1)
                });
            }
        }

        fetchLayoutConflicts()
            .then(() => {
                updateLayoutWarningBanner();
                updateLayoutConflictModal();
            })
            .catch(() => {});

        updateUI();
    } catch (error) {
        console.error("Failed to fetch lab state:", error);
        statusBadge.innerHTML = `<span class="status-dot error"></span> OFFLINE`;
        
        showErrorModal("Connection Failed", error.message);

        // Update Inventory List to show error instead of spinner
        componentList.innerHTML = `
            <div style="padding: 20px; text-align: center; color: #ef4444;">
                <span class="material-icons-round" style="font-size: 24px;">error_outline</span>
                <p style="margin-top: 8px; font-size: 12px;">Connection Failed</p>
                <p style="font-size: 10px; opacity: 0.7;">${error.message}</p>
                <button onclick="location.reload()" class="btn btn-secondary" style="margin-top: 12px; font-size: 10px;">Retry</button>
            </div>
        `;
    }
}

function showErrorModal(title, message) {
    if (document.getElementById('error-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'error-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #ef4444';
    card.style.borderRadius = '8px';
    card.style.padding = '32px';
    card.style.width = '450px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 48px; color: #ef4444; margin-bottom: 16px;">report_problem</span>
        <h2 style="margin: 0 0 12px 0; color: #e2e8f0; font-size: 20px;">${title}</h2>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5;">${message}</p>
        <button onclick="location.reload()" class="btn btn-primary" style="background-color: #ef4444; width: auto; margin: 0 auto; padding: 10px 24px;">
            <span class="material-icons-round">refresh</span> Retry Connection
        </button>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

function showConfirmationModal(message, onConfirm, onCancel) {
    if (document.getElementById('confirm-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'confirm-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #3b82f6';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '400px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 40px; color: #3b82f6; margin-bottom: 12px;">help_outline</span>
        <h3 style="margin: 0 0 12px 0; color: #e2e8f0;">Confirm Action</h3>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5;">${message}</p>
        <div style="display: flex; justify-content: center; gap: 12px;">
            <button id="confirm-no" class="btn btn-secondary" style="width: auto; padding: 8px 20px;">Cancel</button>
            <button id="confirm-yes" class="btn btn-primary" style="width: auto; padding: 8px 20px;">Confirm</button>
        </div>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    document.getElementById('confirm-no').onclick = () => {
        overlay.remove();
        if (onCancel) onCancel();
    };

    document.getElementById('confirm-yes').onclick = () => {
        overlay.remove();
        if (onConfirm) onConfirm();
    };
}

async function sendCommand(command) {
    // Intercept MOVE_COMPONENT commands for confirmation
    if (command.action === 'MOVE_COMPONENT' && !store.isRecording) {
        // Create a descriptive message
        let msg = `Move <strong>${command.target_id}</strong>?`;
        if (command.parameters) {
            msg += `<br>X: ${command.parameters.target_x.toFixed(1)} mm<br>Y: ${command.parameters.target_y.toFixed(1)} mm<br>Rot: ${command.parameters.rotation.toFixed(1)}°`;
        }
        
        return new Promise((resolve) => {
            showConfirmationModal(
                msg, 
                async () => {
                    const r = await executeSendCommand(command);
                    resolve(r);
                },
                () => {
                    // Cancelled: Revert ghost state if possible
                    log("Move cancelled by user.", "info");
                    
                    if (command.target_id && store.labState && store.labState.components && store.labState.components[command.target_id] && isBreadboardIntent(store.labState.components[command.target_id])) {
                        const original = measPose(store.labState.components[command.target_id]);
                        // Only revert if we have the ghost state object
                        if (store.ghostState[command.target_id]) {
                            store.ghostState[command.target_id].x = original.x;
                            store.ghostState[command.target_id].y = original.y;
                            store.ghostState[command.target_id].rotation = original.rotation;
                        
                            // Update Context Panel if selected
                            if (store.selectedComponent === command.target_id) {
                                updateContextPanel(command.target_id);
                            }
                            render();
                        }
                    }
                    resolve({ ok: false, error: 'cancelled' });
                }
            );
        });
    }

    // Direct execution for other commands or if recording
    return await executeSendCommand(command);
}

async function executeSendCommand(command) {
    try {
        log(`Sending command: ${command.action}`, "info");
        
        // RECIPE LOGIC: Capture command if recording
        if (store.isRecording) {
            const step = {
                step: store.currentRecipeSteps.length + 1,
                action: command.action,
                component: command.target_id,
                parameters: command.parameters || {}
            };
            store.currentRecipeSteps.push(step);
            updateRecipeEditorList();
            // We still execute it live so the user sees the result!
        }

        if (command.target_id) {
            store.pendingCommands.add(command.target_id);
            if (command.action) store.pendingActions.set(command.target_id, command.action);
        }

        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(command)
        });
        
        if (response.status === 409) {
             const body = await response.json().catch(() => ({}));
             const detail = (body && body.detail) ? String(body.detail) : 'System BUSY or command not allowed in current state.';
             log(`Command rejected (409): ${detail}`, "warn");
             if (command.target_id) {
                 store.pendingCommands.delete(command.target_id);
                 store.pendingActions.delete(command.target_id);
             }
             return { ok: false, error: detail };
        }

        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
            const detail = result.detail || `HTTP ${response.status}`;
            log(`Command rejected: ${detail}`, "error");
            if (command.target_id) {
                store.pendingCommands.delete(command.target_id);
                store.pendingActions.delete(command.target_id);
            }
            return { ok: false, error: detail };
        }

        log(`Server: ${result.message}`, "info");
        
        if (command.action === 'OPTIMIZE') {
            store.isOptimizing = true;
            store.optimizationData = []; 
        }

        return { ok: true, message: result.message || 'Accepted' };

    } catch (error) {
        log(`Command failed: ${error.message}`, "error");
        if (command.target_id) {
            store.pendingCommands.delete(command.target_id);
            store.pendingActions.delete(command.target_id);
        }
        return { ok: false, error: error.message || String(error) };
    }
}

/**
 * After dragging a STORED part onto the breadboard: confirm PLACE_FROM_STORAGE (same flow as move confirm).
 */
async function confirmPlaceFromStorageDrag(targetId, parameters) {
    const tx = parameters.target_x;
    const ty = parameters.target_y;
    const tr = parameters.rotation;
    const msg =
        `Place <strong>${targetId}</strong> from storage onto the breadboard?<br><br>` +
        `This will change the part from <strong>STORED</strong> to <strong>PLACED</strong>.<br><br>` +
        `X: ${Number(tx).toFixed(1)} mm<br>Y: ${Number(ty).toFixed(1)} mm<br>Rot: ${Number(tr).toFixed(1)}°`;

    return new Promise((resolve) => {
        showConfirmationModal(
            msg,
            async () => {
                const r = await executeSendCommand({
                    action: 'PLACE_FROM_STORAGE',
                    target_id: targetId,
                    parameters: {
                        target_x: tx,
                        target_y: ty,
                        rotation: tr,
                    },
                });
                store.dragFromStorageTag = null;
                store.dragFromStorageStartPose = null;
                resolve(r);
            },
            () => {
                log('Place from storage cancelled.', 'info');
                if (store.ghostState[targetId] && store.dragFromStorageStartPose) {
                    const o = store.dragFromStorageStartPose;
                    store.ghostState[targetId].x = o.x;
                    store.ghostState[targetId].y = o.y;
                    store.ghostState[targetId].rotation = o.rotation;
                }
                if (store.selectedComponent === targetId) {
                    updateContextPanel(targetId);
                }
                render();
                resolve({ ok: false, error: 'cancelled' });
            }
        );
    });
}

// --- 2. Interaction Logic ---

function getComponentSize(name) {
    let size = { width: 90, height: 90 }; // Default 90x90mm as requested

    // Case 1: Existing component in Lab State
    if (store.labState && store.labState.components && store.labState.components[name]) {
        const tagId = store.labState.components[name].id;
        if (store.catalogMap[tagId] && store.catalogMap[tagId].size) {
            size = store.catalogMap[tagId].size;
        }
    } 
    // Case 2: Direct Tag ID (e.g. during Drag-and-Drop creation)
    else if (store.catalogMap[name] && store.catalogMap[name].size) {
        size = store.catalogMap[name].size;
    }
    return size;
}

function getComponentRadius(name) {
    const size = getComponentSize(name);
    // Circumscribed radius = sqrt(w^2 + h^2) / 2
    return Math.sqrt(size.width * size.width + size.height * size.height) / 2;
}

function checkCollision(targetId, x, y, opts = {}) {
    const { forPlaceFromStorageDrag = false } = opts;
    const comp = store.labState && store.labState.components && store.labState.components[targetId];
    const breadboardIntent = comp ? isBreadboardIntent(comp) : true;
    if (forPlaceFromStorageDrag) {
        if (!isPlacedRegion(x, y)) {
            return {
                detected: true,
                other: 'BREADBOARD AREA (release outside storage: not both x<0 and y<0)',
            };
        }
    } else {
        const reg = regionMoveBlocked(breadboardIntent, x, y);
        if (reg.blocked) {
            return { detected: true, other: reg.reason };
        }
    }

    const PADDING_MM = 5; // Minimal padding distance between circumscribed circles
    const r1 = getComponentRadius(targetId);

    for (const [id, pose] of Object.entries(store.ghostState)) {
        if (id === targetId) continue; // Don't check against self
        
        const r2 = getComponentRadius(id);
        const minDist = r1 + r2 + PADDING_MM;

        // Calculate distance in mm
        const dx = x - pose.x;
        const dy = y - pose.y;
        const dist = Math.sqrt(dx*dx + dy*dy);
        
        if (dist < minDist) {
            return { detected: true, other: id };
        }
    }

    // Check Danger Zone (R=126/2mm)
    const distOrigin = Math.sqrt(x*x + y*y);
    if (distOrigin < 90 + r1) {
        return { detected: true, other: "DANGER ZONE (Robot Base)" };
    }

    return { detected: false };
}

function getComponentAtPosition(canvasX, canvasY) {
    for (const [name, pose] of Object.entries(store.ghostState)) {
        const p = mmToPx(pose.x, pose.y);
        const dx = canvasX - p.x;
        const dy = canvasY - p.y;
        if (Math.sqrt(dx*dx + dy*dy) < 20) return { name, type: 'GHOST' };
    }
    return null;
}

/** Same as clicking empty canvas: clear selection and hide the floating component panel. */
function clearSelectionAndHideContextPanel() {
    store.selectedComponent = null;
    store.contextPanelStateSnapshot = null;
    store.contextPanelStatusSnapshot = null;
    store.dragFromStorageTag = null;
    store.dragFromStorageStartPose = null;
    contextPanel.style.display = 'none';
    render();
}

canvas.addEventListener('mousedown', (e) => {
    if (store.labState && store.labState.system_status !== 'IDLE') return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = getComponentAtPosition(mouseX, mouseY);
    
    if (hit) {
        if (store.dragFromStorageTag && hit.name !== store.dragFromStorageTag) {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            store.selectedComponent = hit.name;
            updateContextPanel(hit.name);
            render();
            log('Drag from storage cancelled (another part was selected).', 'info');
            return;
        }
        if (store.selectedComponent !== hit.name) {
            store.selectedComponent = hit.name;
            updateContextPanel(hit.name);
            render(); 
            log(`Selected ${hit.name}`, "info");
        } else {
            const stComp = store.labState.components[hit.name];
            if (isStoredComponent(stComp)) {
                if (store.dragFromStorageTag === hit.name) {
                    store.isDragging = true;
                    store.draggingComponent = hit.name;
                    const g = store.ghostState[hit.name];
                    store.dragFromStorageStartPose = {
                        x: g.x,
                        y: g.y,
                        rotation: typeof g.rotation === 'number' ? g.rotation : 0,
                    };
                    const p = mmToPx(g.x, g.y);
                    store.dragOffset = { x: mouseX - p.x, y: mouseY - p.y };
                    return;
                }
                log('Stored parts cannot be dragged; use Drag from storage in the panel, or type a pose.', 'warn');
                return;
            }
            store.isDragging = true;
            store.draggingComponent = hit.name;
            const p = mmToPx(store.ghostState[hit.name].x, store.ghostState[hit.name].y);
            store.dragOffset = { x: mouseX - p.x, y: mouseY - p.y };
        }
    } else {
        clearSelectionAndHideContextPanel();
    }
});

function ensureHoldingBannerEl() {
    let el = document.getElementById('holding-banner');
    if (el) return el;
    const anchor = document.getElementById('layout-warnings');
    el = document.createElement('div');
    el.id = 'holding-banner';
    el.style.cssText =
        'display: none; margin: 0 16px 10px; padding: 10px 12px; font-size: 11px; ' +
        'color: #f3e8ff; background: rgba(168, 85, 247, 0.12); ' +
        'border: 1px solid rgba(168, 85, 247, 0.45); border-radius: 6px; line-height: 1.4;';
    if (anchor && anchor.parentNode) {
        anchor.parentNode.insertBefore(el, anchor);
    } else {
        document.body.insertBefore(el, document.body.firstChild);
    }
    return el;
}

function updateHoldingBanner() {
    const el = ensureHoldingBannerEl();
    if (!store.labState || !isHoldingState(store.labState)) {
        el.style.display = 'none';
        el.innerHTML = '';
        return;
    }
    const hld = getHolding(store.labState);
    el.style.display = 'block';
    if (hld.requires_operator_confirm) {
        el.style.background = 'rgba(239, 68, 68, 0.12)';
        el.style.borderColor = 'rgba(239, 68, 68, 0.5)';
        el.style.color = '#fee2e2';
        el.innerHTML =
            '<div style="display:flex; align-items:flex-start; gap:8px;">' +
            '<span class="material-icons-round" style="font-size:16px;color:#fecaca;">lock</span>' +
            '<div style="flex:1;">' +
            '<div style="font-weight:600; margin-bottom:2px;">HOLDING (unconfirmed)</div>' +
            'The gripper reports closed on startup but the held tag is unknown. ' +
            'Use <code>confirmhold &lt;tag&gt;</code> in the Command Console (or the confirm button in the part panel) to continue. ' +
            'All other commands are blocked until confirmed.' +
            '</div></div>';
    } else {
        el.style.background = 'rgba(168, 85, 247, 0.12)';
        el.style.borderColor = 'rgba(168, 85, 247, 0.45)';
        el.style.color = '#f3e8ff';
        const pose = hld.nominal_pose || {};
        const poseStr = [
            Number.isFinite(Number(pose.x)) ? `x=${Number(pose.x).toFixed(1)}` : null,
            Number.isFinite(Number(pose.y)) ? `y=${Number(pose.y).toFixed(1)}` : null,
            Number.isFinite(Number(pose.rotation)) ? `rot=${Number(pose.rotation).toFixed(1)}°` : null,
            Number.isFinite(Number(pose.z)) ? `z=${Number(pose.z).toFixed(1)}` : null,
        ].filter(Boolean).join(' ');
        el.innerHTML =
            '<div style="display:flex; align-items:flex-start; gap:8px;">' +
            '<span class="material-icons-round" style="font-size:16px;color:#d8b4fe;">pan_tool</span>' +
            '<div style="flex:1;">' +
            `<div style="font-weight:600; margin-bottom:2px;">HOLDING ${hld.tag_id || '<tag>'}</div>` +
            (poseStr
                ? `<div style="color:#c4b5fd; font-family: monospace; font-size: 10px;">${poseStr}</div>`
                : '') +
            '<div style="margin-top:4px;">' +
            'Only <strong>HOVER</strong>, <strong>PLACE_FROM_HOVER</strong>, <strong>SCAN_ROTATE_IN_PLACE</strong>, or ' +
            '<strong>CONFIRM_HOLDING_TAG</strong> are accepted for this tag until released.' +
            '</div>' +
            '</div></div>';
    }
}

function updateContextPanel(name) {
    const comp = store.labState.components[name];
    const pose = store.ghostState[name];
    
    let displayName = name;
    let properties = {};

    if (store.catalogMap[name]) {
        displayName = store.catalogMap[name].name;
        if (store.catalogMap[name].properties) {
            properties = store.catalogMap[name].properties;
        }
    }
    
    selectedCompName.textContent = displayName;
    if (selectedCompTag) selectedCompTag.textContent = name;

    // Render Properties
    selectedCompProperties.innerHTML = '';
    if (Object.keys(properties).length > 0) {
        const propsHtml = Object.entries(properties).map(([key, val]) => {
            // Format Key: radius_of_curvature -> Radius of curvature
            const cleanKey = key.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
            return `<div style="margin-bottom: 2px;">${cleanKey}: <span style="color: #e2e8f0;">${val}</span></div>`;
        }).join('');
        selectedCompProperties.innerHTML = propsHtml;
    }

    contextPanel.style.display = 'block';

    document.querySelectorAll('.ctx-dynamic-storage').forEach((el) => el.remove());

    const placementState = placementUiLabel(comp);
    ctxX.value = pose.x.toFixed(1);
    ctxY.value = pose.y.toFixed(1);
    ctxRot.value = (pose.rotation || 0).toFixed(1);

    if (ctxObserveSlot) {
        ctxObserveSlot.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.style.borderTop = '1px solid #2a2e36';
        wrap.style.paddingTop = '10px';
        wrap.style.marginTop = '4px';
        const h = document.createElement('div');
        h.style.fontSize = '10px';
        h.style.color = '#94a3b8';
        h.style.fontWeight = '600';
        h.style.marginBottom = '6px';
        h.textContent = 'MEASURABLES: SAVED VS OBSERVE';
        wrap.appendChild(h);
        const p = document.createElement('p');
        p.style.fontSize = '9px';
        p.style.color = '#64748b';
        p.style.lineHeight = '1.35';
        p.style.margin = '0 0 8px 0';
        p.innerHTML =
            'Coordinates above are <strong>intent</strong> (ghost). The UI polls <strong>saved</strong> measurables via lab state. <strong>Observe</strong> asks the lab to refresh this tag’s measurables (e.g. camera → <code style="color:#94a3b8;">camera_image</code>).';
        wrap.appendChild(p);
        if (PRIMITIVE_DEV_HINTS) {
            const dev = document.createElement('div');
            dev.style.fontSize = '9px';
            dev.style.color = '#475569';
            dev.style.marginBottom = '6px';
            dev.innerHTML =
                'Dev: <code>OBSERVE_MEASURABLES</code> · <code>POST /api/components/{tag}/measurables/observe</code>';
            wrap.appendChild(dev);
        }
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.flexWrap = 'wrap';
        row.style.gap = '8px';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary';
        btn.style.fontSize = '11px';
        btn.style.padding = '6px 10px';
        btn.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">photo_camera</span> Observe measurables';
        const status = document.createElement('span');
        status.style.fontSize = '10px';
        status.style.color = '#94a3b8';
        btn.onclick = async () => {
            status.textContent = '…';
            btn.disabled = true;
            try {
                const r = await fetch(
                    `/api/components/${encodeURIComponent(name)}/measurables/observe`,
                    { method: 'POST' }
                );
                const data = await r.json().catch(() => ({}));
                if (!r.ok) {
                    const det = data.detail !== undefined ? data.detail : r.status;
                    const msg = typeof det === 'string' ? det : JSON.stringify(det);
                    log(`Observe failed: ${msg}`, 'error');
                    status.textContent = 'Failed';
                    return;
                }
                status.textContent = 'OK';
                const ci = data.measurables && data.measurables.camera_image;
                if (ci && typeof ci === 'object' && ci.path) {
                    const base = String(ci.path).replace(/^.*[/\\\\]/, '');
                    status.textContent = `OK · ${base}`;
                }
                await fetchLabState();
                updateContextPanel(name);
            } catch (e) {
                log(`Observe error: ${e && e.message ? e.message : e}`, 'error');
                status.textContent = 'Error';
            } finally {
                btn.disabled = false;
            }
        };
        row.appendChild(btn);
        row.appendChild(status);
        wrap.appendChild(row);
        const lu = store.labState && store.labState.last_updated;
        if (lu) {
            const luEl = document.createElement('div');
            luEl.style.fontSize = '9px';
            luEl.style.color = '#64748b';
            luEl.style.marginTop = '6px';
            luEl.textContent = `Lab state last_updated: ${lu}`;
            wrap.appendChild(luEl);
        }
        ctxObserveSlot.appendChild(wrap);
    }

    if (placementState === 'STORED') {
        ctxMoveBtn.style.display = 'none';
        const hint = document.createElement('div');
        hint.className = 'ctx-dynamic-storage';
        hint.style.marginTop = '8px';
        hint.style.fontSize = '10px';
        hint.style.color = '#94a3b8';
        hint.style.lineHeight = '1.35';
        hint.innerHTML =
            'Stored in Q3 at <strong>cell center</strong> and <strong>0°</strong> by default. Use <strong>Drag from storage</strong> or set X/Y/Rot and <strong>Place from storage</strong>.';
        selectedCompProperties.appendChild(hint);
    } else {
        ctxMoveBtn.style.display = 'flex';
    }

    // Generate Strategies Buttons (breadboard only)
    ctxStrategies.innerHTML = '';
    if (placementState !== 'STORED' && store.availableStrategies) {
        Object.entries(store.availableStrategies).forEach(([stratKey, strat]) => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-secondary';
            btn.style.width = '100%';
            btn.style.marginBottom = '4px';
            btn.style.fontSize = '10px';
            btn.style.padding = '6px';
            btn.style.textAlign = 'left';
            btn.innerHTML = `<span class="material-icons-round" style="font-size: 12px; vertical-align: middle;">settings_suggest</span> ${strat.name}`;
            btn.onclick = () => showParameterModal(stratKey, strat);
            ctxStrategies.appendChild(btn);
        });
    }

    if (placementState === 'PLACED') {
        const row = document.createElement('div');
        row.className = 'ctx-dynamic-storage';
        row.style.marginTop = '10px';
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-secondary';
        b.style.fontSize = '11px';
        b.style.width = '100%';
        b.innerHTML = '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">inventory_2</span> Move to storage (auto pack)';
        b.onclick = () =>
            sendCommand({ action: 'STORE_COMPONENT', target_id: name, parameters: {} });
        row.appendChild(b);
        ctxMoveBtn.parentNode.insertBefore(row, ctxMoveBtn.nextSibling);
    }

    if (placementState === 'STORED') {
        const rowPlace = document.createElement('div');
        rowPlace.className = 'ctx-dynamic-storage';
        rowPlace.style.marginTop = '10px';
        rowPlace.style.display = 'flex';
        rowPlace.style.flexDirection = 'column';
        rowPlace.style.gap = '6px';

        const bPlace = document.createElement('button');
        bPlace.type = 'button';
        bPlace.className = 'btn btn-primary';
        bPlace.style.fontSize = '11px';
        bPlace.style.width = '100%';
        bPlace.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">north_east</span> Place from storage';
        bPlace.onclick = async () => {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);
            if (!Number.isFinite(tx) || !Number.isFinite(ty) || !Number.isFinite(trot)) {
                log('Invalid coordinates.', 'error');
                return;
            }
            if (isStorageRegion(tx, ty)) {
                log('Target must be outside the storage quadrant (not both x<0 and y<0).', 'error');
                return;
            }
            await sendCommand({
                action: 'PLACE_FROM_STORAGE',
                target_id: name,
                parameters: { target_x: tx, target_y: ty, rotation: trot },
            });
        };
        rowPlace.appendChild(bPlace);

        const bRecenter = document.createElement('button');
        bRecenter.type = 'button';
        bRecenter.className = 'btn btn-secondary';
        bRecenter.style.fontSize = '11px';
        bRecenter.style.width = '100%';
        bRecenter.title = 'Robot moves part to cell center at 0° (standard storage pose).';
        bRecenter.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">center_focus_strong</span> Re-center in cell (0°)';
        bRecenter.onclick = () => {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            void sendCommand({ action: 'RECENTER_IN_STORAGE', target_id: name, parameters: {} });
        };
        rowPlace.appendChild(bRecenter);

        const bDragFs = document.createElement('button');
        bDragFs.type = 'button';
        bDragFs.className = 'btn btn-secondary';
        bDragFs.style.fontSize = '11px';
        bDragFs.style.width = '100%';
        bDragFs.title =
            'Only this part can be dragged until you place or cancel. Release on the breadboard to confirm placement.';
        if (store.dragFromStorageTag === name) {
            bDragFs.disabled = true;
            bDragFs.style.opacity = '0.95';
            bDragFs.innerHTML =
                '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">pan_tool</span> Drag mode — pull on canvas';
            rowPlace.appendChild(bDragFs);
            const bCancelDrag = document.createElement('button');
            bCancelDrag.type = 'button';
            bCancelDrag.className = 'btn btn-secondary';
            bCancelDrag.style.fontSize = '10px';
            bCancelDrag.style.width = '100%';
            bCancelDrag.textContent = 'Cancel drag-from-storage mode';
            bCancelDrag.onclick = () => {
                store.dragFromStorageTag = null;
                store.dragFromStorageStartPose = null;
                log('Drag from storage mode cancelled.', 'info');
                render();
                updateContextPanel(name);
            };
            rowPlace.appendChild(bCancelDrag);
        } else {
            bDragFs.innerHTML =
                '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">touch_app</span> Drag from storage';
            bDragFs.onclick = () => {
                store.dragFromStorageTag = name;
                log('Drag mode: only this part can be dragged. Pull it onto the breadboard, release, then confirm.', 'info');
                render();
                updateContextPanel(name);
            };
            rowPlace.appendChild(bDragFs);
        }

        ctxMoveBtn.parentNode.insertBefore(rowPlace, ctxMoveBtn.nextSibling);
    }

    // --- Motor Controls ---
    const existingMotor = document.getElementById('ctx-motor-controls');
    if (existingMotor) existingMotor.remove();

    if (
        placementState !== 'STORED' &&
        store.catalogMap[name] &&
        store.catalogMap[name].motor_ids &&
        store.catalogMap[name].motor_ids.length > 0
    ) {
        const motorSection = document.createElement('div');
        motorSection.id = 'ctx-motor-controls';
        motorSection.style.marginTop = '12px';
        motorSection.style.paddingTop = '12px';
        motorSection.style.borderTop = '1px solid #2a2e36';
        
        motorSection.innerHTML = '<div style="font-size:11px; color:#94a3b8; margin-bottom:8px; font-weight:600;">MOTOR CONTROL (Relative) — θ = server-tracked cumulative angle</div>';
        
        store.catalogMap[name].motor_ids.forEach(mid => {
            const block = document.createElement('div');
            block.style.marginBottom = '10px';

            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.flexWrap = 'wrap';
            row.style.gap = '8px';
            
            const label = document.createElement('span');
            label.textContent = `M${mid}`;
            label.style.fontSize = '12px';
            label.style.color = '#cbd5e1';
            label.style.minWidth = '28px';
            
            const angleSpan = document.createElement('span');
            angleSpan.id = `ctx-motor-angle-${mid}`;
            angleSpan.style.fontSize = '11px';
            angleSpan.style.color = '#94a3b8';
            angleSpan.style.fontFamily = 'ui-monospace, monospace';
            angleSpan.textContent = 'θ —';

            const input = document.createElement('input');
            input.type = 'number';
            input.value = '100'; 
            input.style.width = '56px';
            input.style.fontSize = '12px';
            input.style.padding = '6px 8px';
            input.style.background = '#0f1115';
            input.style.border = '1px solid #2a2e36';
            input.style.color = '#fff';
            input.style.borderRadius = '4px';
            input.title = "Step Size";
            
            const btnRev = document.createElement('button');
            btnRev.className = 'btn btn-secondary';
            btnRev.style.padding = '6px 10px';
            btnRev.style.fontSize = '12px';
            btnRev.style.width = 'auto';
            btnRev.innerHTML = '<span class="material-icons-round" style="font-size:14px">remove</span>';
            btnRev.title = "Jog Backward";
            btnRev.onclick = () => moveMotor(name, mid, -parseFloat(input.value));

            const btnFwd = document.createElement('button');
            btnFwd.className = 'btn btn-secondary';
            btnFwd.style.padding = '6px 10px';
            btnFwd.style.fontSize = '12px';
            btnFwd.style.width = 'auto';
            btnFwd.innerHTML = '<span class="material-icons-round" style="font-size:14px">add</span>';
            btnFwd.title = "Jog Forward";
            btnFwd.onclick = () => moveMotor(name, mid, parseFloat(input.value));

            row.appendChild(label);
            row.appendChild(angleSpan);
            row.appendChild(btnRev);
            row.appendChild(input);
            row.appendChild(btnFwd);
            block.appendChild(row);

            const row2 = document.createElement('div');
            row2.style.display = 'flex';
            row2.style.gap = '8px';
            row2.style.marginTop = '4px';
            row2.style.paddingLeft = '36px';

            const btnHome = document.createElement('button');
            btnHome.type = 'button';
            btnHome.className = 'btn btn-secondary';
            btnHome.style.padding = '4px 10px';
            btnHome.style.fontSize = '10px';
            btnHome.style.width = 'auto';
            btnHome.textContent = 'Send to home';
            btnHome.title = 'Move motor by −θ so tracked angle becomes 0';
            btnHome.onclick = () => motorSendHome(name, mid);

            const btnZero = document.createElement('button');
            btnZero.type = 'button';
            btnZero.className = 'btn btn-secondary';
            btnZero.style.padding = '4px 10px';
            btnZero.style.fontSize = '10px';
            btnZero.style.width = 'auto';
            btnZero.textContent = 'Set 0';
            btnZero.title = 'Define current position as θ = 0 (no move)';
            btnZero.onclick = () => motorSetZero(name, mid);

            row2.appendChild(btnHome);
            row2.appendChild(btnZero);
            block.appendChild(row2);

            motorSection.appendChild(block);
        });
        
        ctxStrategies.parentNode.appendChild(motorSection);
    }

    renderInAirControlsForContext(name, comp, placementState);

    store.contextPanelStateSnapshot = placementState;
    const hld = getHolding(store.labState);
    store.contextPanelStatusSnapshot = `${(store.labState && store.labState.system_status) || 'IDLE'}|${hld.tag_id || ''}|${hld.requires_operator_confirm ? '1' : '0'}`;
}

/**
 * Render the in-air manipulation section (Pick / Hover / Place-from-hover /
 * Scan-rotate / Confirm) in the context panel. Behavior depends on the
 * current HOLDING state (see new_primitives.md §6):
 *
 *  - IDLE & selected part is on-table:  show Pick button.
 *  - HOLDING_UNCONFIRMED:                show confirm button + lock message.
 *  - HOLDING & selected is held tag:     show Hover form + Place + Scan.
 *  - HOLDING & selected is NOT held:     disable ctx-move-btn with notice.
 */
function renderInAirControlsForContext(name, comp, placementState) {
    document.querySelectorAll('.ctx-in-air').forEach((el) => el.remove());

    const labState = store.labState || {};
    const holding = isHoldingState(labState);
    const unconfirmed = isHoldingUnconfirmed(labState);
    const held = getHolding(labState).tag_id;
    const selectedIsHeld = isHeldTag(name, labState);

    const section = document.createElement('div');
    section.className = 'ctx-in-air';
    section.style.marginTop = '14px';
    section.style.paddingTop = '10px';
    section.style.borderTop = '1px solid #2a2e36';

    const header = document.createElement('div');
    header.style.fontSize = '10px';
    header.style.color = '#94a3b8';
    header.style.fontWeight = '600';
    header.style.marginBottom = '6px';
    header.textContent = 'IN-AIR MANIPULATION';
    section.appendChild(header);

    if (unconfirmed) {
        const p = document.createElement('p');
        p.style.fontSize = '10px';
        p.style.color = '#fca5a5';
        p.style.lineHeight = '1.4';
        p.style.margin = '0 0 8px 0';
        p.innerHTML =
            'Gripper reports closed on startup. Select the tag physically in the gripper and click <strong>Confirm held tag</strong>. All other commands are blocked until confirmed.';
        section.appendChild(p);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-primary';
        btn.style.width = '100%';
        btn.style.fontSize = '11px';
        btn.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">verified</span> Confirm held tag: ' +
            name;
        btn.onclick = () =>
            sendCommand({ action: 'CONFIRM_HOLDING_TAG', target_id: name, parameters: {} });
        section.appendChild(btn);
        ctxStrategies.parentNode.appendChild(section);
        // Block regular move while unconfirmed.
        if (ctxMoveBtn) {
            ctxMoveBtn.disabled = true;
            ctxMoveBtn.title = 'Disabled while HOLDING is unconfirmed.';
        }
        return;
    }

    if (holding && !selectedIsHeld) {
        const p = document.createElement('p');
        p.style.fontSize = '10px';
        p.style.color = '#c4b5fd';
        p.style.lineHeight = '1.4';
        p.style.margin = '0 0 4px 0';
        p.innerHTML =
            `Robot is currently holding <strong>${held || '<tag>'}</strong>. ` +
            'Release it (Place from hover) before interacting with another part.';
        section.appendChild(p);
        ctxStrategies.parentNode.appendChild(section);
        if (ctxMoveBtn) {
            ctxMoveBtn.disabled = true;
            ctxMoveBtn.title = `Disabled: robot is holding ${held || 'another part'}.`;
        }
        return;
    }

    // From here on, the regular move button is re-enabled.
    if (ctxMoveBtn) {
        ctxMoveBtn.disabled = false;
        ctxMoveBtn.title = '';
    }

    if (holding && selectedIsHeld) {
        // Hide the generic Move button — use Hover / Place-from-hover instead.
        if (ctxMoveBtn) ctxMoveBtn.style.display = 'none';

        const hld = getHolding(labState);
        const currentZ =
            (hld.nominal_pose && Number.isFinite(Number(hld.nominal_pose.z)))
                ? Number(hld.nominal_pose.z)
                : 40.0;

        const zRow = document.createElement('div');
        zRow.style.marginBottom = '8px';
        const zLabel = document.createElement('label');
        zLabel.style.fontSize = '10px';
        zLabel.style.color = '#94a3b8';
        zLabel.style.display = 'block';
        zLabel.style.marginBottom = '4px';
        zLabel.textContent = 'Z CLEARANCE (mm above table)';
        zLabel.title =
            'Height of the component\'s base above the breadboard surface. ' +
            '0 = on the table, 40 = default safe hover height.';
        zRow.appendChild(zLabel);
        const zInp = document.createElement('input');
        zInp.type = 'number';
        zInp.id = 'ctx-z';
        zInp.step = '0.5';
        zInp.className = 'coord-input';
        zInp.style.width = '100%';
        zInp.value = currentZ.toFixed(1);
        zRow.appendChild(zInp);
        section.appendChild(zRow);

        const btnRow = document.createElement('div');
        btnRow.style.display = 'flex';
        btnRow.style.flexDirection = 'column';
        btnRow.style.gap = '6px';

        const bHover = document.createElement('button');
        bHover.type = 'button';
        bHover.className = 'btn btn-secondary';
        bHover.style.fontSize = '11px';
        bHover.style.width = '100%';
        bHover.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">open_with</span> Hover to X/Y/Rot/Z';
        bHover.onclick = async () => {
            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);
            const tz = parseFloat(zInp.value);
            if (![tx, ty, trot, tz].every(Number.isFinite)) {
                log('Invalid coordinates for HOVER (need x, y, rotation, z).', 'error');
                return;
            }
            await sendCommand({
                action: 'HOVER',
                target_id: name,
                parameters: { target_x: tx, target_y: ty, rotation: trot, z: tz },
            });
        };
        btnRow.appendChild(bHover);

        const bPlace = document.createElement('button');
        bPlace.type = 'button';
        bPlace.className = 'btn btn-primary';
        bPlace.style.fontSize = '11px';
        bPlace.style.width = '100%';
        bPlace.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">south_east</span> Place from hover';
        bPlace.onclick = async () => {
            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);
            if (![tx, ty, trot].every(Number.isFinite)) {
                log('Invalid coordinates.', 'error');
                return;
            }
            if (isStorageRegion(tx, ty)) {
                log('Place target must be outside the storage quadrant.', 'error');
                return;
            }
            await sendCommand({
                action: 'PLACE_FROM_HOVER',
                target_id: name,
                parameters: { target_x: tx, target_y: ty, rotation: trot },
            });
        };
        btnRow.appendChild(bPlace);

        section.appendChild(btnRow);

        // Scan-rotate sub-section -------------------------------------------
        section.appendChild(buildScanRotateSubPanel(name, { contextHint: 'held' }));

        ctxStrategies.parentNode.appendChild(section);
        return;
    }

    // IDLE path: expose PICK for on-table parts + Scan Rotate (placed-mode).
    if (comp && isOnTableComponent(comp) && placementState !== 'STORED') {
        const p = document.createElement('p');
        p.style.fontSize = '10px';
        p.style.color = '#94a3b8';
        p.style.lineHeight = '1.4';
        p.style.margin = '0 0 6px 0';
        p.innerHTML =
            '<strong>Pick</strong> closes the gripper on this part and lifts to a safe Z (→ HOLDING). ' +
            'Then use <strong>Hover</strong> to re-pose mid-air, <strong>Place from hover</strong> to set down. ' +
            '<strong>Scan rotate</strong> sweeps θ in place — the robot decides whether to rotate it in-air or on the table.';
        section.appendChild(p);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary';
        btn.style.fontSize = '11px';
        btn.style.width = '100%';
        btn.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">pan_tool</span> Pick up (start HOLDING)';
        btn.onclick = () =>
            sendCommand({ action: 'PICK_COMPONENT', target_id: name, parameters: {} });
        section.appendChild(btn);

        // Placed-mode scan-rotate: same user intent (sweep θ at constant rate),
        // but the backend dispatches to ``scan_rotate_placed_cloudlab`` in
        // lab_automation instead of the held-mode function.
        section.appendChild(buildScanRotateSubPanel(name, { contextHint: 'placed' }));

        ctxStrategies.parentNode.appendChild(section);
    }
}

/**
 * Build the Scan-Rotate In Place sub-panel (θ_min, θ_max, deg/s, Start).
 * The UI is identical regardless of whether the component is currently held
 * or placed — the backend decides which ``lab_automation`` function to call
 * based on ``system_status`` at dispatch time (see new_primitives.md §7 and
 * labautomation_new_primitives.md §2.4).
 *
 * ``contextHint`` ("held" | "placed") only tweaks the helper text.
 */
function buildScanRotateSubPanel(tagId, { contextHint = 'held' } = {}) {
    const scan = document.createElement('div');
    scan.style.marginTop = '12px';
    scan.style.paddingTop = '10px';
    scan.style.borderTop = '1px dashed #2a2e36';
    const scanHdr = document.createElement('div');
    scanHdr.style.fontSize = '10px';
    scanHdr.style.color = '#94a3b8';
    scanHdr.style.fontWeight = '600';
    scanHdr.style.marginBottom = '6px';
    scanHdr.textContent = 'SCAN ROTATE IN PLACE';
    scan.appendChild(scanHdr);

    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.lineHeight = '1.4';
    hint.style.margin = '0 0 6px 0';
    hint.innerHTML = contextHint === 'placed'
        ? 'Briefly grips the part with the robot arm, sweeps \u03b8, then releases it back at the same XY at the new rotation. Works regardless of whether the component has a motor.'
        : 'Rotates the held part in-air while XY + Z stay locked at the hover pose.';
    scan.appendChild(hint);

    const scanGrid = document.createElement('div');
    scanGrid.style.display = 'grid';
    scanGrid.style.gridTemplateColumns = '1fr 1fr 1fr';
    scanGrid.style.gap = '6px';
    const scanTMin = document.createElement('input');
    scanTMin.type = 'number';
    scanTMin.className = 'coord-input';
    scanTMin.placeholder = 'θ min°';
    scanTMin.value = '-45';
    const scanTMax = document.createElement('input');
    scanTMax.type = 'number';
    scanTMax.className = 'coord-input';
    scanTMax.placeholder = 'θ max°';
    scanTMax.value = '45';
    const scanSpd = document.createElement('input');
    scanSpd.type = 'number';
    scanSpd.className = 'coord-input';
    scanSpd.placeholder = 'deg/s';
    scanSpd.value = '30';
    scanGrid.appendChild(scanTMin);
    scanGrid.appendChild(scanTMax);
    scanGrid.appendChild(scanSpd);
    scan.appendChild(scanGrid);

    const bScan = document.createElement('button');
    bScan.type = 'button';
    bScan.className = 'btn btn-secondary';
    bScan.style.marginTop = '6px';
    bScan.style.fontSize = '11px';
    bScan.style.width = '100%';
    bScan.innerHTML =
        '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">rotate_right</span> Start scan rotate';
    bScan.onclick = async () => {
        const tmin = parseFloat(scanTMin.value);
        const tmax = parseFloat(scanTMax.value);
        const spd = parseFloat(scanSpd.value);
        if (![tmin, tmax, spd].every(Number.isFinite) || !(spd > 0)) {
            log('Invalid scan params (need numbers; speed > 0).', 'error');
            return;
        }
        await sendCommand({
            action: 'SCAN_ROTATE_IN_PLACE',
            target_id: tagId,
            parameters: {
                theta_min: tmin,
                theta_max: tmax,
                speed_deg_per_s: spd,
                axis: 'z',
            },
        });
    };
    scan.appendChild(bScan);
    return scan;
}

async function moveMotor(targetId, motorId, dist) {
    await sendCommand({
        action: "MOVE_MOTOR",
        target_id: targetId,
        parameters: {
            motor_id: motorId,
            distance: dist
        }
    });
}

async function motorSendHome(targetId, motorId) {
    await sendCommand({
        action: "MOTOR_SEND_HOME",
        target_id: targetId,
        parameters: { motor_id: motorId }
    });
}

async function motorSetZero(targetId, motorId) {
    await sendCommand({
        action: "MOTOR_SET_ZERO",
        target_id: targetId,
        parameters: { motor_id: motorId }
    });
}

/** Refresh tracked motor angle labels from lab-state (pose.motor_rotations). */
function updateMotorAngleLabels(tagId) {
    if (!tagId || !store.labState || !store.labState.components) return;
    const comp = store.labState.components[tagId];
    if (!comp) return;
    const mr = (measPose(comp).motor_rotations) || {};
    const mids = store.catalogMap[tagId] && store.catalogMap[tagId].motor_ids;
    if (!mids || !mids.length) return;
    mids.forEach((mid) => {
        const el = document.getElementById(`ctx-motor-angle-${mid}`);
        if (!el) return;
        const v = mr[String(mid)];
        const n = (v !== undefined && v !== null && Number.isFinite(Number(v))) ? Number(v) : 0;
        el.textContent = `θ ${n.toFixed(2)}`;
    });
}

ctxMoveBtn.addEventListener('click', async () => {
    if (!store.selectedComponent) return;
    
    const tx = parseFloat(ctxX.value);
    const ty = parseFloat(ctxY.value);
    const trot = parseFloat(ctxRot.value);

    // Collision Check
    const collision = checkCollision(store.selectedComponent, tx, ty);
    if (collision.detected) {
        log(`Move cancelled: Collision with ${collision.other}`, "error");
        // Revert UI values to current ghost state (which hasn't updated yet)
        if (store.ghostState[store.selectedComponent]) {
            const old = store.ghostState[store.selectedComponent];
            ctxX.value = old.x.toFixed(1);
            ctxY.value = old.y.toFixed(1);
        }
        return;
    }

    // Update Ghost State immediately for visual feedback
    store.ghostState[store.selectedComponent].x = tx;
    store.ghostState[store.selectedComponent].y = ty;
    store.ghostState[store.selectedComponent].rotation = trot;
    
    await sendCommand({
        action: "MOVE_COMPONENT",
        target_id: store.selectedComponent,
        parameters: {
            target_x: tx,
            target_y: ty,
            rotation: trot
        }
    });
    render();
});

// Remove popup related event listeners
// popupClose.addEventListener... (deleted)
// popupMoveBtn.addEventListener... (deleted)
// update MouseDown logic above replaced the old one


canvas.addEventListener('mousemove', (e) => {
    if (!store.isDragging || !store.draggingComponent) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const lab = pxToMm(mouseX - store.dragOffset.x, mouseY - store.dragOffset.y);

    let finalX = lab.x;
    let finalY = lab.y;

    // Snapping: nearest enabled laser line (schemas/laser_lines.*.json)
    const doc = store.laserLinesDoc;
    if (doc && Array.isArray(doc.lines)) {
        const SNAP_THRESHOLD_MM = 10;
        let bestDist = SNAP_THRESHOLD_MM;
        let bestPt = { x: finalX, y: finalY };
        doc.lines.forEach((line) => {
            if (!line || line.enabled === false || !line.p1 || !line.p2) return;
            const m = twoPointsToLineModel(line.p1, line.p2);
            if (!m) return;
            const d = distancePointToLineModel(finalX, finalY, m);
            if (d < bestDist) {
                bestDist = d;
                bestPt = projectPointToLineModel(finalX, finalY, m);
            }
        });
        if (bestDist < SNAP_THRESHOLD_MM) {
            finalX = bestPt.x;
            finalY = bestPt.y;
        }
    }

    store.ghostState[store.draggingComponent].x = finalX;
    store.ghostState[store.draggingComponent].y = finalY;
    
    render();
});

// --- ADDED: Mouse Wheel Rotation ---
canvas.addEventListener('wheel', (e) => {
    if (store.isDragging && store.draggingComponent && store.ghostState[store.draggingComponent]) {
        e.preventDefault();
        
        // Scroll direction: positive deltaY (down) -> +step deg, negative (up) -> -step deg
        const direction = Math.sign(e.deltaY);
        
        if (typeof store.ghostState[store.draggingComponent].rotation !== 'number') {
            store.ghostState[store.draggingComponent].rotation = 0;
        }
        
        const r = store.ghostState[store.draggingComponent].rotation;
        store.ghostState[store.draggingComponent].rotation = nextWheelRotationDeg(r, direction);
        
        // Update UI immediately
        render();
        updateContextPanel(store.draggingComponent);
    }
}, { passive: false });

canvas.addEventListener('mouseup', async (e) => {
    if (store.isDragging && store.draggingComponent) {
        store.isDragging = false;
        const dc = store.draggingComponent;
        const current = store.ghostState[dc];
        const labSt = placementUiLabel(store.labState.components[dc]);
        const isDragFromStoragePlace =
            store.dragFromStorageTag === dc && labSt === 'STORED';

        const collision = isDragFromStoragePlace
            ? checkCollision(dc, current.x, current.y, { forPlaceFromStorageDrag: true })
            : checkCollision(dc, current.x, current.y);

        if (collision.detected) {
            log(`Move cancelled: ${collision.other}`, 'error');

            if (labSt === 'PLACED') {
                const original = measPose(store.labState.components[dc]);
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
            render();
            store.draggingComponent = null;
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
    }
});

function handleInventoryDragStart(e, componentName) {
    e.dataTransfer.setData("text/plain", componentName);
}

canvas.addEventListener('dragover', (e) => e.preventDefault());

canvas.addEventListener('drop', (e) => {
        e.preventDefault();
        if (store.labState && store.labState.system_status !== 'IDLE') return;
    
        const componentName = e.dataTransfer.getData("text/plain");
        
        // --- MODIFIED: Allow dropping ANY component ID, even if not in store.labState yet ---
        if (componentName) {
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;
            
            const type = e.dataTransfer.getData("application/type") || "OPTICAL_MIRROR";
            const lab = pxToMm(mouseX, mouseY);
            // Initialize ghost state for new component immediately
            store.ghostState[componentName] = {
                x: lab.x,
                y: lab.y,
                rotation: 0
            };

            // Collision Check
            const collision = checkCollision(componentName, lab.x, lab.y);
            if (collision.detected) {
                log(`Placement cancelled: Collision with ${collision.other}`, "error");
                delete store.ghostState[componentName];
                render();
                return;
            }
    
            // Trigger move command which will create it in backend
            sendCommand({
                action: "MOVE_COMPONENT",
                target_id: componentName,
                parameters: {
                    target_x: store.ghostState[componentName].x,
                    target_y: store.ghostState[componentName].y,
                    rotation: 0,
                    type: type // Pass type to backend
                }
            });
    
            log(`Placed ${componentName}`, "info");
            render();
        }
    });


// Context Popup Elements
const contextPopup = document.getElementById('context-popup');
const popupTitle = document.getElementById('popup-title');
const popupClose = document.getElementById('popup-close');
const popupX = document.getElementById('popup-x');
const popupY = document.getElementById('popup-y');
const popupRot = document.getElementById('popup-rot');
const popupMoveBtn = document.getElementById('popup-move-btn');
const popupStrategies = document.getElementById('popup-strategies');

// ...

// --- 3. Sidebar Selection Panel ---

// Removed showContextPopup and hideContextPopup functions as they are replaced by updateContextPanel

// ... (Parameter Modal Logic remains)

// --- Parameter Modal Logic ---

function showParameterModal(strategyKey, strategyDef) {
    const existing = document.getElementById('param-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'param-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.7)';
    overlay.style.zIndex = '2000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #2a2e36';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '400px';
    card.style.boxShadow = '0 10px 25px rgba(0,0,0,0.5)';

    const title = document.createElement('h3');
    title.textContent = `Configure ${strategyDef.name}`;
    title.style.margin = '0 0 8px 0';
    title.style.color = '#e2e8f0';
    card.appendChild(title);

    const desc = document.createElement('p');
    desc.textContent = strategyDef.description;
    desc.style.margin = '0 0 20px 0';
    desc.style.color = '#94a3b8';
    desc.style.fontSize = '13px';
    card.appendChild(desc);

    const form = document.createElement('form');
    const inputs = {};

    Object.entries(strategyDef.parameters).forEach(([paramKey, paramDef]) => {
        const field = document.createElement('div');
        field.style.marginBottom = '16px';

        const label = document.createElement('label');
        label.textContent = `${paramKey} (${paramDef.description})`;
        label.style.display = 'block';
        label.style.marginBottom = '6px';
        label.style.color = '#cbd5e1';
        label.style.fontSize = '12px';
        field.appendChild(label);

        let input;
        if (paramDef.enum) {
            input = document.createElement('select');
            paramDef.enum.forEach(opt => {
                const option = document.createElement('option');
                option.value = opt;
                option.textContent = opt;
                if (opt === paramDef.default) option.selected = true;
                input.appendChild(option);
            });
        } else {
            input = document.createElement('input');
            input.type = paramDef.type === 'integer' || paramDef.type === 'float' ? 'number' : 'text';
            input.value = paramDef.default !== null ? paramDef.default : '';
            if (paramDef.type === 'float') input.step = '0.01';
        }
        
        input.style.width = '100%';
        input.style.padding = '8px';
        input.style.backgroundColor = '#0f1115';
        input.style.border = '1px solid #2a2e36';
        input.style.borderRadius = '4px';
        input.style.color = 'white';
        
        inputs[paramKey] = input;
        field.appendChild(input);
        form.appendChild(field);
    });



    const btnRow = document.createElement('div');
    btnRow.style.display = 'flex';
    btnRow.style.justifyContent = 'flex-end';
    btnRow.style.gap = '12px';
    btnRow.style.marginTop = '24px';

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-secondary'; 
    cancelBtn.style.width = 'auto';
    cancelBtn.onclick = () => overlay.remove();

    const runBtn = document.createElement('button');
    runBtn.textContent = 'Run Strategy';
    runBtn.type = 'submit';
    runBtn.className = 'btn btn-primary'; 
    runBtn.style.width = 'auto';

    form.onsubmit = (e) => {
        e.preventDefault();
        const params = {};
        Object.entries(inputs).forEach(([key, el]) => {
            const def = strategyDef.parameters[key];
            let val = el.value;
            if (def.type === 'integer') val = parseInt(val);
            if (def.type === 'float') val = parseFloat(val);
            params[key] = val;
        });
        
        params.strategy = strategyKey;

        // Auto-inject motor_ids for COBYLA if available
        if (strategyKey === 'COBYLA' && store.selectedComponent) {
             if (store.catalogMap[store.selectedComponent] && store.catalogMap[store.selectedComponent].motor_ids) {
                 params.motor_ids = store.catalogMap[store.selectedComponent].motor_ids;
                 log(`Using motor_ids: [${params.motor_ids.join(', ')}]`, "info");
             }
        }

        sendCommand({
            action: "OPTIMIZE",
            target_id: store.selectedComponent, 
            parameters: params
        });
        overlay.remove();
    };

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(runBtn);
    form.appendChild(btnRow);
    card.appendChild(form);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

// --- 4. Rendering ---

function clearCanvas() {
    const gradient = ctx.createLinearGradient(0, 0, 0, CANVAS_HEIGHT);
    gradient.addColorStop(0, '#1a1d21');
    gradient.addColorStop(1, '#141619');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
    
    // Draw Danger Zone (R=10cm around origin)
    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, 90 * LAB_SCALE, 0, Math.PI * 2); // 90mm
    ctx.fillStyle = 'rgba(239, 68, 68, 0.1)'; // Reddish transparent
    ctx.fill();
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
    
    // Draw Danger Zone Label
    ctx.fillStyle = 'rgba(239, 68, 68, 0.5)';
    ctx.font = '10px Inter';
    ctx.fillText("DANGER ZONE", LAB_CENTER_PX.x - 30, LAB_CENTER_PX.y - 10);

    // Draw Axes
    // X Axis (Red)
    ctx.beginPath();
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x + 50, LAB_CENTER_PX.y); // 50px length
    ctx.stroke();
    ctx.fillStyle = '#ef4444';
    ctx.fillText("X", LAB_CENTER_PX.x + 55, LAB_CENTER_PX.y + 4);

    // Y Axis (Green) - Note: Canvas Y is inverted relative to Lab Y usually, but here we mapped +Y up in mmToPx
    // mmToPx: y: LAB_CENTER_PX.y - labY * LAB_SCALE. So +LabY is -CanvasY (Up).
    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y - 50); // Up
    ctx.stroke();
    ctx.fillStyle = '#10b981';
    ctx.fillText("Y", LAB_CENTER_PX.x - 4, LAB_CENTER_PX.y - 55);

    // Origin Dot
    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();

    // Draw Breadboard Grid (25mm spacing), shifted in X to match physical hole columns (see BREADBOARD_GRID_OFFSET_X_MM).
    ctx.fillStyle = '#2a2e36';
    const gridSpacingMm = 25;
    
    // Calculate start/end based on lab coordinates
    // We iterate in mm and convert to px to ensure accuracy
    for (let xMm = LAB_X_MIN; xMm <= LAB_X_MAX; xMm += gridSpacingMm) {
        for (let yMm = LAB_Y_MIN; yMm <= LAB_Y_MAX; yMm += gridSpacingMm) {
            const p = mmToPx(xMm + BREADBOARD_GRID_OFFSET_X_MM, yMm);
            // Only draw if within canvas bounds (though mmToPx should handle mapping)
            if (p.x >= 0 && p.x <= CANVAS_WIDTH && p.y >= 0 && p.y <= CANVAS_HEIGHT) {
                ctx.beginPath(); 
                ctx.arc(p.x, p.y, 2, 0, Math.PI * 2); 
                ctx.fill();
            }
        }
    }
}

/** Visual for third quadrant (x<0, y<0): storage / inventory area. */
function drawStorageZone() {
    const pSw = mmToPx(LAB_X_MIN, LAB_Y_MIN);
    const pSe = mmToPx(0, LAB_Y_MIN);
    const pNe = mmToPx(0, 0);
    const pNw = mmToPx(LAB_X_MIN, 0);
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
    ctx.fillText('Storage (Q3)', pSw.x + 10, pSw.y - 10);

    const spec = store.storageGridSpec;
    if (!spec || !spec.nx || !spec.ny) return;
    const { nx, ny, cell_width_mm: cw, cell_height_mm: ch, q3 } = spec;
    const x0 = q3.x_min;
    const y0 = q3.y_min;
    ctx.strokeStyle = 'rgba(96, 165, 250, 0.55)';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    for (let i = 0; i <= nx; i++) {
        const xm = x0 + i * cw;
        const a = mmToPx(xm, y0);
        const b = mmToPx(xm, 0);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
    }
    for (let j = 0; j <= ny; j++) {
        const ym = y0 + j * ch;
        const a = mmToPx(x0, ym);
        const b = mmToPx(0, ym);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
    }
}

/** Clip segment of line x = a*y + b to lab bounds; returns [p1, p2] in mm or null. */
function clipLaserLineToBounds(a, b) {
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

function drawLaserPath() {
    const doc = store.laserLinesDoc;
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
 * Base purple used by the HOLDING system status badge and reused here for the
 * steady-state "HOLDING" ghost overlay (solid halo + label) so the canvas
 * stays visually consistent with the top-right status pill and the holding
 * banner.
 */
const HOLDING_STEADY_COLOR = '#a855f7';

/**
 * Style + label for the amber / purple "in flight" overlay drawn on the
 * ghost while ``store.pendingCommands`` is non-empty for ``name``.
 *
 * In-air actions (PICK/HOVER/PLACE_FROM_HOVER/SCAN_ROTATE_IN_PLACE) render
 * in purple with a descriptive label — e.g. HOVERING... — instead of the
 * generic amber "MOVING..." used for everything else, to match the
 * HOLDING status badge and make it obvious that the robot is manipulating
 * the part in mid-air rather than doing a plain table move.
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
        case 'SCAN_ROTATE_IN_PLACE':
            return { color: '#a855f7', label: 'SCAN ROTATING...' };
        default:
            return { color: '#f59e0b', label: 'MOVING...' };
    }
}

function drawComponent(name, pose, type, mode = 'SOLID') {
    const p = mmToPx(pose.x, pose.y);
    const x = p.x;
    const y = p.y;
    const rotation = pose.rotation * (Math.PI / 180); 

    // Determine Size in Pixels (since LAB_SCALE is px/mm, width_px = width_mm * LAB_SCALE)
    const size = getComponentSize(name);
    const w = size.width * LAB_SCALE;
    const h = size.height * LAB_SCALE;
    const halfW = w / 2;
    const halfH = h / 2;

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rotation);

    if (mode === 'GHOST') ctx.globalAlpha = 0.5;
    if (mode === 'PENDING') ctx.globalAlpha = 0.7;
    if (mode === 'HOLDING') ctx.globalAlpha = 0.75;

    const isTranslucent = mode === 'GHOST' || mode === 'HOLDING';
    ctx.shadowColor = isTranslucent ? 'transparent' : 'rgba(0,0,0,0.5)';
    ctx.shadowBlur = isTranslucent ? 0 : 10;
    
    // Selection Halo
    if (name === store.selectedComponent) {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        ctx.beginPath(); 
        // Use circumscribed circle for halo to ensure it covers the shape
        const r = Math.sqrt(halfW*halfW + halfH*halfH) + 5;
        ctx.arc(0, 0, r, 0, Math.PI * 2); 
        ctx.stroke();
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
        const r = Math.sqrt(halfW*halfW + halfH*halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.stroke();
        ctx.setLineDash([]);
    }

    if (mode === 'HOLDING') {
        ctx.strokeStyle = HOLDING_STEADY_COLOR;
        ctx.lineWidth = 2;
        const r = Math.sqrt(halfW*halfW + halfH*halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.stroke();
    }

    if (store.isOptimizing && store.pendingCommands.has(name)) {
        ctx.shadowColor = '#10b981'; // Green glow
        ctx.shadowBlur = 20;
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        const r = Math.sqrt(halfW*halfW + halfH*halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.stroke();
    }

    // Draw Specific Icons based on Catalog ID or Type
    const catalogItem = store.catalogMap[name];
    const catalogId = catalogItem ? catalogItem.id : null;

    if (catalogId === 'nd_filter') {
        // ND Filter: Dark Neutral (Black/Grey)
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        // Dark Glass look
        ctx.fillStyle = 'rgba(20, 20, 20, 0.9)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);

    } else if (catalogId === 'filter_generic') {
        // Generic Filter: Colored (e.g. Red/Pink)
        ctx.fillStyle = '#333';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#f87171'; // Reddish border
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        // Tinted Glass look
        ctx.fillStyle = 'rgba(248, 113, 113, 0.3)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);

    } else if (catalogId === 'cam_gripper_1' || catalogId === 'cam_gripper_2' || type === 'OPTICAL_CAMERA') {
        // Camera
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(-halfW, -halfH, w, h);
        // Lens ring
        ctx.fillStyle = '#000';
        ctx.beginPath(); ctx.arc(0, 0, Math.min(w,h)/3, 0, Math.PI * 2); ctx.fill();
        // Sensor reflection
        ctx.fillStyle = '#3b82f6'; // Blueish reflection
        ctx.beginPath(); ctx.arc(0, 0, Math.min(w,h)/8, 0, Math.PI * 2); ctx.fill();
        // Direction indicator
        ctx.fillStyle = '#ef4444';
        const triH = h/4;
        ctx.beginPath(); ctx.moveTo(0, -halfH - 2); ctx.lineTo(-triH/2, -halfH - triH - 2); ctx.lineTo(triH/2, -halfH - triH - 2); ctx.fill();

    } else if (catalogId === 'mirror_curved') {
        // Curved (concave) OC: left semicircle in local space, opening toward +local X (canvas right before pose.rotation).
        // Previous code used ±PI/4 extra on arc angles, rotating the opening ~45° and making e.g. 270° look ~225°.
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
        // Planar Mirror
        // Draw fitting in w x h box.
        // Usually thin in one dimension, wide in other.
        // But footprint is 90x90.
        // We'll draw the mirror face along Y axis, centered.
        
        ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.moveTo(0, -halfH); ctx.lineTo(0, halfH); ctx.stroke();
        // Mount backing
        ctx.fillStyle = '#444'; ctx.fillRect(-halfW/2, -halfH, halfW/2, h); 
        // Reflective side hint
        ctx.strokeStyle = 'rgba(255,255,255,0.5)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(2, -halfH + 5); ctx.lineTo(2, halfH - 5); ctx.stroke();

    } else if (catalogId === 'beam_block') {
        // Beam Block: Solid dark block with cross
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.beginPath(); 
        ctx.moveTo(-halfW, -halfH); ctx.lineTo(halfW, halfH);
        ctx.moveTo(halfW, -halfH); ctx.lineTo(-halfW, halfH);
        ctx.stroke();
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);

    } else if (catalogId === 'beam_splitter' || type === 'OPTICAL_BEAMSPLITTER') {
        // Beam Splitter: Cube
        ctx.fillStyle = 'rgba(200, 200, 200, 0.1)';
        ctx.strokeStyle = '#888'; ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        // Diagonal coating
        ctx.strokeStyle = 'rgba(100, 200, 255, 0.8)';
        ctx.beginPath(); ctx.moveTo(-halfW, -halfH); ctx.lineTo(halfW, halfH); ctx.stroke();

    } else if (catalogId === 'lens_main' || type === 'OPTICAL_LENS') {
        // Lens: Ellipse fitting the box
        ctx.fillStyle = 'rgba(100, 200, 255, 0.3)';
        ctx.strokeStyle = 'rgba(150, 220, 255, 0.9)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.ellipse(0, 0, halfW/3, halfH, 0, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        
    } else if (catalogId === 'crystal_main' || type === 'OPTICAL_CRYSTAL') {
        // Crystal: Hexagon or Rectangle fitting box
        ctx.fillStyle = 'rgba(236, 72, 153, 0.3)'; // Pinkish
        ctx.strokeStyle = '#ec4899';
        ctx.lineWidth = 2;
        ctx.beginPath();
        // Draw Hexagon fitting in w/h
        ctx.moveTo(-halfW/2, -halfH); ctx.lineTo(halfW/2, -halfH);
        ctx.lineTo(halfW, 0);
        ctx.lineTo(halfW/2, halfH); ctx.lineTo(-halfW/2, halfH);
        ctx.lineTo(-halfW, 0);
        ctx.closePath();
        ctx.fill(); ctx.stroke();

    } else {
        // Default / Unknown
        ctx.fillStyle = '#C0C0C0'; 
        const r = Math.min(halfW, halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#000';
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText("?", 0, 4);
    }

    ctx.restore();
    
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = (mode === 'GHOST' || mode === 'HOLDING') ? 'rgba(255, 255, 255, 0.5)' : 'rgba(255, 255, 255, 0.9)';
    ctx.font = '500 11px Inter, sans-serif';
    ctx.textAlign = 'center';
    
    // Resolve Display Name from Catalog
    let displayName = name;
    if (store.catalogMap[name]) {
        displayName = store.catalogMap[name].name;
    }
    
    ctx.fillText(displayName, 0, -halfH - 10);
    const stLab = store.labState && store.labState.components[name] && placementUiLabel(store.labState.components[name]);
    if (mode === 'SOLID' && stLab === 'STORED') {
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
        ctx.fillText("OPTIMIZING...", 0, halfH + 15);
    }

    ctx.restore();
}

function drawOptimizationGraph() {
    if (!store.isOptimizing || store.optimizationData.length === 0) return;
    if (!store.labState || store.labState.lab_mode !== 'MOCK') return;

    const w = 300;
    const h = 150;
    const x = CANVAS_WIDTH - w - 20;
    const y = CANVAS_HEIGHT - h - 20;

    // Background
    ctx.fillStyle = 'rgba(24, 27, 33, 0.9)';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#2a2e36';
    ctx.strokeRect(x, y, w, h);

    // Title
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px Inter';
    ctx.fillText("Optimization Metric (Beam Intensity)", x + 10, y + 20);

    // Plot
    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;

    const maxSteps = 20; // assumed max
    const xScale = (w - 20) / maxSteps;
    const yScale = (h - 40); // 0-1 normalized

    store.optimizationData.forEach((point, i) => {
        const px = x + 10 + point.step * xScale;
        const py = y + h - 10 - point.value * yScale;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
    });
    ctx.stroke();
}

function render() {
    clearCanvas();
    drawStorageZone();
    drawLaserPath();

    if (!store.labState) return;

    // 1. Draw Physical Components (Solid)
    Object.entries(store.labState.components).forEach(([name, comp]) => {
        if (isOnTableComponent(comp)) {
            drawComponent(name, measPose(comp), comp.type, 'SOLID');
        }
    });

    // 2. Draw Ghost Components (Intent) — synced from tunables.nominal_pose
    Object.entries(store.ghostState).forEach(([name, pose]) => {
        const type = store.labState.components[name]?.type || 'UNKNOWN';
        const isPending = store.pendingCommands.has(name);
        // Steady-state HOLDING: system reports HOLDING and this tag is the
        // one in the gripper, with no primitive currently in flight. We draw
        // a distinct "HOLDING" ghost (solid purple halo) so the label
        // oscillates naturally: PICKING UP... -> HOLDING -> HOVERING... ->
        // HOLDING -> PLACING... as the operator works in-air.
        const isHeldSteady = !isPending && isHeldTag(name, store.labState);
        const mode = isPending ? 'PENDING' : (isHeldSteady ? 'HOLDING' : 'GHOST');
        drawComponent(name, pose, type, mode);

        // Draw Drift Line (Nominal vs Physical)
        const physical = store.labState.components[name];
        if (physical && isOnTableComponent(physical)) {
            const mp = measPose(physical);
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

// --- 5. UI Logic ---

function getComponentIcon(type) {
    switch(type) {
        case 'OPTICAL_MIRROR': return 'crop_portrait';
        case 'OPTICAL_LENS': return 'lens';
        case 'OPTICAL_BEAMSPLITTER': return 'dashboard';
        case 'OPTICAL_CAMERA': return 'videocam';
        default: return 'help_outline';
    }
}

function updateUI() {
    if (!store.labState) return;

    const status = store.labState.system_status;
    let badgeClass = 'active';
    let badgeColor = 'placed'; // green
    let badgeStyle = '';
    let badgeSuffix = '';

    if (status === 'BUSY') {
        badgeClass = '';
        badgeColor = 'inventory'; // blue/default
        badgeStyle = 'background-color: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.4);';
    } else if (status === 'OPTIMIZING') {
        badgeClass = '';
        badgeColor = 'placed';
        badgeStyle = 'background-color: #10b981; box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);';
    } else if (status === 'HOLDING') {
        badgeClass = '';
        badgeColor = '';
        const hld = getHolding(store.labState);
        if (hld.requires_operator_confirm) {
            badgeStyle = 'background-color: #ef4444; box-shadow: 0 0 8px rgba(239, 68, 68, 0.5);';
            badgeSuffix = ' · UNCONFIRMED';
        } else {
            badgeStyle = 'background-color: #a855f7; box-shadow: 0 0 8px rgba(168, 85, 247, 0.45);';
            badgeSuffix = hld.tag_id ? ` · ${hld.tag_id}` : '';
        }
    }

    statusBadge.className = `system-status ${badgeClass}`;
    statusBadge.innerHTML = `<span class="status-dot ${badgeColor}" style="${badgeStyle}"></span> ${status}${badgeSuffix}`;

    updateHoldingBanner();

    // Update Sidebar Selection if active
    if (store.selectedComponent) {
        // updateSidebarSelection(); // function removed previously
    }

    componentList.innerHTML = '';

    const components = store.labState.components || {};
    
    // Check if there are any placed items
    // (We treat everything in components as 'placed' or at least 'in lab' for the sidebar list)
    const placedCount = Object.keys(components).length;
    
    if (placedCount === 0) {
        componentList.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b; font-size: 11px;">No components placed.</div>';
    }
    
    Object.entries(components).forEach(([name, comp]) => {
        const card = document.createElement('div');
        card.className = 'component-card';
        if (name === store.selectedComponent) card.style.borderColor = '#3b82f6'; 
        // card.draggable = true; // Dragging from sidebar to move? Maybe, but mostly we select and use context panel.
        
        // card.addEventListener('dragstart', (e) => handleInventoryDragStart(e, name));
        const isPlaced = isOnTableComponent(comp);
        
        // Resolve Real Name from Catalog using Tag ID
        let displayName = name; // Default to key if unknown
        let displayType = comp.type;
        let unknownTag = false;

        if (store.catalogMap[comp.id]) {
            displayName = store.catalogMap[comp.id].name;
            // displayType = store.catalogMap[comp.id].type; // Ensure type matches catalog
        } else {
            // Unknown Tag Logic
            displayName = `Unknown (${comp.id})`;
            unknownTag = true;
        }

        const icon = getComponentIcon(comp.type);
        
        // Sidebar status-dot priority ladder (highest wins):
        //   1. HOLDING (this tag is in the gripper right now)   — purple
        //   2. OPTIMIZED (current placement came from a strategy)— green + gold halo
        //   3. STORED (Q3 storage region)                       — indigo
        //   4. PLACED on breadboard                             — green
        //   5. OFF_TABLE inventory                              — blue
        // Note: hasOptimizationOutcome() alone is NOT enough for "optimized"
        // styling -- isOptimizedPlacement() also requires the current
        // placement.mode to be a strategy name (i.e. the part hasn't been
        // manually re-moved since the optimizer ran). Hovering tooltip shows
        // the raw placement label (e.g. MANUAL / COBYLA / HOVER) for detail.
        let statusDot;
        if (isHeldTag(name, store.labState)) {
            statusDot = `<div class="status-dot holding" title="Held by gripper"></div>`;
        } else if (isOptimizedPlacement(comp)) {
            statusDot = `<div class="status-dot optimized" title="Optimized (${placementUiLabel(comp)})"></div>`;
        } else if (isStoredComponent(comp)) {
            statusDot = `<div class="status-dot stored" title="Stored (Q3)"></div>`;
        } else {
            statusDot = `<div class="status-dot ${isPlaced ? 'placed' : 'inventory'}" title="${placementUiLabel(comp)}"></div>`;
        }

        // Motor Badge
        let motorBadge = '';
        if (store.catalogMap[comp.id] && store.catalogMap[comp.id].motor_ids && store.catalogMap[comp.id].motor_ids.length > 0) {
            motorBadge = `<span class="material-icons-round" style="font-size: 12px; color: #f59e0b; margin-right: 4px;" title="Motorized">settings_input_component</span>`;
        }

        card.innerHTML = `
            <div class="comp-icon material-icons-round">${icon}</div>
            <div class="comp-info">
                <span class="comp-name" style="${unknownTag ? 'color: #f59e0b;' : ''}">${displayName}</span>
                <span class="comp-meta">${motorBadge}${displayType.replace('OPTICAL_', '')} • ${comp.id}</span>
            </div>
            ${statusDot}
        `;
        
        // Click listener for selection
        card.addEventListener('click', () => {
            store.selectedComponent = name;
            updateContextPanel(name);
            render();
        });

        componentList.appendChild(card);
    });

    syncTableCamMockHint();
    updateMotorAngleLabels(store.selectedComponent);
    updateLayoutWarningBanner();
    render();
}

function updateLayoutWarningBanner() {
    const el = document.getElementById('layout-warnings');
    if (!el || !store.labState) return;
    const local = collectLayoutWarnings(store.labState);
    const server = (store.layoutIssues || []).map((i) => i.message);
    const seen = new Set();
    const lines = [];
    for (const s of [...server, ...local]) {
        if (!seen.has(s)) {
            seen.add(s);
            lines.push(s);
        }
    }
    if (!lines.length) {
        el.style.display = 'none';
        el.textContent = '';
        return;
    }
    el.style.display = 'block';
    el.innerHTML =
        '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;color:#f59e0b;">warning</span> ' +
        '<strong>Layout</strong>: ' +
        lines.map((w) => `<span style="display:block;margin-top:4px;">${w}</span>`).join('');
}

const dismissedLayoutIssueKeys = new Set();

function layoutIssueKey(issue) {
    return `${issue.kind}:${issue.tag_id}`;
}

function layoutConflictMoveDefaults(tagId) {
    const c = store.labState?.components?.[tagId];
    const g = store.ghostState[tagId];
    const mp = measPose(c || {});
    const rot = typeof mp.rotation === 'number' ? mp.rotation : 0;
    if (g && typeof g.x === 'number' && typeof g.y === 'number' && !isStorageRegion(g.x, g.y)) {
        return { x: g.x, y: g.y, rotation: typeof g.rotation === 'number' ? g.rotation : rot };
    }
    return { x: 150, y: 150, rotation: rot };
}

function removeLayoutConflictModal() {
    document.getElementById('layout-conflict-modal')?.remove();
}

function updateLayoutConflictModal() {
    if (!store.labState || store.labState.system_status !== 'IDLE') {
        removeLayoutConflictModal();
        return;
    }
    const issues = store.layoutIssues || [];
    for (const k of [...dismissedLayoutIssueKeys]) {
        if (!issues.some((i) => layoutIssueKey(i) === k)) dismissedLayoutIssueKeys.delete(k);
    }
    const next = issues.find((i) => !dismissedLayoutIssueKeys.has(layoutIssueKey(i)));
    if (!next) {
        removeLayoutConflictModal();
        return;
    }
    const existing = document.getElementById('layout-conflict-modal');
    const prevKey = existing?.dataset?.issueKey;
    const key = layoutIssueKey(next);
    if (existing && prevKey === key) return;

    removeLayoutConflictModal();
    const overlay = document.createElement('div');
    overlay.id = 'layout-conflict-modal';
    overlay.dataset.issueKey = key;
    overlay.style.cssText =
        'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:2990;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

    const card = document.createElement('div');
    card.style.cssText =
        'background:#181b21;border:1px solid rgba(245,158,11,0.45);border-radius:10px;padding:22px;max-width:440px;width:92%;box-shadow:0 20px 50px rgba(0,0,0,0.65);';

    const kind = next.kind;
    const tid = next.tag_id;
    let bodyHtml = `<h3 style="margin:0 0 8px 0;color:#e2e8f0;font-size:16px;display:flex;align-items:center;gap:8px;"><span class="material-icons-round" style="color:#f59e0b;font-size:22px;">warning</span> Inventory layout</h3>`;
    bodyHtml += `<p style="margin:0 0 16px 0;color:#94a3b8;font-size:13px;line-height:1.45;">${next.message}</p>`;

    if (kind === 'PLACED_IN_Q3') {
        const d = layoutConflictMoveDefaults(tid);
        bodyHtml += `<div style="font-size:11px;color:#64748b;margin-bottom:8px;">Move to a breadboard pose (mm, degrees):</div>`;
        bodyHtml += `<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px;">`;
        bodyHtml += `<label style="font-size:10px;color:#94a3b8;">X<br><input id="lconf-tx" type="number" step="0.1" value="${d.x.toFixed(1)}" style="width:100%;padding:6px;border-radius:6px;border:1px solid #334155;background:#0f1115;color:#e2e8f0;"></label>`;
        bodyHtml += `<label style="font-size:10px;color:#94a3b8;">Y<br><input id="lconf-ty" type="number" step="0.1" value="${d.y.toFixed(1)}" style="width:100%;padding:6px;border-radius:6px;border:1px solid #334155;background:#0f1115;color:#e2e8f0;"></label>`;
        bodyHtml += `<label style="font-size:10px;color:#94a3b8;">Rot<br><input id="lconf-tr" type="number" step="0.1" value="${d.rotation.toFixed(1)}" style="width:100%;padding:6px;border-radius:6px;border:1px solid #334155;background:#0f1115;color:#e2e8f0;"></label>`;
        bodyHtml += `</div>`;
        bodyHtml += `<div style="display:flex;flex-direction:column;gap:8px;">`;
        bodyHtml += `<button type="button" id="lconf-move" class="btn btn-primary" style="width:100%;justify-content:center;">Move to target</button>`;
        bodyHtml += `<button type="button" id="lconf-store" class="btn btn-secondary" style="width:100%;justify-content:center;">Store with packing (grid)</button>`;
        bodyHtml += `<button type="button" id="lconf-dismiss" class="btn btn-secondary" style="width:100%;opacity:0.85;">Dismiss</button>`;
        bodyHtml += `</div>`;
    } else if (kind === 'STORED_OUTSIDE_Q3' || kind === 'STORED_OFF_SLOT') {
        bodyHtml += `<div style="display:flex;flex-direction:column;gap:8px;">`;
        bodyHtml += `<button type="button" id="lconf-affirm" class="btn btn-primary" style="width:100%;justify-content:center;">Mark as PLACED (keep current pose)</button>`;
        bodyHtml += `<button type="button" id="lconf-repack" class="btn btn-secondary" style="width:100%;justify-content:center;">Repack into storage (grid)</button>`;
        bodyHtml += `<button type="button" id="lconf-dismiss" class="btn btn-secondary" style="width:100%;opacity:0.85;">Dismiss</button>`;
        bodyHtml += `</div>`;
    } else {
        bodyHtml += `<button type="button" id="lconf-dismiss" class="btn btn-secondary" style="width:100%;">Dismiss</button>`;
    }

    card.innerHTML = bodyHtml;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const dismiss = () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
    };

    overlay.querySelector('#lconf-dismiss')?.addEventListener('click', dismiss);

    overlay.querySelector('#lconf-move')?.addEventListener('click', async () => {
        const tx = parseFloat(document.getElementById('lconf-tx')?.value || '0');
        const ty = parseFloat(document.getElementById('lconf-ty')?.value || '0');
        const tr = parseFloat(document.getElementById('lconf-tr')?.value || '0');
        if (isStorageRegion(tx, ty)) {
            log('Target must not be in storage quadrant (not both x<0 and y<0).', 'error');
            return;
        }
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await executeSendCommand({
            action: 'MOVE_COMPONENT',
            target_id: tid,
            parameters: { target_x: tx, target_y: ty, rotation: tr },
        });
    });

    overlay.querySelector('#lconf-store')?.addEventListener('click', async () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await executeSendCommand({ action: 'STORE_COMPONENT', target_id: tid, parameters: {} });
    });

    overlay.querySelector('#lconf-affirm')?.addEventListener('click', async () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await executeSendCommand({ action: 'AFFIRM_PLACED_AT_CURRENT', target_id: tid, parameters: {} });
    });

    overlay.querySelector('#lconf-repack')?.addEventListener('click', async () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await executeSendCommand({ action: 'REPACK_STORAGE', target_id: tid, parameters: {} });
    });
}

function syncTableCamMockHint() {
    const el = document.getElementById('table-cam-mock-hint');
    if (!el || !store.labState) return;
    el.style.display = store.labState.lab_mode === 'MOCK' ? 'block' : 'none';
}

function renderRecipes() {
    recipeList.innerHTML = '';
    if (store.availableRecipes.length === 0) {
        recipeList.innerHTML = '<div style="color: #64748b; font-size: 11px; padding: 10px; text-align: center;">No recipes saved.</div>';
        return;
    }

    store.availableRecipes.forEach(recipe => {
        const item = document.createElement('div');
        item.style.backgroundColor = 'rgba(255,255,255,0.03)';
        item.style.border = '1px solid #2a2e36';
        item.style.borderRadius = '6px';
        item.style.padding = '8px';
        item.style.marginBottom = '6px';
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.justifyContent = 'space-between';

        item.innerHTML = `
            <div style="overflow: hidden;">
                <div style="font-weight: 500; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${recipe.name}</div>
                <div style="font-size: 10px; color: #64748b;">${recipe.steps.length} steps</div>
            </div>
        `;

        const playBtn = document.createElement('button');
        playBtn.className = 'btn btn-primary';
        playBtn.style.padding = '4px 8px';
        playBtn.style.fontSize = '10px';
        playBtn.style.width = 'auto';
        playBtn.innerHTML = '<span class="material-icons-round" style="font-size: 14px;">play_arrow</span>';
        playBtn.title = "Run Recipe";
        playBtn.onclick = () => playRecipe(recipe.id);

        item.appendChild(playBtn);
        recipeList.appendChild(item);
    });
}

// --- Recipe Controls ---

function updateRecipeEditorList() {
    recipeStepsContainer.innerHTML = '';
    if (store.currentRecipeSteps.length === 0) {
        recipeStepsContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b; font-size: 12px;">No steps recorded yet.</div>';
        return;
    }

    store.currentRecipeSteps.forEach((step, index) => {
        const item = document.createElement('div');
        item.className = 'recipe-step-item';
        
        let desc = `${step.component}`;
        if (step.action === 'MOVE_COMPONENT') {
            desc += ` to (${step.parameters.target_x.toFixed(1)}, ${step.parameters.target_y.toFixed(1)})`;
        } else if (step.action === 'OPTIMIZE') {
            desc += ` with ${step.parameters.strategy || 'NEWTON'}`;
        }

        item.innerHTML = `
            <div class="step-num">${index + 1}</div>
            <div class="step-action">${step.action === 'MOVE_COMPONENT' ? 'MOVE' : 'OPTIMIZE'}</div>
            <div class="step-desc">${desc}</div>
            <div class="material-icons-round step-del" title="Remove Step">delete</div>
        `;

        item.querySelector('.step-del').addEventListener('click', () => deleteStep(index));
        recipeStepsContainer.appendChild(item);
    });
}

function deleteStep(index) {
    store.currentRecipeSteps.splice(index, 1);
    // Re-assign step numbers if needed, though mostly visual
    updateRecipeEditorList();
}

recordBtn.addEventListener('click', () => {
    store.isRecording = !store.isRecording; // Toggle recording
    
    if (store.isRecording) {
        store.currentRecipeSteps = [];
        recipeEditorName.value = `Recipe ${new Date().toLocaleTimeString()}`;
        updateRecipeEditorList();
        recIndicator.style.display = 'flex';
        recordBtn.classList.add('btn-primary'); // Highlight
        recordBtn.classList.remove('btn-secondary');
        log("Recording started. Perform actions on the canvas.", "warn");
    } else {
        recIndicator.style.display = 'none';
        recordBtn.classList.remove('btn-primary');
        recordBtn.classList.add('btn-secondary');
        log("Recording stopped.", "info");
    }
});

recipeEditorSave.addEventListener('click', async () => {
    if (store.currentRecipeSteps.length === 0) {
        alert("No actions recorded!");
        return;
    }
    
    const name = recipeEditorName.value || "Untitled Recipe";
    const id = name.toLowerCase().replace(/[^a-z0-9]/g, '_') + '_' + Math.floor(Math.random() * 1000);

    // Re-number steps just in case
    const steps = store.currentRecipeSteps.map((s, i) => ({ ...s, step: i + 1 }));

    const recipe = {
        id: id,
        name: name,
        steps: steps
    };

    try {
        const res = await fetch('/api/recipes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(recipe)
        });
        
        if (res.ok) {
            log(`Recipe "${name}" saved!`, "info");
            store.isRecording = false;
            recIndicator.style.display = 'none';
            recordBtn.classList.remove('btn-primary');
            recordBtn.classList.add('btn-secondary');
            fetchRecipes();
        }
    } catch (e) {
        log("Failed to save recipe.", "error");
    }
});

// --- Recipe Panel Dragging --- REMOVED
// Logic removed as panel is now static in sidebar


async function playRecipe(id) {
    // 1. Find the recipe object
    const recipe = store.availableRecipes.find(r => r.id === id);
    if (!recipe) {
        log("Recipe not found locally.", "error");
        return;
    }

    // 2. Check Requirements
    const reqs = checkRecipeRequirements(recipe);
    
    if (!reqs.valid) {
        showRecipeRequirementModal(recipe.name, reqs);
        return;
    }

    try {
        log(`Playing recipe ${id}...`, "info");
        const res = await fetch(`/api/recipes/${id}/play`, { method: 'POST' });
        if (res.ok) {
            log("Recipe execution started.", "info");
        } else {
            const err = await res.json();
            log(`Failed to start recipe: ${err.detail}`, "error");
        }
    } catch (e) {
        log("Network error starting recipe.", "error");
    }
}

function checkRecipeRequirements(recipe) {
    if (!store.labState || !store.labState.components) return { valid: false, error: "Lab state not loaded" };
    
    const missingRequestable = [];
    const missingUnknown = [];
    
    // Get all unique components referenced in recipe
    const requiredComponents = new Set();
    recipe.steps.forEach(step => {
        if (step.component) requiredComponents.add(step.component);
        // Fallback for older recipe formats if they used 'target' or 'target_id'
        if (step.target) requiredComponents.add(step.target);
    });
    
    requiredComponents.forEach(id => {
        // Check if it exists in the current lab state
        if (!store.labState.components[id]) {
            // Check if in catalog
            if (store.catalogMap[id]) {
                missingRequestable.push(store.catalogMap[id]);
            } else {
                missingUnknown.push(id);
            }
        }
    });
    
    return {
        valid: missingRequestable.length === 0 && missingUnknown.length === 0,
        missingRequestable,
        missingUnknown
    };
}

function showRecipeRequirementModal(recipeName, reqs) {
    const existing = document.getElementById('req-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'req-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #f59e0b'; // Amber warning
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '500px';
    card.style.maxHeight = '80vh';
    card.style.overflowY = 'auto';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    let html = `
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
            <span class="material-icons-round" style="font-size: 32px; color: #f59e0b;">warning_amber</span>
            <h2 style="margin: 0; color: #e2e8f0; font-size: 18px;">Components Missing</h2>
        </div>
        <p style="color: #94a3b8; font-size: 13px; margin-bottom: 20px;">
            The recipe <strong>"${recipeName}"</strong> cannot run because some components are not present in the lab.
        </p>
    `;

    if (reqs.missingUnknown.length > 0) {
        html += `
            <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 6px; padding: 12px; margin-bottom: 16px;">
                <div style="color: #ef4444; font-weight: 600; font-size: 12px; margin-bottom: 8px;">OUTDATED / UNKNOWN COMPONENTS</div>
                <div style="font-size: 12px; color: #cbd5e1;">
                    The following IDs are not found in the Catalog. The recipe may be outdated.
                    <ul style="margin: 8px 0 0 20px; padding: 0;">
                        ${reqs.missingUnknown.map(id => `<li>${id}</li>`).join('')}
                    </ul>
                </div>
            </div>
        `;
    }

    if (reqs.missingRequestable.length > 0) {
        html += `
            <div style="margin-bottom: 16px;">
                <div style="color: #e2e8f0; font-weight: 600; font-size: 12px; margin-bottom: 8px;">AVAILABLE TO REQUEST</div>
                <div id="req-list" style="display: flex; flex-direction: column; gap: 8px;">
                    <!-- Items injected via JS -->
                </div>
            </div>
        `;
    }

    html += `
        <div style="display: flex; justify-content: flex-end; gap: 12px; margin-top: 24px;">
            <button id="req-close-btn" class="btn btn-secondary" style="width: auto;">Close</button>
        </div>
    `;

    card.innerHTML = html;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    // Event Listeners
    document.getElementById('req-close-btn').onclick = () => overlay.remove();

    // Render Requestable Items
    const listContainer = document.getElementById('req-list');
    if (listContainer && reqs.missingRequestable.length > 0) {
        reqs.missingRequestable.forEach(item => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.justifyContent = 'space-between';
            row.style.background = '#0f1115';
            row.style.padding = '8px 12px';
            row.style.borderRadius = '4px';
            row.style.border = '1px solid #2a2e36';

            const icon = getComponentIcon(item.type);
            
            row.innerHTML = `
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span class="material-icons-round" style="color: #64748b; font-size: 18px;">${icon}</span>
                    <div>
                        <div style="font-size: 12px; color: #e2e8f0; font-weight: 500;">${item.name}</div>
                        <div style="font-size: 10px; color: #64748b;">${item.tag_id}</div>
                    </div>
                </div>
            `;

            const btn = document.createElement('button');
            btn.className = 'btn btn-primary';
            btn.style.width = 'auto';
            btn.style.padding = '4px 10px';
            btn.style.fontSize = '10px';
            btn.textContent = 'Request';
            
            btn.onclick = async () => {
                btn.textContent = 'Requesting...';
                btn.disabled = true;
                try {
                    const res = await fetch('/api/components', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(item)
                    });
                    if (res.ok) {
                        btn.textContent = 'Requested';
                        btn.style.backgroundColor = '#10b981';
                        btn.style.borderColor = '#10b981';
                    } else {
                        btn.textContent = 'Failed';
                        btn.disabled = false;
                    }
                } catch (e) {
                    btn.textContent = 'Error';
                    btn.disabled = false;
                }
            };

            row.appendChild(btn);
            listContainer.appendChild(row);
        });
    }
}


function log(message, type = 'info') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logOutput.prepend(entry);
    if (logOutput.children.length > 50) logOutput.removeChild(logOutput.lastChild);
}

/** Initialize store.ghostState[tagId] from lab state for Command Console moves. */
function ensureGhostForConsole(tagId) {
    if (store.ghostState[tagId]) return true;
    const comp = store.labState && store.labState.components && store.labState.components[tagId];
    const mp = measPose(comp || {});
    if (!comp || !isBreadboardIntent(comp) || (mp.x === undefined && mp.y === undefined)) return false;
    store.ghostState[tagId] = {
        x: mp.x,
        y: mp.y,
        rotation: typeof mp.rotation === 'number' ? mp.rotation : 0
    };
    return true;
}

function _laserLineAttrEscape(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;');
}

function renderLaserLinesPanel() {
    const root = document.getElementById('laser-lines-list');
    if (!root) return;
    const doc = store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines) || doc.lines.length === 0) {
        root.innerHTML =
            '<div style="padding:10px 12px;font-size:11px;color:var(--text-muted);line-height:1.4;">No lines defined. Add entries in <code>schemas/laser_lines.*.json</code> for this lab mode.</div>';
        return;
    }
    const snap = doc.snap_line_id;
    root.innerHTML = doc.lines
        .map((line) => {
            const id = line.id || '';
            const name = (line.name || id).replace(/</g, '\u003c');
            const en = line.enabled !== false;
            const rawC = (line.color && String(line.color).trim()) || '#ff3b3b';
            const c = /^#[0-9A-Fa-f]{3,8}$/i.test(rawC) ? rawC : '#ff3b3b';
            const idA = _laserLineAttrEscape(id);
            const snapTag =
                id === snap
                    ? ' <span style="font-size:9px;color:var(--text-muted);">(snap)</span>'
                    : '';
            return (
                `<div class="laser-line-row" data-line-id="${idA}" title="Double-click to edit reference points">` +
                `<input type="checkbox" class="laser-line-enabled" data-line-id="${idA}" ${en ? 'checked' : ''} title="Show on canvas">` +
                `<span class="laser-line-swatch" style="background:${c}"></span>` +
                `<span class="laser-line-name">${name}</span>${snapTag}` +
                `</div>`
            );
        })
        .join('');
}

function closeLaserLineEditModal() {
    const m = document.getElementById('laser-line-edit-modal');
    if (m) {
        m.style.display = 'none';
        m.setAttribute('aria-hidden', 'true');
    }
    const ch = document.getElementById('laser-line-edit-confirm');
    if (ch) ch.checked = false;
}

function openLaserLineEditModal(lineId) {
    const doc = store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines)) return;
    const line = doc.lines.find((l) => l && l.id === lineId);
    if (!line || !line.p1 || !line.p2) return;
    const modal = document.getElementById('laser-line-edit-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    modal.dataset.lineId = lineId;
    const title = document.getElementById('laser-line-edit-title');
    if (title) title.textContent = `Edit line: ${line.name || lineId}`;
    const setNum = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.value = Number(v);
    };
    setNum('laser-edit-p1x', line.p1.x);
    setNum('laser-edit-p1y', line.p1.y);
    setNum('laser-edit-p2x', line.p2.x);
    setNum('laser-edit-p2y', line.p2.y);
    const ch = document.getElementById('laser-line-edit-confirm');
    if (ch) ch.checked = false;
}

async function applyLaserLineGeometryEdit() {
    const modal = document.getElementById('laser-line-edit-modal');
    const confirmEl = document.getElementById('laser-line-edit-confirm');
    const lineId = modal && modal.dataset.lineId;
    if (!lineId) return;
    if (!confirmEl || !confirmEl.checked) {
        log('Check "I confirm" to apply reference point changes.', 'warn');
        return;
    }
    const read = (id) => {
        const el = document.getElementById(id);
        return el ? parseFloat(el.value) : NaN;
    };
    const p1 = { x: read('laser-edit-p1x'), y: read('laser-edit-p1y') };
    const p2 = { x: read('laser-edit-p2x'), y: read('laser-edit-p2y') };
    if (![p1.x, p1.y, p2.x, p2.y].every(Number.isFinite)) {
        log('Enter valid numbers for all coordinates.', 'error');
        return;
    }
    try {
        const res = await fetch(`/api/laser-lines/${encodeURIComponent(lineId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ p1, p2, confirm: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        closeLaserLineEditModal();
        await fetchLaserLines();
        render();
        log(`Laser line "${lineId}" geometry updated.`, 'info');
    } catch (e) {
        console.error(e);
        log(`Laser line update failed: ${e.message || e}`, 'error');
    }
}

function initLaserLinesPanel() {
    const list = document.getElementById('laser-lines-list');
    if (!list) return;

    list.addEventListener('change', async (e) => {
        const t = e.target;
        if (!t.classList.contains('laser-line-enabled')) return;
        const id = t.getAttribute('data-line-id');
        if (!id) return;
        try {
            const res = await fetch(`/api/laser-lines/${encodeURIComponent(id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: !!t.checked }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || res.statusText);
            store.laserLinesDoc = data;
            store.laserLineCoeffs = coeffsFromLaserLinesDoc(store.laserLinesDoc);
            renderLaserLinesPanel();
            render();
            log(`Laser line "${id}" ${t.checked ? 'shown' : 'hidden'}.`, 'info');
        } catch (err) {
            console.error(err);
            t.checked = !t.checked;
            log(`Toggle failed: ${err.message || err}`, 'error');
        }
    });

    list.addEventListener('dblclick', (e) => {
        if (e.target.classList.contains('laser-line-enabled')) return;
        const row = e.target.closest('.laser-line-row');
        if (!row) return;
        const id = row.getAttribute('data-line-id');
        if (id) openLaserLineEditModal(id);
    });

    const cancel = document.getElementById('laser-line-edit-cancel');
    const apply = document.getElementById('laser-line-edit-apply');
    const modal = document.getElementById('laser-line-edit-modal');
    if (cancel) cancel.addEventListener('click', () => closeLaserLineEditModal());
    if (apply) apply.addEventListener('click', () => applyLaserLineGeometryEdit());
    if (modal) {
        modal.addEventListener('click', (ev) => {
            if (ev.target === modal) closeLaserLineEditModal();
        });
    }
    renderLaserLinesPanel();
}

/** Same behavior as the Refresh Pose button (shared with Command Console): camera pose pass → measurables.pose. */
async function runLabPoseRefresh() {
    log("Refreshing poses from camera (re-localize)...", "warn");
    const res = await fetch('/api/lab-state/refresh-pose', { method: 'POST' });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Refresh pose failed');
    }

    const start = Date.now();
    while (Date.now() - start < 30000) {
        const stateRes = await fetch('/api/lab-state');
        const state = await stateRes.json();
        if (state && state.system_status === 'IDLE') break;
        await new Promise(r => setTimeout(r, 500));
    }

    store.forceGhostSync = true;
    await fetchLaserLines();
    await fetchLabState();
    checkVideoStatus();
}

function init() {
    log("Interface loaded.");
    fetchStorageGridSpec();
    fetchStrategies();
    fetchRecipes();
    fetchLabState();
    setInterval(fetchLabState, POLLING_INTERVAL);
    refreshBtn.addEventListener('click', async () => {
        try {
            await runLabPoseRefresh();
        } catch (e) {
            console.error("Refresh pose failed:", e);
            log(`Refresh pose failed: ${e.message || e}`, "error");
            showErrorModal("Refresh Pose Failed", e.message || String(e));
        }
    });

    if (ctxPanelCloseBtn) {
        ctxPanelCloseBtn.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            clearSelectionAndHideContextPanel();
        });
    }

    if (saveStateBtn) {
        saveStateBtn.addEventListener('click', async () => {
            try {
                const defaultName = `state_${new Date().toISOString().replace(/[:.]/g, '-')}`;
                const name = prompt("Save current lab state as:", defaultName);
                if (!name) return;

                const res = await fetch('/api/states/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Save failed');

                log(`Saved lab state: ${data.name || name}`, "info");
            } catch (e) {
                console.error("Save state failed:", e);
                showErrorModal("Save Failed", e.message || String(e));
            }
        });
    }

    if (loadStateBtn) {
        loadStateBtn.addEventListener('click', async () => {
            try {
                const defaultName = `state_last`;
                const name = prompt("Load lab state (saved name):", defaultName);
                if (!name) return;

                const res = await fetch('/api/states/load', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Load failed');

                // Force UI+ghost to match loaded state immediately.
                store.forceGhostSync = true;
                await fetchLabState();
                log(`Loaded lab state: ${data.name || name}`, "info");
            } catch (e) {
                console.error("Load state failed:", e);
                showErrorModal("Load Failed", e.message || String(e));
            }
        });
    }
    
    initLaserLinesPanel();
    initVideoFeed();
    initUnifiedPanel();
}

// --- Unified Panel Logic (REMOVED - Panel is static) ---
function initUnifiedPanel() {
    // No dynamic minimization or tabs anymore
}

// --- Video Feed Logic ---
function initVideoFeed() {
    const fpsInput = document.getElementById('fps-input');
    if (fpsInput) {
        fpsInput.addEventListener('change', () => {
             const fps = Math.max(1, Math.min(60, parseInt(fpsInput.value) || 10));
             fpsInput.value = fps;
             // Reload video
             if (videoImg) {
                 // Base source is /api/video-feed/stream
                 const baseSrc = '/api/video-feed/stream';
                 videoImg.src = `${baseSrc}?fps=${fps}&t=${Date.now()}`;
                 log(`Video stream FPS set to ${fps}`, "info");
             }
        });
    }

    // Check status periodically
    setInterval(checkVideoStatus, 5000);
    checkVideoStatus();
}

// --- Table cam capture (on-demand, real lab only) ---
const tableCamBtn1 = document.getElementById('table-cam-btn-1');
const tableCamBtn2 = document.getElementById('table-cam-btn-2');
const tableCamCaptureBtn = document.getElementById('table-cam-capture-btn');
const tableCamImg = document.getElementById('table-cam-img');
const tableCamPlaceholder = document.getElementById('table-cam-placeholder');
const tableCamError = document.getElementById('table-cam-error');

function setTableCamSelection(camId) {
    store.selectedTableCam = camId;
    if (tableCamBtn1) {
        tableCamBtn1.classList.toggle('btn-primary', camId === 1);
        tableCamBtn1.classList.toggle('btn-secondary', camId !== 1);
    }
    if (tableCamBtn2) {
        tableCamBtn2.classList.toggle('btn-primary', camId === 2);
        tableCamBtn2.classList.toggle('btn-secondary', camId !== 2);
    }
}

if (tableCamBtn1) tableCamBtn1.addEventListener('click', () => setTableCamSelection(1));
if (tableCamBtn2) tableCamBtn2.addEventListener('click', () => setTableCamSelection(2));
setTableCamSelection(1);

function getTableCamExposureSeconds() {
    const el = document.getElementById('table-cam-exposure');
    if (!el) return store.tableCamExposure;
    const v = parseFloat(el.value);
    if (!Number.isFinite(v) || v <= 0) return store.tableCamExposure;
    store.tableCamExposure = v;
    return v;
}

if (tableCamCaptureBtn) {
    tableCamCaptureBtn.addEventListener('click', async () => {
        if (!tableCamImg || !tableCamPlaceholder || !tableCamError) return;
        const exp = getTableCamExposureSeconds();
        tableCamPlaceholder.textContent = 'Capturing...';
        tableCamPlaceholder.style.display = 'block';
        tableCamImg.style.display = 'none';
        tableCamImg.src = '';
        tableCamError.style.display = 'none';
        try {
            const res = await fetch(`/api/table-cam/capture?cam_id=${store.selectedTableCam}&exposure=${encodeURIComponent(exp)}`);
            if (res.ok) {
                const blob = await res.blob();
                if (store.tableCamLastBlobUrl) URL.revokeObjectURL(store.tableCamLastBlobUrl);
                store.tableCamLastBlobUrl = URL.createObjectURL(blob);
                tableCamImg.src = store.tableCamLastBlobUrl;
                tableCamImg.style.display = 'block';
                tableCamPlaceholder.style.display = 'none';
                tableCamPlaceholder.textContent = 'Click Capture to get image';
            } else {
                const err = (await res.json().catch(() => ({}))).detail || 'Capture failed';
                tableCamError.textContent = err;
                tableCamError.style.display = 'block';
                tableCamPlaceholder.style.display = 'none';
                tableCamPlaceholder.textContent = 'Click Capture to get image';
            }
        } catch (e) {
            tableCamError.textContent = e.message || 'Request failed';
            tableCamError.style.display = 'block';
            tableCamPlaceholder.style.display = 'none';
            tableCamPlaceholder.textContent = 'Click Capture to get image';
        }
    });
}

// --- Cobyla reference image (server-side BGR ndarray for CobylaAlignmentStrategy.reference_image) ---
const tableCamCobylaRefBtn = document.getElementById('table-cam-cobyla-ref-btn');
const tableCamCobylaClearBtn = document.getElementById('table-cam-cobyla-clear-btn');
const cobylaRefSaveBtn = document.getElementById('cobyla-ref-save-btn');
const cobylaRefLoadBtn = document.getElementById('cobyla-ref-load-btn');
const cobylaRefFileInput = document.getElementById('cobyla-ref-file-input');
const cobylaRefStatusEl = document.getElementById('cobyla-ref-status');
const cobylaRefPreviewImg = document.getElementById('cobyla-ref-preview-img');
const cobylaRefPlaceholderEl = document.getElementById('cobyla-ref-placeholder');

function revokeCobylaRefPreviewUrl() {
    if (store.cobylaRefPreviewObjectUrl) {
        URL.revokeObjectURL(store.cobylaRefPreviewObjectUrl);
        store.cobylaRefPreviewObjectUrl = null;
    }
}

function setCobylaRefPreviewVisible(hasImage) {
    if (cobylaRefPreviewImg && cobylaRefPlaceholderEl) {
        cobylaRefPreviewImg.style.display = hasImage ? 'block' : 'none';
        cobylaRefPlaceholderEl.style.display = hasImage ? 'none' : 'block';
    }
}

/** Load stored reference PNG into the red-bordered preview (Latest capture unchanged). */
async function refreshCobylaRefPreview() {
    if (!cobylaRefPreviewImg) return;
    revokeCobylaRefPreviewUrl();
    cobylaRefPreviewImg.src = '';
    try {
        const r = await fetch(`/api/cobyla-reference-image?t=${Date.now()}`);
        if (!r.ok) {
            setCobylaRefPreviewVisible(false);
            return;
        }
        const blob = await r.blob();
        store.cobylaRefPreviewObjectUrl = URL.createObjectURL(blob);
        cobylaRefPreviewImg.src = store.cobylaRefPreviewObjectUrl;
        setCobylaRefPreviewVisible(true);
    } catch {
        setCobylaRefPreviewVisible(false);
    }
}

/** @returns {Promise<boolean>} whether a reference is set on the server */
async function refreshCobylaRefStatus() {
    if (!cobylaRefStatusEl) return false;
    try {
        const r = await fetch('/api/cobyla-reference-image/status');
        if (!r.ok) {
            cobylaRefStatusEl.textContent = 'Cobyla ref: status unavailable';
            if (cobylaRefSaveBtn) cobylaRefSaveBtn.disabled = true;
            return false;
        }
        const d = await r.json();
        if (d.set && d.width && d.height) {
            cobylaRefStatusEl.textContent = `Cobyla ref: set (${d.width}×${d.height})`;
            if (cobylaRefSaveBtn) cobylaRefSaveBtn.disabled = false;
            return true;
        }
        cobylaRefStatusEl.textContent = 'Cobyla ref: not set';
        if (cobylaRefSaveBtn) cobylaRefSaveBtn.disabled = true;
        return false;
    } catch (e) {
        cobylaRefStatusEl.textContent = 'Cobyla ref: status error';
        if (cobylaRefSaveBtn) cobylaRefSaveBtn.disabled = true;
        return false;
    }
}

async function syncCobylaRefUi() {
    await refreshCobylaRefStatus();
    await refreshCobylaRefPreview();
}

if (tableCamCobylaRefBtn) {
    tableCamCobylaRefBtn.addEventListener('click', async () => {
        if (!cobylaRefStatusEl || !tableCamImg) return;
        if (tableCamImg.style.display === 'none' || !tableCamImg.src) {
            cobylaRefStatusEl.textContent = 'Cobyla ref: capture an image first (Latest capture)';
            log('Set Cobyla reference: need an image in Latest capture.', 'warn');
            return;
        }
        cobylaRefStatusEl.textContent = 'Cobyla ref: uploading…';
        try {
            const cap = await fetch(tableCamImg.src);
            if (!cap.ok) throw new Error('Could not read Latest capture image');
            const blob = await cap.blob();
            const res = await fetch('/api/cobyla-reference-image', {
                method: 'POST',
                headers: { 'Content-Type': 'image/png' },
                body: blob,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const err = data.detail || res.statusText || 'Upload failed';
                cobylaRefStatusEl.textContent = `Cobyla ref: ${err}`;
                log(err, 'warn');
                return;
            }
            log(data.message || 'Cobyla reference stored from Latest capture', 'info');
            await syncCobylaRefUi();
        } catch (e) {
            cobylaRefStatusEl.textContent = `Cobyla ref: ${e.message || 'failed'}`;
            log(e.message || 'Cobyla reference upload failed', 'error');
        }
    });
}

if (tableCamCobylaClearBtn) {
    tableCamCobylaClearBtn.addEventListener('click', async () => {
        try {
            const res = await fetch('/api/cobyla-reference-image', { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (res.ok) log(data.message || 'Cobyla reference cleared', 'info');
            await syncCobylaRefUi();
        } catch (e) {
            log(e.message || 'Clear failed', 'error');
        }
    });
}

if (cobylaRefSaveBtn) {
    cobylaRefSaveBtn.addEventListener('click', async () => {
        try {
            const r = await fetch(`/api/cobyla-reference-image?t=${Date.now()}`);
            if (!r.ok) {
                log('No Cobyla reference to save.', 'warn');
                return;
            }
            const blob = await r.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'cobyla-reference.png';
            a.rel = 'noopener';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
            log('Saved Cobyla reference as cobyla-reference.png', 'info');
        } catch (e) {
            log(e.message || 'Save failed', 'error');
        }
    });
}

if (cobylaRefLoadBtn && cobylaRefFileInput) {
    cobylaRefLoadBtn.addEventListener('click', () => cobylaRefFileInput.click());
    cobylaRefFileInput.addEventListener('change', async () => {
        const file = cobylaRefFileInput.files && cobylaRefFileInput.files[0];
        cobylaRefFileInput.value = '';
        if (!file || !cobylaRefStatusEl) return;
        if (!file.type.includes('png') && !file.name.toLowerCase().endsWith('.png')) {
            log('Please choose a PNG file.', 'warn');
            return;
        }
        cobylaRefStatusEl.textContent = 'Cobyla ref: uploading…';
        try {
            const buf = await file.arrayBuffer();
            const head = new Uint8Array(buf.slice(0, 8));
            const pngSig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
            if (head.length < 8 || !pngSig.every((b, i) => head[i] === b)) {
                cobylaRefStatusEl.textContent = 'Cobyla ref: not a valid PNG';
                log('File is not a valid PNG.', 'warn');
                return;
            }
            const res = await fetch('/api/cobyla-reference-image', {
                method: 'POST',
                headers: { 'Content-Type': 'image/png' },
                body: buf,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                cobylaRefStatusEl.textContent = `Cobyla ref: ${data.detail || res.statusText}`;
                log(data.detail || 'Upload failed', 'warn');
                return;
            }
            log(data.message || 'Cobyla reference loaded from file', 'info');
            await syncCobylaRefUi();
        } catch (e) {
            cobylaRefStatusEl.textContent = `Cobyla ref: ${e.message || 'failed'}`;
            log(e.message || 'Load failed', 'error');
        }
    });
}

syncCobylaRefUi();
setInterval(async () => {
    const isSet = await refreshCobylaRefStatus();
    if (!isSet && cobylaRefPreviewImg) {
        revokeCobylaRefPreviewUrl();
        cobylaRefPreviewImg.src = '';
        setCobylaRefPreviewVisible(false);
    }
}, 8000);

async function checkVideoStatus() {
    try {
        console.log(`[${new Date().toLocaleTimeString()}] Checking Video Status...`);
        const res = await fetch('/api/video-feed/status');
        if (res.ok) {
            const data = await res.json();
            if (data.connected) {
                console.log(`[${new Date().toLocaleTimeString()}] Video Status: Connected`);
                videoImg.style.display = 'block';
                videoPlaceholder.style.display = 'none';
                videoStatus.innerHTML = '● LIVE';
                videoStatus.style.color = '#10b981';
                // Force refresh with cache-busting so we don't get stuck showing an older mock SVG response.
                // Reload only if the placeholder was visible (i.e. previous state was OFFLINE).
                if (videoPlaceholder.style.display !== 'none') {
                    const fpsInput = document.getElementById('fps-input');
                    const fps = fpsInput ? fpsInput.value : 10;
                    videoImg.src = `${data.source}?fps=${fps}&t=${Date.now()}`;
                } else if (videoImg.src.indexOf('t=') === -1) {
                    // Also refresh once on connect if the src has no timestamp yet.
                    const fpsInput = document.getElementById('fps-input');
                    const fps = fpsInput ? fpsInput.value : 10;
                    videoImg.src = `${data.source}?fps=${fps}&t=${Date.now()}`;
                }
            } else {
                console.warn(`[${new Date().toLocaleTimeString()}] Video Status: Disconnected`);
                throw new Error("Disconnected");
            }
        } else {
             console.error(`[${new Date().toLocaleTimeString()}] Video Status Check Failed: HTTP ${res.status}`);
            throw new Error("API Error");
        }
    } catch (e) {
        console.error(`[${new Date().toLocaleTimeString()}] Video Error: ${e.message}`);
        videoImg.style.display = 'none';
        videoPlaceholder.style.display = 'flex';
        videoStatus.innerHTML = '● OFFLINE';
        videoStatus.style.color = '#ef4444';
    }
}

// --- Command Console (ES module `js/command-console.js`): dependency injection ---
window.__commandConsoleDeps = {
    executeSendCommand,
    sendCommand,
    checkCollision,
    log,
    runLabPoseRefresh,
    fetchLabState,
    ensureGhostForConsole,
    getTableCamExposureSeconds,
    get ghostState() {
        return store.ghostState;
    },
    render,
    getCatalogEntry: (tagId) => store.catalogMap[tagId] || null,
    getLabState: () => store.labState
};

init();

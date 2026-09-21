// Optical Digital Twin — ES module bundle (see js/bootstrap.js)
import { CANVAS_WIDTH, CANVAS_HEIGHT } from './config.js';
import { mmToPx, pxToMm } from './canvas/coordinates.js';
import { store } from './state/store.js';
import { drawPose, isBreadboardIntent } from './component-model.js';
import { isTeleopActive } from './component-state.js';
import { log } from './ui/log.js';
import {
    ALIGNMENT_SHOW_INTERSECTION_MARKERS,
    getAlignmentSnapThresholdMm,
} from './canvas/alignment-snap.js';
import {
    clearAllGuides,
    initGuides,
    migrateLocalGuidesOnce,
    updatePencilToolButtonUi,
} from './canvas/guides.js';
import {
    fetchCatalogMap,
    fetchRecipes,
    fetchStorageGridSpec,
    initApiFetchers,
} from './api/fetchers.js';
import { showErrorModal } from './ui/modals.js';
import { initSessionReconciliation } from './ui/session-reconciliation.js';
import { initLayoutConflicts } from './ui/layout-conflicts.js';
import { initRecipes, renderRecipes } from './ui/recipes.js';
import {
    fetchLaserLines,
    initLaserLinesPanel,
    initLaserLinesPanelDeps,
} from './ui/laser-lines-panel.js';
import {
    initPoseRefresh,
    initializeUnlocalizedRealInventoryPoses,
    runLabPoseRefresh,
} from './ui/pose-refresh.js';
import { showLabInitPendingGate } from './ui/lab-init-overlay.js';
import { runtimeEditableOrMessage } from './control/control-state.js';
import { getTableCamExposureSeconds } from './camera-exposure.js';
import {
    executeSendCommand,
    initCommands,
    sendCommand,
} from './api/commands.js';
import { endTeleopBeacon } from './api/teleop.js';
import { releaseSessionLeaseBeacon } from './api/session-lease.js';
import { fetchLabState, initLabState, startLabStatePolling } from './state/lab-state.js';
import { fetchBackends } from './state/backend-selection.js';
import { getClientId } from './state/client-id.js';
import { initBenchChromeBar, initBenchChromeBarInteraction } from './ui/bench-chrome-bar.js';
import { initBackendPicker } from './ui/backend-picker.js';
import { initRuntimeMode } from './ui/runtime-mode.js';
import { initWorkspaceTabs } from './ui/workspace-tabs.js';
import { initSessionNotes } from './ui/session-notes.js';
import { initOptimizationMode } from './ui/optimization-mode.js';
import { initParameterScanMode } from './ui/parameter-scan-mode.js';
import { initUpdateUI, initComponentSidebarInteraction, updateUI } from './ui/updateUI.js';
import { initCommandMatrixPanel } from './ui/command-matrix-panel.js';
import {
    initContextPanel,
    openPanel,
    placementUiLabel,
    updateContextPanel,
    updateHoldingBanner,
    updateMotorAngleLabels,
    closePanel,
} from './ui/context-panel.js';
import { checkCollision, initCanvasInteraction } from './canvas/interaction.js';
import { exportTableLayoutPng, initRender, render } from './canvas/render.js';
import { loadPlatformRegistries } from './lab-capabilities.js';
import { initControlPanel, refreshControlWorkingState } from './ui/control-panel.js';
import { initInventoryAdd } from './ui/inventory-add.js';


const _frontendBuild =
    new URL(import.meta.url).searchParams.get('v') ?? 'dev';
console.info('[cloud-labs] frontend loaded', {
    build: _frontendBuild,
    module: 'app-main.js',
    alignmentSnapThresholdMm: getAlignmentSnapThresholdMm(),
    intersectionMarkers: ALIGNMENT_SHOW_INTERSECTION_MARKERS,
});

console.log('App main module loading...');


// DOM Elements
const canvas = document.getElementById('optical-table');
const ctx = canvas.getContext('2d');
initRender();
initGuides({
    canvas,
    ctx,
    canvasWidth: CANVAS_WIDTH,
    canvasHeight: CANVAS_HEIGHT,
    mmToPx,
    pxToMm,
    render: () => render(),
    refreshControlWorkingState: () => refreshControlWorkingState(),
});
const refreshBtn = document.getElementById('refresh-btn');
const recipeList = document.getElementById('recipe-list');

// Each per-tag panel renders its own close button now (see ui/component-popup.js).
// The historical global #ctx-panel-close element no longer exists in index.html.

// Recipe UI Elements (Right Sidebar)
const recordBtn = document.getElementById('record-btn');
const recipeEditorName = document.getElementById('recipe-editor-name');
const recipeStepsContainer = document.getElementById('recipe-steps-container');
const recipeEditorSave = document.getElementById('recipe-editor-save');
const recipeEditorCancel = document.getElementById('recipe-editor-cancel');
const recIndicator = document.getElementById('rec-indicator');


// --- Module wiring ---

initApiFetchers({
    onCatalogLoaded: () => updateUI({ forceSidebar: true }),
    onRecipesLoaded: () => renderRecipes(),
});
void initRuntimeMode({
    fetchCatalogMap: () => fetchCatalogMap(),
    fetchLabState: () => fetchLabState(),
    log,
    showErrorModal,
});
initWorkspaceTabs();
initSessionNotes();
initOptimizationMode({ sendCommand, log });
initParameterScanMode({ log });

// Guides are now versioned server state: they arrive via lab-state polling
// (synced into store.guideLines) rather than localStorage.
fetchCatalogMap();
fetchLaserLines();


initSessionReconciliation({ fetchLabState: () => fetchLabState() });
initInventoryAdd({
    fetchLabState: () => fetchLabState(),
    updateUI: () => updateUI({ forceSidebar: true }),
    closePanel: (tagId) => closePanel(tagId),
});
initLayoutConflicts({ executeSendCommand: (cmd) => executeSendCommand(cmd) });
initRecipes({
    recipeList,
    recipeStepsContainer,
    recipeEditorName,
    recipeEditorSave,
    recipeEditorCancel,
    recIndicator,
    recordBtn,
});
initLaserLinesPanelDeps({
    render: () => render(),
    refreshControlWorkingState: () => refreshControlWorkingState(),
});
initPoseRefresh({
    fetchLabState: () => fetchLabState(),
});
initCommands({
    render: () => render(),
    updateContextPanel: (tagId) => updateContextPanel(tagId),
    refreshControlWorkingState: () => refreshControlWorkingState(),
});
initLabState({
    placementUiLabel,
    updateContextPanel: (tagId) => updateContextPanel(tagId),
    updateMotorAngleLabels: (tagId) => updateMotorAngleLabels(tagId),
    updateUI: () => updateUI(),
    refreshControlWorkingState: () => refreshControlWorkingState(),
});
initControlPanel({
    fetchLabState: () => fetchLabState(),
    render: () => render(),
});
initBenchChromeBar({
    // Chrome bar click → open (or focus) a panel for the chrome tag.
    // Ctrl/Cmd+click adds a panel without closing existing ones (multi-panel).
    onSelect: (tagId, opts) => {
        openPanel(tagId, opts || {});
    },
});
initBenchChromeBarInteraction();
initBackendPicker();
initUpdateUI({
    placementUiLabel,
    updateContextPanel: (tagId) => updateContextPanel(tagId),
    updateMotorAngleLabels: (tagId) => updateMotorAngleLabels(tagId),
    updateHoldingBanner: () => updateHoldingBanner(),
    render: () => render(),
    openPanel: (tagId, opts) => openPanel(tagId, opts || {}),
    fetchLabState: () => fetchLabState(),
    log,
});
initComponentSidebarInteraction();
initContextPanel({
    render: () => render(),
    checkCollision: (id, x, y, opts) => checkCollision(id, x, y, opts),
});
initCanvasInteraction({ render: () => render() });
store._teleopLivePoseRender = () => render();

// Laptop keyboards commonly expose F8 through Fn+F8. Browsers report both as
// the same `F8` key, so one handler supports full-size and compact keyboards.
let tablePngExportInProgress = false;
window.addEventListener('keydown', async (event) => {
    if (event.key !== 'F8' && event.code !== 'F8') return;
    if (event.repeat) return;
    event.preventDefault();
    if (tablePngExportInProgress) return;

    tablePngExportInProgress = true;
    try {
        const results = await Promise.all([
            exportTableLayoutPng({ scale: 4, includeText: true }),
            exportTableLayoutPng({ scale: 4, includeText: false }),
        ]);
        log(`Saved high-resolution table PNGs: ${results.map((item) => item.path).join(', ')}`);
    } catch (error) {
        console.error('High-resolution table PNG export failed', error);
        log(`Table PNG export failed: ${error && error.message ? error.message : error}`, 'error');
    } finally {
        tablePngExportInProgress = false;
    }
});


/** Initialize store.ghostState[tagId] from lab state for Command Console moves. */
function ensureGhostForConsole(tagId) {
    if (store.ghostState[tagId]) return true;
    const comp = store.labState && store.labState.components && store.labState.components[tagId];
    const dp = drawPose(comp || {});
    if (!comp || !isBreadboardIntent(comp) || (dp.x === undefined && dp.y === undefined)) return false;
    store.ghostState[tagId] = {
        x: dp.x,
        y: dp.y,
        rotation: typeof dp.rotation === 'number' ? dp.rotation : 0
    };
    return true;
}

function initAlignmentDockTools() {
    const pencil = document.getElementById('pencil-tool-btn');
    const clearBtn = document.getElementById('clear-guides-btn');
    if (pencil) {
        pencil.addEventListener('click', () => {
            const blocked = runtimeEditableOrMessage();
            if (blocked) {
                log(blocked, 'warn');
                return;
            }
            store.pencilToolActive = !store.pencilToolActive;
            updatePencilToolButtonUi();
            log(
                store.pencilToolActive
                    ? 'Pencil on: drag on empty table to draw a guide (Shift = horizontal / vertical).'
                    : 'Pencil off.',
                'info',
            );
        });
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            const blocked = runtimeEditableOrMessage();
            if (blocked) {
                log(blocked, 'warn');
                return;
            }
            void clearAllGuides();
        });
    }
    updatePencilToolButtonUi();
}

function init() {
    log("Interface loaded.");
    log(`Client id ${getClientId()}`, 'info');
    // Opaque gate before first poll — canvas / component lists stay hidden.
    showLabInitPendingGate();
    void fetchBackends()
        .then(() => {
            const p = store.coordinatorPolicy;
            if (p) {
                log(
                    `Coordinator policy: solo=${Boolean(p.solo)} `
                    + `command_lease_required=${p.command_lease_required !== false}`,
                    'info',
                );
            }
        })
        .catch((e) => console.warn('fetchBackends (policy) failed', e));
    loadPlatformRegistries()
        .then((reg) => {
            console.info('[cloud-labs] platform registries loaded', {
                tunables: Object.keys(reg.tunables || {}),
                measurables: Object.keys(reg.measurables || {}),
            });
        })
        .catch((e) => console.warn('[cloud-labs] platform registries preload failed:', e));
    fetchStorageGridSpec();
    fetchRecipes();
    // First fetch seeds runtime overlays server-side; then run the one-time
    // localStorage→server guide migration + backfill of existing commits, and
    // refresh once more so the canvas + control flags reflect the migrated set.
    fetchLabState()
        .then(() => initializeUnlocalizedRealInventoryPoses())
        .then(() => migrateLocalGuidesOnce())
        .then(() => {
            fetchLabState();
            return refreshControlWorkingState();
        })
        .catch((e) => console.warn('Initial line migration sequence failed', e));
    startLabStatePolling();
    refreshBtn.addEventListener('click', async () => {
        try {
            await runLabPoseRefresh();
        } catch (e) {
            console.error("Refresh pose failed:", e);
            log(`Refresh pose failed: ${e.message || e}`, "error");
            showErrorModal("Refresh Pose Failed", e.message || String(e));
        }
    });

    initAlignmentDockTools();
    initLaserLinesPanel();
    initCommandMatrixPanel();
    // Phase 9c removed the lab-wide LIVE FEED pane.
    // Phase 9d removed the table-cam dock; cameras live in the component panel.

    // Best-effort END_TELEOP + session-lease release on browser unload.
    const fireTeleopEndBeacons = () => {
        const comps = store.labState && store.labState.components;
        if (!comps) return;
        Object.entries(comps).forEach(([tagId, comp]) => {
            if (comp && isTeleopActive(comp)) {
                endTeleopBeacon(tagId);
            }
        });
    };
    const fireSessionLeaseRelease = () => {
        releaseSessionLeaseBeacon();
    };
    window.addEventListener('pagehide', () => {
        fireTeleopEndBeacons();
        fireSessionLeaseRelease();
    });
    window.addEventListener('beforeunload', () => {
        fireTeleopEndBeacons();
        fireSessionLeaseRelease();
    });
}

// --- Command Console (ES module `js/command-console.js`): dependency injection ---
window.__commandConsoleDeps = {
    executeSendCommand,
    sendCommand,
    checkCollision,
    log,
    runLabPoseRefresh,
    fetchLabState,
    fetchCatalogMap,
    ensureGhostForConsole,
    getTableCamExposureSeconds,
    get ghostState() {
        return store.ghostState;
    },
    render,
    getCatalogEntry: (tagId) => store.catalogMap[tagId] || null,
    getCatalogMap: () => store.catalogMap,
    getLabState: () => store.labState
};

// command-console.js often evaluates before the backend gate finishes; kick it now.
import('./command-console.js')
    .then((mod) => {
        if (mod && typeof mod.startCommandConsole === 'function') {
            mod.startCommandConsole(window.__commandConsoleDeps);
        }
    })
    .catch((err) => {
        console.warn('[Command Console] failed to start from app-main:', err);
    });

init();

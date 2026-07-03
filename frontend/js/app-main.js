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
    fetchStrategies,
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
import { initPoseRefresh, runLabPoseRefresh } from './ui/pose-refresh.js';
import { runtimeEditableOrMessage } from './control/control-state.js';
import { getTableCamExposureSeconds } from './camera-exposure.js';
import {
    executeSendCommand,
    initCommands,
    sendCommand,
} from './api/commands.js';
import { endTeleopBeacon } from './api/teleop.js';
import { fetchLabState, initLabState, startLabStatePolling } from './state/lab-state.js';
import { initBenchChromeBar, initBenchChromeBarInteraction } from './ui/bench-chrome-bar.js';
import { initRuntimeMode } from './ui/runtime-mode.js';
import { initWorkspaceTabs } from './ui/workspace-tabs.js';
import { initOptimizationMode } from './ui/optimization-mode.js';
import { initUpdateUI, initComponentSidebarInteraction, updateUI } from './ui/updateUI.js';
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
import { initRender, render } from './canvas/render.js';
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
initOptimizationMode({ sendCommand, log });

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
initUpdateUI({
    placementUiLabel,
    updateContextPanel: (tagId) => updateContextPanel(tagId),
    updateMotorAngleLabels: (tagId) => updateMotorAngleLabels(tagId),
    updateHoldingBanner: () => updateHoldingBanner(),
    render: () => render(),
    openPanel: (tagId, opts) => openPanel(tagId, opts || {}),
});
initComponentSidebarInteraction();
initContextPanel({
    render: () => render(),
    checkCollision: (id, x, y, opts) => checkCollision(id, x, y, opts),
});
initCanvasInteraction({ render: () => render() });
store._teleopLivePoseRender = () => render();


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
    loadPlatformRegistries()
        .then((reg) => {
            console.info('[cloud-labs] platform registries loaded', {
                tunables: Object.keys(reg.tunables || {}),
                measurables: Object.keys(reg.measurables || {}),
            });
        })
        .catch((e) => console.warn('[cloud-labs] platform registries preload failed:', e));
    fetchStorageGridSpec();
    fetchStrategies();
    fetchRecipes();
    // First fetch seeds runtime overlays server-side; then run the one-time
    // localStorage→server guide migration + backfill of existing commits, and
    // refresh once more so the canvas + control flags reflect the migrated set.
    fetchLabState()
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
    // Phase 9c removed the lab-wide LIVE FEED pane.
    // Phase 9d removed the table-cam dock; cameras live in the component panel.

    // Best-effort END_TELEOP on browser unload. Fires sendBeacon for every
    // tag with an active TeleOp session so leases do not linger after tab close.
    const fireTeleopEndBeacons = () => {
        const comps = store.labState && store.labState.components;
        if (!comps) return;
        Object.entries(comps).forEach(([tagId, comp]) => {
            if (comp && isTeleopActive(comp)) {
                endTeleopBeacon(tagId);
            }
        });
    };
    window.addEventListener('pagehide', fireTeleopEndBeacons);
    window.addEventListener('beforeunload', fireTeleopEndBeacons);
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

// Optical Digital Twin — ES module bundle (see js/bootstrap.js)
import { CANVAS_WIDTH, CANVAS_HEIGHT } from './config.js';
import { mmToPx, pxToMm } from './canvas/coordinates.js';
import { store } from './state/store.js';
import { drawPose, isBreadboardIntent } from './component-model.js';
import { log } from './ui/log.js';
import {
    ALIGNMENT_SHOW_INTERSECTION_MARKERS,
    getAlignmentSnapThresholdMm,
} from './canvas/alignment-snap.js';
import {
    initGuides,
    loadGuideLinesFromStorage,
    saveGuideLinesToStorage,
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
import { getTableCamExposureSeconds } from './camera-exposure.js';
import {
    executeSendCommand,
    initCommands,
    sendCommand,
} from './api/commands.js';
import { endTeleopBeacon } from './api/teleop.js';
import { fetchLabState, initLabState, startLabStatePolling } from './state/lab-state.js';
import { initBenchChromeBar } from './ui/bench-chrome-bar.js';
import { initUpdateUI, updateUI } from './ui/updateUI.js';
import {
    clearSelectionAndHideContextPanel,
    initContextPanel,
    placementUiLabel,
    updateContextPanel,
    updateHoldingBanner,
    updateMotorAngleLabels,
} from './ui/context-panel.js';
import { checkCollision, initCanvasInteraction } from './canvas/interaction.js';
import { initRender, render } from './canvas/render.js';


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
});
const refreshBtn = document.getElementById('refresh-btn');
const saveStateBtn = document.getElementById('save-state-btn');
const loadStateBtn = document.getElementById('load-state-btn');
const recipeList = document.getElementById('recipe-list');

const ctxPanelCloseBtn = document.getElementById('ctx-panel-close');

// Recipe UI Elements (Right Sidebar)
const recordBtn = document.getElementById('record-btn');
const recipeEditorName = document.getElementById('recipe-editor-name');
const recipeStepsContainer = document.getElementById('recipe-steps-container');
const recipeEditorSave = document.getElementById('recipe-editor-save');
const recipeEditorCancel = document.getElementById('recipe-editor-cancel');
const recIndicator = document.getElementById('rec-indicator');


// --- Module wiring ---

initApiFetchers({
    onCatalogLoaded: () => updateUI(),
    onRecipesLoaded: () => renderRecipes(),
});

// Load guides before first render so junction scan includes pencil lines.
loadGuideLinesFromStorage();
fetchCatalogMap();
fetchLaserLines();


initSessionReconciliation({ fetchLabState: () => fetchLabState() });
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
initLaserLinesPanelDeps({ render: () => render() });
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
});
initBenchChromeBar({
    onSelect: (tagId) => {
        updateContextPanel(tagId);
        render();
    },
});
initUpdateUI({
    placementUiLabel,
    updateContextPanel: (tagId) => updateContextPanel(tagId),
    updateMotorAngleLabels: (tagId) => updateMotorAngleLabels(tagId),
    updateHoldingBanner: () => updateHoldingBanner(),
    render: () => render(),
});
initContextPanel({
    render: () => render(),
    checkCollision: (id, x, y, opts) => checkCollision(id, x, y, opts),
});
initCanvasInteraction({ render: () => render() });


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
    loadGuideLinesFromStorage();
    const pencil = document.getElementById('pencil-tool-btn');
    const clearBtn = document.getElementById('clear-guides-btn');
    if (pencil) {
        pencil.addEventListener('click', () => {
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
            store.guideLines = [];
            saveGuideLinesToStorage();
            log('Cleared drawn alignment guides.', 'info');
            render();
        });
    }
    updatePencilToolButtonUi();
}


function init() {
    log("Interface loaded.");
    fetchStorageGridSpec();
    fetchStrategies();
    fetchRecipes();
    fetchLabState();
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
    
    initAlignmentDockTools();
    initLaserLinesPanel();
    // Phase 9c removed the lab-wide LIVE FEED pane.
    // Phase 9d removed the table-cam dock; cameras live in the component panel.

    // Phase 8b: best-effort END_TELEOP on browser unload. Walks
    // ``store.labState.components`` (last known snapshot) and fires a
    // sendBeacon for every tag whose ``tunables.teleop_active`` was
    // True. Without this, a closed tab would leave the lease dangling
    // until the server's stale-lease sweeper (TTL ~3 s by default) ran;
    // the beacon brings that down to "before the page is gone".
    //
    // We listen on BOTH pagehide (Safari-friendly, fires on bfcache
    // restore too) AND beforeunload so the path is covered across
    // browsers. The handler must stay synchronous-ish and tiny --
    // sendBeacon hands the request to the OS and returns immediately.
    const fireTeleopEndBeacons = () => {
        const comps = store.labState && store.labState.components;
        if (!comps) return;
        Object.entries(comps).forEach(([tagId, comp]) => {
            if (comp && comp.tunables && comp.tunables.teleop_active) {
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

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
    initGuides,
    loadGuideLinesFromStorage,
    replaceGuideLines,
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
import { showConfirmationModal, showErrorModal } from './ui/modals.js';
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
import { initRuntimeMode, switchRuntimeMode } from './ui/runtime-mode.js';
import { initWorkspaceTabs } from './ui/workspace-tabs.js';
import { initUpdateUI, updateUI } from './ui/updateUI.js';
import {
    initContextPanel,
    openPanel,
    placementUiLabel,
    updateContextPanel,
    updateHoldingBanner,
    updateMotorAngleLabels,
} from './ui/context-panel.js';
import { checkCollision, initCanvasInteraction } from './canvas/interaction.js';
import { initRender, render } from './canvas/render.js';
import { loadPlatformRegistries } from './lab-capabilities.js';


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
const savedStateSelect = document.getElementById('saved-state-select');
const resetOriginalStateBtn = document.getElementById('reset-original-state-btn');
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
    onCatalogLoaded: () => updateUI(),
    onRecipesLoaded: () => renderRecipes(),
});
void initRuntimeMode({
    fetchCatalogMap: () => fetchCatalogMap(),
    fetchLabState: () => fetchLabState(),
    log,
    showErrorModal,
});
initWorkspaceTabs();

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
    // Chrome bar click → open (or focus) a panel for the chrome tag.
    // Ctrl/Cmd+click adds a panel without closing existing ones (multi-panel).
    onSelect: (tagId, opts) => {
        openPanel(tagId, opts || {});
    },
});
initUpdateUI({
    placementUiLabel,
    updateContextPanel: (tagId) => updateContextPanel(tagId),
    updateMotorAngleLabels: (tagId) => updateMotorAngleLabels(tagId),
    updateHoldingBanner: () => updateHoldingBanner(),
    render: () => render(),
    openPanel: (tagId, opts) => openPanel(tagId, opts || {}),
});
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

async function refreshSavedStateOptions(preferredName = null) {
    if (!savedStateSelect) return;
    const response = await fetch('/api/states');
    const names = response.ok ? await response.json() : [];
    savedStateSelect.innerHTML = '';
    if (!Array.isArray(names) || names.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No saved snapshots';
        savedStateSelect.appendChild(option);
        return;
    }
    names.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name === 'original_demo' ? 'Original Demo' : name;
        savedStateSelect.appendChild(option);
    });
    const selected =
        preferredName && names.includes(preferredName)
            ? preferredName
            : names.includes('original_demo')
              ? 'original_demo'
              : names[0];
    savedStateSelect.value = selected;
}

async function restoreSavedState(name) {
    if (!name) throw new Error('Choose a saved snapshot first.');
    const res = await fetch('/api/states/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Restore failed');
    if (data.has_ui_state) {
        replaceGuideLines(data.ui_state?.alignment_guides || []);
    }
    store.forceGhostSync = true;
    await fetchLabState();
    log(`Restored workspace snapshot: ${data.name || name}`, 'info');
    return data;
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
    fetchLabState();
    refreshSavedStateOptions().catch((e) =>
        console.warn('Saved snapshot list failed:', e),
    );
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

    // Each per-tag panel renders its own close button now; the legacy
    // global #ctx-panel-close listener that used to live here is gone.

    if (saveStateBtn) {
        saveStateBtn.addEventListener('click', async () => {
            try {
                const defaultName = `state_${new Date().toISOString().replace(/[:.]/g, '-')}`;
                const name = prompt("Save current lab state as:", defaultName);
                if (!name) return;

                const res = await fetch('/api/states/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name,
                        ui_state: {
                            alignment_guides: store.guideLines || [],
                        },
                    }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Save failed');

                await refreshSavedStateOptions(data.name || name);
                log(`Saved workspace snapshot: ${data.name || name}`, "info");
            } catch (e) {
                console.error("Save state failed:", e);
                showErrorModal("Save Failed", e.message || String(e));
            }
        });
    }

    if (loadStateBtn) {
        loadStateBtn.addEventListener('click', async () => {
            try {
                await restoreSavedState(savedStateSelect?.value);
            } catch (e) {
                console.error("Restore snapshot failed:", e);
                showErrorModal("Restore Failed", e.message || String(e));
            }
        });
    }

    if (resetOriginalStateBtn) {
        const resetOriginalDemo = async () => {
            try {
                if (store.runtimeMode?.active_mode === 'mujoco') {
                    const switched = await switchRuntimeMode('mock');
                    if (!switched || switched.active_mode !== 'mock') return;
                }
                await restoreSavedState('original_demo');
                if (savedStateSelect) savedStateSelect.value = 'original_demo';
            } catch (e) {
                console.error('Original demo restore failed:', e);
                showErrorModal('Original Demo Restore Failed', e.message || String(e));
            }
        };

        resetOriginalStateBtn.addEventListener('click', () => {
            if (store.runtimeMode?.active_mode !== 'mujoco') {
                void resetOriginalDemo();
                return;
            }
            showConfirmationModal(
                'Resetting will close the MuJoCo viewer, discard the current simulated positions, switch to <strong>Mock UI</strong>, and restore <strong>Original Demo</strong>. Continue?',
                () => void resetOriginalDemo(),
            );
        });
    }
    
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

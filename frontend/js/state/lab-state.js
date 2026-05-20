/**
 * Lab-state polling.
 *
 * `fetchLabState` is the single source of truth for "the world according to the backend": it
 * pulls `/api/lab-state`, mirrors it into `store.labState`, and reconciles three things that the
 * raw payload does not carry:
 *   1. **Ghost state** (`store.ghostState[tag]`) — the user-editable target pose that the canvas
 *      draws. We rebuild it from nominal/measured pose whenever the system status implies a
 *      "fresh start" (e.g. command just finished, OPTIMIZING tick, HOLDING transition).
 *   2. **Context panel state snapshots** — re-render the side panel on placement / holding edges
 *      but ignore the transient BUSY phase so the panel doesn't flicker mid-command.
 *   3. **Auxiliary cross-feature side effects** — optimization overlay, table-cam highlight,
 *      session reconciliation, layout conflict refresh.
 *
 * The function is intentionally long because it encodes a tricky finite-state-machine over the
 * sequence (IDLE → BUSY → HOLDING/IDLE → OPTIMIZING …). Splitting it further risks breaking the
 * subtle invariants around `previousSystemStatus` and `contextPanelStatusSnapshot`.
 */
import { store } from './store.js';
import { log } from '../ui/log.js';
import { POLLING_INTERVAL } from '../config.js';
import {
    getHolding,
    isBreadboardIntent,
    isHeldTag,
    isHoldingState,
    isOnTableComponent,
    measPose,
    nominalPose,
} from '../component-model.js';
import { showErrorModal } from '../ui/modals.js';
import { maybeTriggerSessionReconciliation } from '../ui/session-reconciliation.js';
import { fetchLayoutConflicts } from '../api/fetchers.js';
import {
    updateLayoutConflictModal,
    updateLayoutWarningBanner,
} from '../ui/layout-conflicts.js';
import {
    clearTableCamError,
    restoreTableCamPanelVisuals,
    updateTableCamMockPreviewChrome,
} from '../table-cam/panel.js';

let _deps = {
    placementUiLabel: () => 'PLACED',
    updateContextPanel: () => {},
    updateMotorAngleLabels: () => {},
    updateUI: () => {},
};

let _pollTimerId = null;

/**
 * @param {{
 *   placementUiLabel: (comp: any) => string,
 *   updateContextPanel: (tagId: string) => void,
 *   updateMotorAngleLabels: (tagId: string) => void,
 *   updateUI: () => void,
 * }} deps
 */
export function initLabState(deps) {
    _deps = { ..._deps, ...deps };
}

/** Start the background poll. Returns the timer id. Idempotent. */
export function startLabStatePolling() {
    if (_pollTimerId != null) return _pollTimerId;
    _pollTimerId = setInterval(fetchLabState, POLLING_INTERVAL);
    return _pollTimerId;
}

export async function fetchLabState() {
    try {
        console.log(`[${new Date().toLocaleTimeString()}] Requesting Lab State...`);
        const response = await fetch('/api/lab-state');
        if (!response.ok) {
            // Surface the backend's error detail when available — it usually carries a
            // human-readable reason (e.g. "controller in safe mode").
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

        // If a part the user was dragging from storage has since landed on the breadboard, clear the
        // drag-from-storage handle so we don't double-confirm a PLACE_FROM_STORAGE later.
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

        // Recovery: a previously displayed connection-error modal becomes stale once we successfully
        // fetched state again — remove it so the operator isn't blocked.
        const existingError = document.getElementById('error-modal');
        if (existingError) existingError.remove();

        // Ghost state reconciliation. The ghost is what the canvas draws; we sync it from the
        // backend's nominal/measured pose when:
        //   1. Force Sync was requested (Refresh button).
        //   2. The system status transitioned BUSY/OPTIMIZING → IDLE (command finished).
        //   3. Initial load (handled by `!store.ghostState[name]` check).
        if (store.labState.components) {
            const justFinishedCommand = (store.previousSystemStatus !== 'IDLE' && store.labState.system_status === 'IDLE');
            const shouldSync = store.forceGhostSync || justFinishedCommand;

            if (shouldSync) {
                log('Syncing ghost state with lab state...', 'info');
            }

            Object.entries(store.labState.components).forEach(([name, comp]) => {
                if (isOnTableComponent(comp)) {
                    const np = nominalPose(comp);
                    const mp = measPose(comp);
                    // First load: initialize from nominal if available, else from measured pose.
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
                            const ctxX = document.getElementById('ctx-x');
                            const ctxY = document.getElementById('ctx-y');
                            const ctxRot = document.getElementById('ctx-rot');
                            if (ctxX) ctxX.value = store.ghostState[name].x.toFixed(1);
                            if (ctxY) ctxY.value = store.ghostState[name].y.toFixed(1);
                            if (ctxRot) ctxRot.value = store.ghostState[name].rotation.toFixed(1);
                        }
                    }
                    // HOLDING: only the held tag's ghost follows live nominal_pose (incl. z) —
                    // every other component stays on the user's last intent.
                    //
                    // IMPORTANT: we deliberately do NOT overwrite the ctx-x/y/rot/z input fields
                    // here. Those represent the operator's *intent* for the next HOVER /
                    // PLACE_FROM_HOVER and must stay editable. Their initial values are set once
                    // by `renderInAirControlsForContext` when the HOLDING panel is rebuilt.
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
                                const ctxX = document.getElementById('ctx-x');
                                const ctxY = document.getElementById('ctx-y');
                                const ctxRot = document.getElementById('ctx-rot');
                                if (ctxX) ctxX.value = store.ghostState[name].x.toFixed(1);
                                if (ctxY) ctxY.value = store.ghostState[name].y.toFixed(1);
                                if (ctxRot) ctxRot.value = store.ghostState[name].rotation.toFixed(1);
                            }
                        }
                    }
                }
            });

            if (shouldSync) store.forceGhostSync = false;
        }

        // Rebuild context panel when EITHER the selected component's placement label OR the
        // top-level (system_status, holding) snapshot changes. The latter is what flips
        // IDLE ↔ HOLDING so the Pick / Hover / Place buttons appear/disappear without the user
        // having to re-click the sidebar card. Transient BUSY states are ignored so the panel
        // doesn't briefly revert mid-command — the pending overlay on canvas signals "in flight".
        const selCtx = store.selectedComponent;
        const hldCtx = getHolding(store.labState);
        const rawStatus = store.labState.system_status || 'IDLE';
        const statusKey = `${rawStatus}|${hldCtx.tag_id || ''}|${hldCtx.requires_operator_confirm ? '1' : '0'}`;
        if (selCtx && store.labState.components && store.labState.components[selCtx]) {
            const compCtx = store.labState.components[selCtx];
            const stCtx = _deps.placementUiLabel(compCtx);
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
                _deps.updateContextPanel(selCtx);
                _deps.updateMotorAngleLabels(selCtx);
            }
        }
        // Only snapshot stable states so a transient BUSY in-between doesn't "use up" the real
        // transition (IDLE → BUSY → HOLDING should still rebuild once on the HOLDING edge).
        if (rawStatus !== 'BUSY') {
            store.contextPanelStatusSnapshot = statusKey;
        }

        store.previousSystemStatus = store.labState.system_status;

        // Clear in-flight overlays once the system has settled into any stable (non-BUSY,
        // non-OPTIMIZING) state. PICK_COMPONENT and HOVER land in HOLDING (not IDLE), so gating
        // this on IDLE only would leave the amber/purple "PICKING UP..." / "HOVERING..." label
        // forever and prevent render() from swapping in the steady-state "HOLDING" overlay.
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
                log('Optimization sequence complete.', 'info');

                const optOverlay = document.getElementById('optimization-overlay');
                if (optOverlay) optOverlay.style.display = 'none';
                store.isOptimizingFeedActive = false;

                // Revert table-cam preview to latest capture vs live MJPEG vs placeholder.
                const tableCamPreview = document.getElementById('table-cam-preview');
                if (tableCamPreview) {
                    tableCamPreview.style.border = '1px solid var(--border-color)';
                    tableCamPreview.style.backgroundColor = '#0f1115';
                    tableCamPreview.style.boxShadow = '';
                }

                restoreTableCamPanelVisuals();
                updateTableCamMockPreviewChrome();
            }
        } else if (store.labState.system_status === 'OPTIMIZING') {
            store.isOptimizing = true;

            const optOverlay = document.getElementById('optimization-overlay');
            const optStepText = document.getElementById('optimization-step-text');
            if (optOverlay && optStepText) {
                optOverlay.style.display = 'flex';
                const runBit = store.labState.optimization_run_dir
                    ? ` · ${store.labState.optimization_run_dir}`
                    : '';
                optStepText.innerText = `OPTIMIZING (Step ${store.labState.optimization_step || 0})${runBit}`;
            }

            // Highlight the table-cam preview while optimizing — visual cue that the optimizer
            // owns the camera. The actual feed swap happens below.
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

                if (tableCamImg) {
                    // Drop the old captured image immediately so the user sees the new feed switch.
                    tableCamImg.src = '';
                    tableCamImg.style.display = 'none';
                    console.log('[UI] switching table-cam to optimization-feed stream...');
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
                    clearTableCamError();
                }
            }

            // Mock-only: synthetic beam-intensity values so the optimization plot has something
            // to draw before the real lab metric exists.
            if (store.labState.lab_mode === 'MOCK' && Math.random() > 0.5) {
                store.optimizationData.push({
                    step: store.optimizationData.length,
                    value: Math.min(1.0, 0.2 + store.optimizationData.length * 0.05 + Math.random() * 0.1),
                });
            }
        }

        if (store.labState.system_status === 'IDLE') {
            void maybeTriggerSessionReconciliation();
        }

        fetchLayoutConflicts()
            .then(() => {
                updateLayoutWarningBanner();
                updateLayoutConflictModal();
            })
            .catch(() => {});

        _deps.updateUI();
    } catch (error) {
        console.error('Failed to fetch lab state:', error);
        const statusBadge = document.getElementById('system-status-badge');
        if (statusBadge) {
            statusBadge.innerHTML = `<span class="status-dot error"></span> OFFLINE`;
        }

        showErrorModal('Connection Failed', error.message);

        // Inventory: replace the spinner with a retry block so the user has a path forward.
        const componentList = document.getElementById('component-list');
        if (componentList) {
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
}

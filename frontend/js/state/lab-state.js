/**
 * Lab-state polling.
 *
 * `fetchLabState` is the single source of truth for "the world according to the backend": it
 * pulls `/api/lab-state`, mirrors it into `store.labState`, and reconciles three things that the
 * raw payload does not carry:
 *   1. **Ghost state** (`store.ghostState[tag]`) — the user-editable target pose that the canvas
 *      draws. We rebuild it from `tunables.nominal_pose` (canvas-truth model — see
 *      `universal_component_architecture.md` §7-8) whenever the system status implies a
 *      "fresh start" (e.g. command just finished, OPTIMIZING tick, HOLDING transition).
 *      `drawPose()` falls back to `measurables.pose` defensively for legacy state files.
 *   2. **Context panel state snapshots** — re-render the side panel on placement / holding edges
 *      but ignore the transient BUSY phase so the panel doesn't flicker mid-command.
 *   3. **Auxiliary cross-feature side effects** — optimization preview overlay,
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
    drawPose,
    getHolding,
    isBreadboardIntent,
    isHeldTag,
    isHoldingState,
    isOnTableComponent,
    shouldRenderOnCanvas,
} from '../component-model.js';
import { isTeleopReady, componentDataSnapshot } from '../component-state.js';
import { syncTeleopLivePosePolls } from '../teleop-session.js';
import { syncOptimizeLivePosePolls, stopOptimizeTelemetryWatch, isSystemOptimizing } from '../optimize-session.js';
import { clearOptimizeReadouts } from '../optimize-live-readout.js';
import {
    syncLabReferenceDisplay,
    finalizeOptimizeSession,
} from '../ui/optimization-sidebar.js';
import { showErrorModal } from '../ui/modals.js';
import { maybeTriggerSessionReconciliation } from '../ui/session-reconciliation.js';
import { fetchLayoutConflicts } from '../api/fetchers.js';
import {
    updateLayoutConflictModal,
    updateLayoutWarningBanner,
} from '../ui/layout-conflicts.js';
import { syncMotorActionStatuses } from '../ui/motor-action-ui.js';
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
        const prevStatus = store.previousSystemStatus;
        const curStatus = store.labState.system_status;
        syncTeleopLivePosePolls();
        console.log(`[${new Date().toLocaleTimeString()}] Received Lab State successfully.`);

        const teleopReadyNow = new Set();
        if (store.labState.components) {
            Object.entries(store.labState.components).forEach(([name, comp]) => {
                if (isTeleopReady(comp)) teleopReadyNow.add(name);
            });
        }

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
                if (shouldRenderOnCanvas(name, comp)) {
                    const justLeftTeleop =
                        store.previousTeleopReadyTags.has(name) && !isTeleopReady(comp);
                    if (isTeleopReady(comp)) return;
                    const dp = drawPose(comp);
                    const hasPose = dp && Object.keys(dp).length > 0;
                    // First load: initialize from drawPose (intent first, measured fallback).
                    if (!store.ghostState[name]) {
                        store.ghostState[name] = { ...dp };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = 0;
                        }
                    }
                    else if (
                        isHoldingState(store.labState) &&
                        isHeldTag(name, store.labState) &&
                        !store.isDragging &&
                        hasPose
                    ) {
                        store.ghostState[name] = { ...dp };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = 0;
                        }
                    }
                    else if (shouldSync && !store.isDragging) {
                        store.ghostState[name] = { ...dp };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = 0;
                        }
                    }
                    else if (justLeftTeleop && hasPose && !store.isDragging) {
                        store.ghostState[name] = { ...dp };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = 0;
                        }
                    }
                }
            });

            if (shouldSync) store.forceGhostSync = false;
        }

        // Rebuild EVERY open panel when its component's placement label OR the
        // top-level (system_status, holding) snapshot changes. The latter is
        // what flips IDLE ↔ HOLDING so the Pick / Hover / Place buttons
        // appear/disappear without the operator re-clicking the sidebar card.
        // Transient BUSY / OPTIMIZING states are ignored so panels don't briefly revert
        // mid-command — the pending overlay on canvas signals "in flight".
        //
        // Multi-panel: each open tag has its own snapshot bag in
        // ``store.contextPanelSnapshots`` so panel A's rebuild doesn't
        // invalidate panel B's change-detection.
        const hldCtx = getHolding(store.labState);
        const rawStatus = store.labState.system_status || 'IDLE';
        const statusKey = `${rawStatus}|${hldCtx.tag_id || ''}|${hldCtx.requires_operator_confirm ? '1' : '0'}`;
        store.openPanels.forEach((tag) => {
            if (!store.labState.components || !store.labState.components[tag]) return;
            const compCtx = store.labState.components[tag];
            const stCtx = _deps.placementUiLabel(compCtx);
            const snap = store.contextPanelSnapshots.get(tag) || {};
            const placementChanged =
                snap.state != null && snap.state !== stCtx;
            const statusChanged =
                snap.status != null &&
                snap.status !== statusKey &&
                rawStatus !== 'BUSY' &&
                rawStatus !== 'OPTIMIZING';
            const dataKey = componentDataSnapshot(compCtx);
            const dataChanged =
                snap.data != null && dataKey != null && snap.data !== dataKey;
            const optimizingThisTag =
                store.optimizeActiveTarget === tag
                && (isSystemOptimizing(rawStatus) || store.pendingCommands.has(tag));
            const skipDataRebuild = dataChanged && optimizingThisTag;
            if ((placementChanged || statusChanged || dataChanged) && !skipDataRebuild) {
                if (placementChanged && isOnTableComponent(compCtx) && !store.isDragging) {
                    const dp = drawPose(compCtx);
                    store.ghostState[tag] = { ...dp };
                    if (typeof store.ghostState[tag].rotation !== 'number') {
                        store.ghostState[tag].rotation = 0;
                    }
                }
                _deps.updateContextPanel(tag);
                _deps.updateMotorAngleLabels(tag);
                // updateContextPanel rebuilds the panel DOM and writes a full
                // fresh snapshot for ``tag``; no extra bookkeeping needed.
            }
        });

        // Clear in-flight overlays once the system has settled — but preserve
        // pending state for OPTIMIZE during the IDLE gap before the backend
        // enters BUSY (async command accept).
        const stableStatus = curStatus === 'IDLE' || curStatus === 'HOLDING';
        const optimizeStartupGap =
            store.optimizeActiveTarget
            && curStatus === 'IDLE'
            && !isSystemOptimizing(prevStatus);
        if (stableStatus && !optimizeStartupGap) {
            store.pendingCommands.clear();
            store.pendingActions.clear();
            store.pendingInAirPose = {};
        }
        syncMotorActionStatuses();
        // Per-tag snapshots are now written by ``updateContextPanel(tag)`` at
        // mount/rebuild time (see ``ui/context-panel.js``). The legacy global
        // ``contextPanelStatusSnapshot`` advance is therefore unnecessary —
        // each panel's snapshot is initialized when its panel mounts and
        // refreshed when its panel rebuilds.

        store.previousSystemStatus = curStatus;
        store.previousTeleopReadyTags = teleopReadyNow;
        syncOptimizeLivePosePolls();
        syncLabReferenceDisplay();

        const optimizeJustFinished =
            isSystemOptimizing(prevStatus)
            && curStatus === 'IDLE'
            && store.optimizeActiveTarget;
        if (optimizeJustFinished) {
            finalizeOptimizeSession();
            store.optimizeActiveTarget = null;
            store.optimizeActiveSensor = null;
            store.optimizeRunningStrategy = null;
            stopOptimizeTelemetryWatch();
            clearOptimizeReadouts();
        } else if (curStatus === 'IDLE' && !store.optimizeActiveTarget && !store.optimizeSession) {
            stopOptimizeTelemetryWatch();
        }

        // One-shot boot restore: first stable IDLE after page load (not after OPTIMIZE).
        if (
            curStatus === 'IDLE'
            && !optimizeStartupGap
            && !store.optimizeActiveTarget
            && !store.optimizeSession
        ) {
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

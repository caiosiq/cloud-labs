/**
 * Lab-state polling — **Tier C overview only** (Edge Contract live plane).
 *
 * ``GET /api/lab-state`` is the layout / lease / badge overview for Twin:
 * component presence, nominal poses for the canvas, system_status, session lease.
 * It is **not** the live science plane:
 *   - Tier A TeleOp samples → WS after ``START_TELEOP``
 *   - Tier B camera wire → JPEG/MJPEG after ``START_LIVE_FEED``
 *   - Latched observations → ``RECORD_MEASURABLES`` / ``EVAL_KERNEL`` (``epoch_ms``)
 *
 * Do not hunt lasers or sync closed-loop science from this poll. Prefer armed
 * capability channels or latched primitives (see ``docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md``).
 *
 * `fetchLabState` still mirrors the overview into `store.labState` and reconciles:
 *   1. **Ghost state** (`store.ghostState[tag]`) — user-editable / optimistic
 *      commanded pose. Synced from `tunables.nominal_pose` on IDLE edges and
 *      explicit Refresh — **not** immediately after a command is accepted, so
 *      the ghost stays at the destination while the solid (committed) pose
 *      catches up when the move finishes.
 *   2. **Context panel state snapshots** — placement / holding edges; skip BUSY flicker.
 *   3. **Auxiliary side effects** — optimization overlay, session reconciliation,
 *      layout conflict refresh, teleop poll sync.
 *
 * The function is intentionally long (IDLE → BUSY → HOLDING/IDLE → OPTIMIZING FSM).
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
import { showErrorModal } from '../ui/modals.js';
import { maybeTriggerSessionReconciliation } from '../ui/session-reconciliation.js';
import { updateOptimizationModeUi } from '../ui/optimization-mode.js';
import { fetchLayoutConflicts } from '../api/fetchers.js';
import {
    updateLayoutConflictModal,
    updateLayoutWarningBanner,
} from '../ui/layout-conflicts.js';
import { syncMotorActionStatuses } from '../ui/motor-action-ui.js';
import { syncGuidesFromLabState } from '../canvas/guides.js';
import { syncLaserLinesFromLabState } from '../ui/laser-lines-panel.js';
import { refreshSessionLeaseBanner } from '../control/control-state.js';
import { withBackendQuery, ensureBackendSelected } from './backend-selection.js';
import { updateLabInitOverlay } from '../ui/lab-init-overlay.js';
const OPTIMIZING_POLL_MS = 100;
let _pollTimerId = null;
let _pollIntervalMs = POLLING_INTERVAL;
/** Last lab-init key logged to the console (status-change only). */
let _lastLabInitKey = null;

/**
 * Compose lab-init view: prefer coordinator ``lab_initialization``, else derive.
 * @param {object | null | undefined} labState
 */
export function deriveLabInit(labState) {
    if (!labState || typeof labState !== 'object') {
        return {
            ready: false,
            phase: 'starting',
            runtime_sync: 'missing',
            errors: [],
        };
    }
    const server = labState.lab_initialization;
    if (server && typeof server === 'object' && server.phase != null) {
        return {
            ready: !!server.ready,
            phase: String(server.phase),
            runtime_sync: String(server.runtime_sync || 'missing'),
            errors: Array.isArray(server.errors) ? server.errors : [],
            edge_attached: server.edge_attached,
            edge_offline: server.edge_offline,
        };
    }
    const rs = labState.runtime_sync;
    const rsStatus =
        rs && typeof rs === 'object' && rs.status != null
            ? String(rs.status)
            : 'missing';
    const edgeAttached = !!labState.edge_attached;
    const edgeOffline = !!labState.edge_offline;
    const knownSync = ['ready', 'running', 'failed', 'pending'].includes(rsStatus);
    let phase = 'starting';
    let ready = false;
    if (edgeOffline && !knownSync) {
        phase = 'waiting_for_edge';
    } else if (rsStatus === 'ready') {
        phase = 'ready';
        ready = true;
    } else if (rsStatus === 'running') {
        phase = 'measuring_inventory';
    } else if (rsStatus === 'failed') {
        phase = 'failed';
    } else if (rsStatus === 'pending') {
        phase = 'starting';
    } else if (edgeOffline || !edgeAttached) {
        phase = 'waiting_for_edge';
    } else {
        phase = 'missing_runtime_sync';
    }
    return {
        ready,
        phase,
        runtime_sync: rsStatus,
        errors: Array.isArray(rs?.errors) ? rs.errors : [],
        edge_attached: edgeAttached,
        edge_offline: edgeOffline,
    };
}

/** Whether Twin may populate canvas / component list / tunables. */
export function isLabInitReady(labState = store.labState) {
    return !!deriveLabInit(labState).ready;
}

/**
 * Log lab initialization phase when runtime_sync / edge attach changes.
 * Updates the blocking Twin overlay. Does not spam the console on every poll.
 */
function _noteLabInitChange(labState) {
    const init = deriveLabInit(labState);
    updateLabInitOverlay(init);
    const key = `${init.phase}|${init.runtime_sync}|${init.edge_attached}|${init.edge_offline}`;
    if (key === _lastLabInitKey) return;
    _lastLabInitKey = key;
    const errN = Array.isArray(init.errors) ? init.errors.length : 0;
    console.info(
        `[lab-init] phase=${init.phase} ready=${init.ready} runtime_sync=${init.runtime_sync} ` +
            `edge_attached=${init.edge_attached} edge_offline=${init.edge_offline} errors=${errN}`,
    );
    if (errN) {
        console.warn('[lab-init] errors', init.errors);
    }
}

let _deps = {
    placementUiLabel: () => 'PLACED',
    updateContextPanel: () => {},
    updateMotorAngleLabels: () => {},
    updateUI: () => {},
    refreshControlWorkingState: async () => {},
};

/**
 * Resolve which component tag is currently being optimized.
 *
 * Primary source: ``labState.optimization_target_id`` (set by the
 * backend during ``OPTIMIZE`` — Phase 9b). Fallback: scan
 * ``store.pendingActions`` for an in-flight ``OPTIMIZE`` command.
 *
 * @param {object | null | undefined} labState
 * @returns {string | null}
 */
function resolveOptimizationTargetId(labState) {
    if (!labState || typeof labState !== 'object') return null;
    const direct = labState.optimization_target_id;
    if (typeof direct === 'string' && direct) return direct;
    for (const [tag, action] of store.pendingActions) {
        if (action === 'OPTIMIZE') return tag;
    }
    return null;
}

function resetOptimizationFeedPreview() {
    const preview = document.getElementById('optimization-feed-preview');
    const img = document.getElementById('optimization-feed-img');
    const placeholder = document.getElementById('optimization-feed-placeholder');
    if (preview) {
        preview.style.border = '1px solid var(--border-color)';
        preview.style.backgroundColor = '#0f1115';
        preview.style.boxShadow = '';
    }
    if (img) {
        img.src = '';
        img.style.display = 'none';
    }
    if (placeholder) {
        placeholder.style.display = 'flex';
        placeholder.innerHTML =
            'Runs during OPTIMIZE — per-component optimization stream.';
    }
}

/** Drop canvas/UI state for tags no longer present in runtime (e.g. after untrack). */
function pruneOrphanRuntimeUiState() {
    const runtimeTags = new Set(Object.keys(store.labState?.components || {}));

    for (const tagId of Object.keys(store.ghostState)) {
        if (!runtimeTags.has(tagId)) delete store.ghostState[tagId];
    }
    for (const tagId of store.pendingCommands) {
        if (!runtimeTags.has(tagId)) {
            store.pendingCommands.delete(tagId);
            store.pendingActions.delete(tagId);
        }
    }
    for (const tagId of Object.keys(store.teleopLivePose)) {
        if (!runtimeTags.has(tagId)) delete store.teleopLivePose[tagId];
    }
    for (const tagId of Object.keys(store.teleopTarget)) {
        if (!runtimeTags.has(tagId)) delete store.teleopTarget[tagId];
    }
    for (const tagId of Object.keys(store.teleopTargetAwaitingLive)) {
        if (!runtimeTags.has(tagId)) delete store.teleopTargetAwaitingLive[tagId];
    }
    if (store.draggingComponent && !runtimeTags.has(store.draggingComponent)) {
        store.draggingComponent = null;
        store.isDragging = false;
    }
    if (store.dragFromStorageTag && !runtimeTags.has(store.dragFromStorageTag)) {
        store.dragFromStorageTag = null;
        store.dragFromStorageStartPose = null;
    }
}

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

function syncLabStatePollingInterval() {
    const want =
        store.labState?.system_status === 'OPTIMIZING' ? OPTIMIZING_POLL_MS : POLLING_INTERVAL;
    if (_pollIntervalMs === want && _pollTimerId != null) return;
    _pollIntervalMs = want;
    if (_pollTimerId != null) clearInterval(_pollTimerId);
    _pollTimerId = setInterval(fetchLabState, want);
}

/** Start the background poll. Returns the timer id. Idempotent. */
export function startLabStatePolling() {
    if (_pollTimerId != null) return _pollTimerId;
    syncLabStatePollingInterval();
    return _pollTimerId;
}

export async function fetchLabState() {
    try {
        await ensureBackendSelected();
        console.log(`[${new Date().toLocaleTimeString()}] Requesting Lab State...`);
        const response = await fetch(withBackendQuery('/api/lab-state'));
        if (!response.ok) {
            // Surface the backend's error detail when available — it usually carries a
            // human-readable reason (e.g. "controller in safe mode").
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errData = await response.json();
                if (typeof errData.detail === 'string') {
                    errorMsg = errData.detail;
                } else if (errData.detail && typeof errData.detail === 'object') {
                    errorMsg =
                        errData.detail.message ||
                        errData.detail.reason ||
                        JSON.stringify(errData.detail);
                }
            } catch (e) { /* ignore JSON parse error */ }
            console.error(`[${new Date().toLocaleTimeString()}] Error receiving Lab State: ${errorMsg}`);
            throw new Error(errorMsg);
        }

        store.labState = await response.json();
        refreshSessionLeaseBanner();
        _noteLabInitChange(store.labState);
        const labInit = deriveLabInit(store.labState);
        if (!labInit.ready) {
            // Do not populate canvas / component list / tunables until SYNC ready.
            syncLabStatePollingInterval();
            console.log(
                `[${new Date().toLocaleTimeString()}] Lab state received; `
                + `waiting for init (phase=${labInit.phase}).`,
            );
            return;
        }
        syncTeleopLivePosePolls();
        // Versioned alignment overlays travel with lab-state — mirror them into
        // the canvas mirrors so commit / checkout / stash changes show up.
        syncGuidesFromLabState();
        syncLaserLinesFromLabState();
        console.log(`[${new Date().toLocaleTimeString()}] Received Lab State successfully.`);

        const runtimeError = store.labState.last_runtime_error;
        const runtimeErrorKey =
            runtimeError && typeof runtimeError === 'object'
                ? `${runtimeError.timestamp || ''}|${runtimeError.target_id || ''}|${runtimeError.message || ''}`
                : null;
        const newRuntimeFailure =
            !!runtimeErrorKey && runtimeErrorKey !== store.previousRuntimeErrorKey;
        if (newRuntimeFailure) {
            const target = runtimeError.target_id ? ` for ${runtimeError.target_id}` : '';
            log(`Simulator move failed${target}: ${runtimeError.message || 'unknown error'}`, 'error');
        }

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
        // fetched state again — remove it so the operator isn't blocked. Dismissible operation
        // errors (e.g. "Apply on bench failed") are NOT connection problems, so we leave them up
        // until the user reads and dismisses them.
        const existingError = document.getElementById('error-modal');
        if (existingError && existingError.dataset.errorKind !== 'dismissible') {
            existingError.remove();
        }

        // Ghost state reconciliation. The ghost is what the canvas draws; we sync it from the
        // backend's nominal/measured pose when:
        //   1. Force Sync was requested (Refresh button).
        //   2. The system status transitioned BUSY/OPTIMIZING → IDLE (command finished).
        //   3. Initial load (handled by `!store.ghostState[name]` check).
        pruneOrphanRuntimeUiState();

        if (store.labState.components) {
            const justFinishedCommand = (store.previousSystemStatus !== 'IDLE' && store.labState.system_status === 'IDLE');
            const shouldSync = store.forceGhostSync || justFinishedCommand || newRuntimeFailure;

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
                    else if (store.labState.system_status === 'OPTIMIZING' && !store.isDragging && hasPose) {
                        store.ghostState[name] = { ...dp };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = 0;
                        }
                    }
                    // HOLDING: only the held tag's ghost follows live intent (incl. z) —
                    // every other component stays on the user's last committed intent.
                    //
                    // IMPORTANT: we deliberately do NOT overwrite the panel x/y/rot/z input
                    // fields here. Those represent the operator's *intent* for the next
                    // HOVER / PLACE_FROM_HOVER and must stay editable. Their initial values
                    // are set once by `renderInAirControlsForContext` when the HOLDING panel
                    // is rebuilt.
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
                        // While a command is in flight, keep optimistic ghost for
                        // pending tags even if something set forceGhostSync mid-BUSY
                        // (HTTP accept still has the old lab pose).
                        if (
                            store.pendingCommands.has(name) &&
                            store.labState.system_status === 'BUSY' &&
                            !justFinishedCommand
                        ) {
                            return;
                        }
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

            if (shouldSync) {
                store.forceGhostSync = false;
                // A command just settled (or a forced sync) — refresh the
                // git-like working flags so the Stash button / HEAD pill reflect
                // freshly-made (or cleared) uncommitted edits.
                void _deps.refreshControlWorkingState();
            }
        }

        // Rebuild EVERY open panel when its component's placement label OR the
        // top-level (system_status, holding) snapshot changes. The latter is
        // what flips IDLE ↔ HOLDING so the Pick / Hover / Place buttons
        // appear/disappear without the operator re-clicking the sidebar card.
        // Transient BUSY states are ignored so panels don't briefly revert
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
                rawStatus !== 'BUSY';
            const dataKey = componentDataSnapshot(compCtx);
            const dataChanged =
                snap.data != null && dataKey != null && snap.data !== dataKey;
            if (placementChanged || statusChanged || dataChanged) {
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

        // Clear in-flight overlays once the system has settled into any stable (non-BUSY,
        // non-OPTIMIZING) state. PICK_COMPONENT and HOVER land in HOLDING (not IDLE), so gating
        // this on IDLE only would leave the amber/purple "PICKING UP..." / "HOVERING..." label
        // forever and prevent render() from swapping in the steady-state "HOLDING" overlay.
        const stableStatus =
            store.labState.system_status === 'IDLE' ||
            store.labState.system_status === 'HOLDING';
        const reconcileActive =
            store.control.reconcileProgress && store.control.reconcileProgress.active;
        const jobMonitorActive = store.operationsMonitorActive;
        if (stableStatus && !reconcileActive && !jobMonitorActive) {
            store.pendingCommands.clear();
            store.pendingActions.clear();
        }
        if (jobMonitorActive && typeof store.operationsOnLabStatePoll === 'function') {
            store.operationsOnLabStatePoll();
        }
        syncMotorActionStatuses();
        // Per-tag snapshots are now written by ``updateContextPanel(tag)`` at
        // mount/rebuild time (see ``ui/context-panel.js``). The legacy global
        // ``contextPanelStatusSnapshot`` advance is therefore unnecessary —
        // each panel's snapshot is initialized when its panel mounts and
        // refreshed when its panel rebuilds.

        store.previousSystemStatus = store.labState.system_status;
        store.previousRuntimeErrorKey = runtimeErrorKey;
        store.previousTeleopReadyTags = teleopReadyNow;
        if (store.labState.system_status === 'IDLE') {
            if (store.isOptimizing) {
                store.isOptimizing = false;
                log('Optimization sequence complete.', 'info');
                const last = store.labState?.last_ensemble_optimization;
                if (last?.best_loss != null) {
                    log(
                        `Ensemble finished · best loss ${Number(last.best_loss).toFixed(4)} · ${last.evals ?? 0} evals`,
                        'info',
                    );
                }

                const optOverlay = document.getElementById('optimization-overlay');
                if (optOverlay) optOverlay.style.display = 'none';
                store.isOptimizingFeedActive = false;

                resetOptimizationFeedPreview();
            }
        } else if (store.labState.system_status === 'OPTIMIZING') {
            store.isOptimizing = true;

            const optOverlay = document.getElementById('optimization-overlay');
            const optStepText = document.getElementById('optimization-step-text');
            if (optOverlay && optStepText) {
                optOverlay.style.display = 'flex';
                const sess = store.labState.optimization_session;
                if (sess && sess.mode === 'ensemble') {
                    const loss =
                        sess.best_loss != null && Number.isFinite(Number(sess.best_loss))
                            ? Number(sess.best_loss).toFixed(3)
                            : '—';
                    optStepText.innerText = `ENSEMBLE · eval ${sess.eval ?? store.labState.optimization_step ?? 0} · loss ${loss}`;
                } else {
                    const runBit = store.labState.optimization_run_dir
                        ? ` · ${store.labState.optimization_run_dir}`
                        : '';
                    optStepText.innerText = `OPTIMIZING (Step ${store.labState.optimization_step || 0})${runBit}`;
                }
            }

            const feedPreview = document.getElementById('optimization-feed-preview');
            if (feedPreview) {
                feedPreview.style.border = '2px solid #22c55e';
                feedPreview.style.backgroundColor = '#0b2a19';
                feedPreview.style.boxShadow = '0 0 0 3px rgba(34,197,94,0.25)';
            }

            if (!store.isOptimizingFeedActive) {
                store.isOptimizingFeedActive = true;
                const feedImg = document.getElementById('optimization-feed-img');
                const feedPlaceholder = document.getElementById('optimization-feed-placeholder');
                const optTarget = resolveOptimizationTargetId(store.labState);

                if (feedImg && optTarget) {
                    feedImg.src = '';
                    feedImg.style.display = 'none';
                    if (feedPlaceholder) {
                        feedPlaceholder.style.display = 'flex';
                        const runBit2 = store.labState.optimization_run_dir
                            ? `<br><span style="font-size:9px;opacity:0.85">${store.labState.optimization_run_dir}</span>`
                            : '';
                        feedPlaceholder.innerHTML = `<span class="material-icons-round" style="font-size: 18px; margin-bottom: 2px;">auto_awesome</span><div>Optimizing... (Step ${store.labState.optimization_step || 0})${runBit2}</div>`;
                    }

                    feedImg.src =
                        `/api/components/${encodeURIComponent(optTarget)}/telemetry/optimization-stream?t=${Date.now()}`;
                    feedImg.style.display = 'block';
                    if (feedPlaceholder) feedPlaceholder.style.display = 'none';
                } else if (feedImg && !optTarget) {
                    console.warn(
                        '[UI] OPTIMIZING but optimization_target_id unknown — skipping feed swap',
                    );
                }
            }

            const ens = store.labState.optimization_session;

            // Mock-only legacy plot: synthetic beam-intensity when not ensemble.
            if (
                store.labState.lab_mode === 'MOCK' &&
                !(ens && ens.mode === 'ensemble') &&
                Math.random() > 0.5
            ) {
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

        syncLabStatePollingInterval();
        updateOptimizationModeUi();
        _deps.updateUI();
    } catch (error) {
        console.error('Failed to fetch lab state:', error);
        const statusBadge = document.getElementById('system-status-badge');
        if (statusBadge) {
            statusBadge.innerHTML = `<span class="status-dot error"></span> OFFLINE`;
        }

        showErrorModal('Connection Failed', error.message, { kind: 'connection' });

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

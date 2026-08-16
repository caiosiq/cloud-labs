/**
 * Command dispatch — primary path for interactive primitives via ``POST /api/command``.
 *
 * **Exception:** staged optimization mode (ensemble) submits via ``POST /api/jobs/submit``
 * so runs hold a session lease and appear on ``/operations`` — see ``api/jobs.js``.
 *
 * Two-stage flow:
 *   1. `sendCommand` — confirms with the operator (except high-frequency teleop frames,
 *      recipe recording, or ``{ skipConfirm: true }``), then delegates to `executeSendCommand`.
 *   2. `executeSendCommand` — does the network call, mirrors recipe state, and tracks
 *      `pendingCommands` / `pendingActions` so the renderer can show pending visual feedback
 *      while the backend processes the request.
 *
 * `confirmPlaceFromStorageDrag` is the analogous confirm-then-execute wrapper for
 * `PLACE_FROM_STORAGE` issued by the storage drag-and-drop interaction.
 *
 * Render side-effects (revert ghost pose, refresh context panel) are injected at boot via
 * `initCommands` so this module stays independent of the canvas/UI module graph.
 */
import { store } from '../state/store.js';
import { runtimeEditableOrMessage } from '../control/control-state.js';
import { log } from '../ui/log.js';
import { leaseHeaders } from './session-lease.js';
import { withBackendQuery } from '../state/backend-selection.js';
import { showConfirmationModal } from '../ui/modals.js';
import {
    PRIMITIVE_CONFIRM_SKIP,
    confirmPrimitiveCommand,
} from './confirm-primitive.js';
import { drawPose, isBreadboardIntent } from '../component-model.js';
import { applyCommandMatrixSnapshot } from '../ui/command-matrix-panel.js';
import { updateRecipeEditorList } from '../ui/recipes.js';
import { fetchLabState } from '../state/lab-state.js';

let _render = () => {};
let _updateContextPanel = () => {};
/** @type {() => Promise<void>} */
let _refreshControlWorkingState = async () => {};

/**
 * Inject the renderer and selection-panel refresher so we can roll back ghost state on cancel.
 * @param {{
 *   render: () => void,
 *   updateContextPanel: (tagId: string) => void,
 *   refreshControlWorkingState?: () => Promise<void>,
 * }} deps
 */
export function initCommands(deps) {
    if (deps.render) _render = deps.render;
    if (deps.updateContextPanel) _updateContextPanel = deps.updateContextPanel;
    if (typeof deps.refreshControlWorkingState === 'function') {
        _refreshControlWorkingState = deps.refreshControlWorkingState;
    }
}

/**
 * @param {object} command
 * @param {{ skipConfirm?: boolean }} [opts]
 */
export async function sendCommand(command, opts = {}) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        log(blocked, 'warn');
        return { ok: false, error: blocked };
    }

    const action = String(command?.action || '').trim();
    const skipConfirm =
        Boolean(opts.skipConfirm) ||
        store.isRecording ||
        PRIMITIVE_CONFIRM_SKIP.has(action);

    if (!skipConfirm && action) {
        const ok = await confirmPrimitiveCommand(command);
        if (!ok) {
            log(`${action} cancelled by user.`, 'info');

            // MOVE_COMPONENT: revert ghost pose if the canvas was already previewing the target.
            if (
                action === 'MOVE_COMPONENT' &&
                command.target_id &&
                store.labState?.components?.[command.target_id] &&
                isBreadboardIntent(store.labState.components[command.target_id])
            ) {
                const original = drawPose(store.labState.components[command.target_id]);
                if (store.ghostState[command.target_id]) {
                    store.ghostState[command.target_id].x = original.x;
                    store.ghostState[command.target_id].y = original.y;
                    store.ghostState[command.target_id].rotation = original.rotation;
                    if (store.openPanels.includes(command.target_id)) {
                        _updateContextPanel(command.target_id);
                    }
                    _render();
                }
            }
            return { ok: false, error: 'cancelled' };
        }
    }

    return await executeSendCommand(command);
}

export async function executeSendCommand(command) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        log(blocked, 'warn');
        return { ok: false, error: blocked };
    }

    /** @param {unknown} detail */
    function formatApiDetail(detail) {
        if (typeof detail === 'string') return detail;
        if (Array.isArray(detail)) {
            return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
        }
        if (detail && typeof detail === 'object') {
            const msg = detail.message;
            if (msg && Array.isArray(detail.errors) && detail.errors.length) {
                const errs = detail.errors
                    .map((e) => e.reason || e.msg || JSON.stringify(e))
                    .join('; ');
                return `${msg}: ${errs}`;
            }
            return msg || JSON.stringify(detail);
        }
        return 'Request failed';
    }

    try {
        log(`Sending command: ${command.action}`, 'info');

        // Recipe editor: capture the command verbatim so it can be played back later.
        // We still execute it live so the user sees the immediate effect.
        if (store.isRecording) {
            const step = {
                step: store.currentRecipeSteps.length + 1,
                action: command.action,
                component: command.target_id,
                parameters: command.parameters || {},
            };
            store.currentRecipeSteps.push(step);
            updateRecipeEditorList();
        }

        if (command.target_id) {
            store.pendingCommands.add(command.target_id);
            if (command.action) store.pendingActions.set(command.target_id, command.action);
            _render();
        }

        const response = await fetch(withBackendQuery('/api/command'), {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(command),
        });

        if (response.status === 409) {
            // 409 = system busy / state machine rejected the command. Surface the detail
            // verbatim to the log so the operator knows *why*.
            const body = await response.json().catch(() => ({}));
            const detail = formatApiDetail(body?.detail) || 'System BUSY or command not allowed in current state.';
            log(`Command rejected (409): ${detail}`, 'warn');
            if (command.target_id) {
                store.pendingCommands.delete(command.target_id);
                store.pendingActions.delete(command.target_id);
            }
            return { ok: false, error: detail };
        }

        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
            const detail = formatApiDetail(result.detail) || `HTTP ${response.status}`;
            log(`Command rejected: ${detail}`, 'error');
            if (command.target_id) {
                store.pendingCommands.delete(command.target_id);
                store.pendingActions.delete(command.target_id);
            }
            return { ok: false, error: detail };
        }

        // Command Matrix: accepted into a per-thread queue (may still be waiting).
        if (result.status === 'queued') {
            const res = Array.isArray(result.resources) ? result.resources.join(', ') : '';
            const cid = result.command_id ? ` [${result.command_id}]` : '';
            log(
                `${command.action}${cid} queued${res ? ` on ${res}` : ''}` +
                    (command.target_id ? ` → ${command.target_id}` : ''),
                'info',
            );
            if (result.command_matrix) {
                applyCommandMatrixSnapshot(result.command_matrix);
            }
            return { ok: true, message: result.message || 'queued', queued: true, result };
        }

        const serverMessage = result.message || result.status || 'Command accepted';
        log(`Server: ${serverMessage}`, 'info');

        if (command.action === 'OPTIMIZE') {
            store.isOptimizing = true;
            store.optimizationData = [];
            if (command.parameters?.mode === 'ensemble') {
                store.ensembleLossTrace = [];
                const b = store.optimizationBuilder;
                if (b) {
                    b.runCompleted = false;
                    b.viewingLastRun = false;
                    b.lastSeenResultAt = null;
                    b.awaitingRunResults = true;
                    b.maxEvalsForRun =
                        command.parameters?.solver?.max_total_evals ?? b.maxEvals ?? 200;
                }
                void fetchLabState();
            }
        } else {
            try {
                // Refresh overview (BUSY badge, etc.) but do **not** force-sync
                // ghost poses: the command was only accepted; lab nominal_pose
                // may still be the pre-move value. Keep the operator's commanded
                // ghost until BUSY→IDLE reconciliation (or an explicit Refresh /
                // RECORD_TUNABLES).
                await fetchLabState();
                _render();
                // HTTP edges often stay IDLE on the coordinator until southbound
                // finishes in one request — refresh dirty/stash without waiting
                // for a separate BUSY→IDLE poll edge.
                void _refreshControlWorkingState();
            } catch (syncError) {
                log(`Command completed, but refresh failed: ${syncError.message || syncError}`, 'warn');
            }
        }

        return { ok: true, message: serverMessage };
    } catch (error) {
        log(`Command failed: ${error.message}`, 'error');
        if (command.target_id) {
            store.pendingCommands.delete(command.target_id);
            store.pendingActions.delete(command.target_id);
        }
        return { ok: false, error: error.message || String(error) };
    }
}

/**
 * After dragging a STORED part onto the breadboard: confirm PLACE_FROM_STORAGE (same UX as the
 * MOVE_COMPONENT confirm). On cancel we restore the ghost back to its original pose so the
 * canvas doesn't leave the part visually "placed" at the drop site.
 */
export async function confirmPlaceFromStorageDrag(targetId, parameters) {
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
                if (store.openPanels.includes(targetId)) {
                    _updateContextPanel(targetId);
                }
                _render();
                resolve({ ok: false, error: 'cancelled' });
            },
        );
    });
}

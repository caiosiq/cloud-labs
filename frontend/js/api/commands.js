/**
 * Command dispatch — the **only** code path that POSTs to `/api/command`.
 *
 * Two-stage flow:
 *   1. `sendCommand` — applies user-facing safeguards (e.g. MOVE_COMPONENT confirm modal). If the
 *      user confirms, it delegates to `executeSendCommand`. Recording-mode bypasses the modal
 *      because the recipe editor needs every command to be captured deterministically.
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
import { showConfirmationModal } from '../ui/modals.js';
import { drawPose, isBreadboardIntent } from '../component-model.js';
import { updateRecipeEditorList } from '../ui/recipes.js';

let _render = () => {};
let _updateContextPanel = () => {};

/**
 * Inject the renderer and selection-panel refresher so we can roll back ghost state on cancel.
 * @param {{ render: () => void, updateContextPanel: (tagId: string) => void }} deps
 */
export function initCommands(deps) {
    if (deps.render) _render = deps.render;
    if (deps.updateContextPanel) _updateContextPanel = deps.updateContextPanel;
}

export async function sendCommand(command) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        log(blocked, 'warn');
        return { ok: false, error: blocked };
    }

    // MOVE_COMPONENT goes through a user confirmation modal — except while recording a recipe,
    // where every command is captured verbatim so the recipe stays deterministic.
    if (command.action === 'MOVE_COMPONENT' && !store.isRecording) {
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
                    log('Move cancelled by user.', 'info');

                    if (
                        command.target_id &&
                        store.labState &&
                        store.labState.components &&
                        store.labState.components[command.target_id] &&
                        isBreadboardIntent(store.labState.components[command.target_id])
                    ) {
                        const original = drawPose(store.labState.components[command.target_id]);
                        // Only revert if we have the ghost state object — the ghost is what the canvas draws,
                        // so without it there is nothing visible to roll back.
                        if (store.ghostState[command.target_id]) {
                            store.ghostState[command.target_id].x = original.x;
                            store.ghostState[command.target_id].y = original.y;
                            store.ghostState[command.target_id].rotation = original.rotation;

                            // Multi-panel: refresh whichever open panel matches
                            // this target — not just the focused one.
                            if (store.openPanels.includes(command.target_id)) {
                                _updateContextPanel(command.target_id);
                            }
                            _render();
                        }
                    }
                    resolve({ ok: false, error: 'cancelled' });
                },
            );
        });
    }

    return await executeSendCommand(command);
}

export async function executeSendCommand(command) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        log(blocked, 'warn');
        return { ok: false, error: blocked };
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

        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(command),
        });

        if (response.status === 409) {
            // 409 = system busy / state machine rejected the command. Surface the detail
            // verbatim to the log so the operator knows *why*.
            const body = await response.json().catch(() => ({}));
            const detail = (body && body.detail) ? String(body.detail) : 'System BUSY or command not allowed in current state.';
            log(`Command rejected (409): ${detail}`, 'warn');
            if (command.target_id) {
                store.pendingCommands.delete(command.target_id);
                store.pendingActions.delete(command.target_id);
            }
            return { ok: false, error: detail };
        }

        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
            const detail = result.detail || `HTTP ${response.status}`;
            log(`Command rejected: ${detail}`, 'error');
            if (command.target_id) {
                store.pendingCommands.delete(command.target_id);
                store.pendingActions.delete(command.target_id);
            }
            return { ok: false, error: detail };
        }

        log(`Server: ${result.message}`, 'info');

        if (command.action === 'OPTIMIZE') {
            store.isOptimizing = true;
            store.optimizationData = [];
        }

        return { ok: true, message: result.message || 'Accepted' };
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

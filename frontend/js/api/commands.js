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
import { log } from '../ui/log.js';
import { showConfirmationModal } from '../ui/modals.js';
import { drawPose, isBreadboardIntent } from '../component-model.js';
import { updateRecipeEditorList } from '../ui/recipes.js';

let _render = () => {};
let _updateContextPanel = () => {};
let _checkCollision = null;

/**
 * Inject the renderer and selection-panel refresher so we can roll back ghost state on cancel.
 * @param {{ render: () => void, updateContextPanel: (tagId: string) => void }} deps
 */
export function initCommands(deps) {
    if (deps.render) _render = deps.render;
    if (deps.updateContextPanel) _updateContextPanel = deps.updateContextPanel;
    if (deps.checkCollision) _checkCollision = deps.checkCollision;
}

function moveComponentOriginalPose(tagId) {
    if (
        tagId &&
        store.labState &&
        store.labState.components &&
        store.labState.components[tagId] &&
        isBreadboardIntent(store.labState.components[tagId])
    ) {
        return drawPose(store.labState.components[tagId]);
    }
    return null;
}

function refreshMoveGhost(tagId) {
    if (store.openPanels.includes(tagId)) {
        _updateContextPanel(tagId);
    }
    _render();
}

function revertMoveGhost(tagId) {
    const original = moveComponentOriginalPose(tagId);
    if (!original || !store.ghostState[tagId]) return;
    store.ghostState[tagId].x = original.x;
    store.ghostState[tagId].y = original.y;
    store.ghostState[tagId].rotation = original.rotation;
    refreshMoveGhost(tagId);
}

function setMoveGhost(tagId, pose) {
    if (!store.ghostState[tagId]) return;
    store.ghostState[tagId].x = pose.target_x;
    store.ghostState[tagId].y = pose.target_y;
    store.ghostState[tagId].rotation = pose.rotation;
    refreshMoveGhost(tagId);
}

function numberField(labelText, value, step) {
    const field = document.createElement('label');
    field.style.display = 'flex';
    field.style.flexDirection = 'column';
    field.style.alignItems = 'stretch';
    field.style.gap = '5px';
    field.style.textAlign = 'left';
    field.style.color = '#cbd5e1';
    field.style.fontSize = '11px';
    field.style.fontWeight = '600';

    const label = document.createElement('span');
    label.textContent = labelText;

    const input = document.createElement('input');
    input.type = 'number';
    input.step = step;
    input.value = Number.isFinite(Number(value)) ? Number(value).toFixed(1) : '';
    input.style.width = '100%';
    input.style.boxSizing = 'border-box';
    input.style.padding = '8px 9px';
    input.style.backgroundColor = '#0f1115';
    input.style.border = '1px solid #2a2e36';
    input.style.borderRadius = '4px';
    input.style.color = '#e2e8f0';
    input.style.fontSize = '13px';

    field.appendChild(label);
    field.appendChild(input);
    return { field, input };
}

function showMoveConfirmationModal(command, onConfirm, onCancel) {
    if (document.getElementById('confirm-modal')) return;

    const params = command.parameters || {};
    const overlay = document.createElement('div');
    overlay.id = 'confirm-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100vw';
    overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('form');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #3b82f6';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '420px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    const icon = document.createElement('span');
    icon.className = 'material-icons-round';
    icon.textContent = 'help_outline';
    icon.style.fontSize = '40px';
    icon.style.color = '#3b82f6';
    icon.style.marginBottom = '12px';

    const title = document.createElement('h3');
    title.textContent = 'Confirm Action';
    title.style.margin = '0 0 12px 0';
    title.style.color = '#e2e8f0';

    const prompt = document.createElement('p');
    prompt.style.margin = '0 0 16px 0';
    prompt.style.color = '#94a3b8';
    prompt.style.fontSize = '14px';
    prompt.style.lineHeight = '1.5';
    prompt.appendChild(document.createTextNode('Move '));
    const targetName = document.createElement('strong');
    targetName.textContent = command.target_id;
    prompt.appendChild(targetName);
    prompt.appendChild(document.createTextNode('?'));

    const grid = document.createElement('div');
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = '1fr 1fr 1fr';
    grid.style.gap = '8px';
    grid.style.marginBottom = '12px';

    const x = numberField('X mm', params.target_x, '0.1');
    const y = numberField('Y mm', params.target_y, '0.1');
    const rot = numberField('Rot deg', params.rotation, '0.1');
    grid.appendChild(x.field);
    grid.appendChild(y.field);
    grid.appendChild(rot.field);

    const error = document.createElement('div');
    error.style.minHeight = '16px';
    error.style.margin = '0 0 12px 0';
    error.style.color = '#f87171';
    error.style.fontSize = '12px';
    error.style.textAlign = 'left';

    const buttons = document.createElement('div');
    buttons.style.display = 'flex';
    buttons.style.justifyContent = 'center';
    buttons.style.gap = '12px';

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.id = 'confirm-no';
    cancel.className = 'btn btn-secondary';
    cancel.style.width = 'auto';
    cancel.style.padding = '8px 20px';
    cancel.textContent = 'Cancel';

    const confirm = document.createElement('button');
    confirm.type = 'submit';
    confirm.id = 'confirm-yes';
    confirm.className = 'btn btn-primary';
    confirm.style.width = 'auto';
    confirm.style.padding = '8px 20px';
    confirm.textContent = 'Confirm';

    buttons.appendChild(cancel);
    buttons.appendChild(confirm);
    card.appendChild(icon);
    card.appendChild(title);
    card.appendChild(prompt);
    card.appendChild(grid);
    card.appendChild(error);
    card.appendChild(buttons);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    rot.input.focus();
    rot.input.select();

    cancel.onclick = () => {
        overlay.remove();
        if (onCancel) onCancel();
    };

    card.onsubmit = (event) => {
        event.preventDefault();
        const edited = {
            target_x: parseFloat(x.input.value),
            target_y: parseFloat(y.input.value),
            rotation: parseFloat(rot.input.value),
        };
        if (![edited.target_x, edited.target_y, edited.rotation].every(Number.isFinite)) {
            error.textContent = 'Enter finite numbers for X, Y, and Rot.';
            return;
        }
        if (typeof _checkCollision === 'function') {
            const collision = _checkCollision(command.target_id, edited.target_x, edited.target_y);
            if (collision && collision.detected) {
                error.textContent = `Move blocked: collision with ${collision.other}.`;
                return;
            }
        }
        overlay.remove();
        onConfirm({
            ...command,
            parameters: {
                ...params,
                ...edited,
            },
        });
    };
}

export async function sendCommand(command) {
    // MOVE_COMPONENT goes through a user confirmation modal except while recording a recipe,
    // where every command is captured verbatim so the recipe stays deterministic.
    if (command.action === 'MOVE_COMPONENT' && !store.isRecording) {
        return new Promise((resolve) => {
            showMoveConfirmationModal(
                command,
                async (editedCommand) => {
                    if (editedCommand.parameters) {
                        setMoveGhost(command.target_id, editedCommand.parameters);
                    }
                    const r = await executeSendCommand(editedCommand);
                    resolve(r);
                },
                () => {
                    log('Move cancelled by user.', 'info');
                    revertMoveGhost(command.target_id);
                    resolve({ ok: false, error: 'cancelled' });
                },
            );
        });
    }

    return await executeSendCommand(command);
}

export async function executeSendCommand(command) {
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

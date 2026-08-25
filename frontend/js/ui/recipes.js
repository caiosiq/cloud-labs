/**
 * Recipe editor + playback panel.
 *
 * Three concerns:
 *   1. Render `store.availableRecipes` into the right-sidebar list, with a play button.
 *   2. Record / save: capture move-and-optimize sequences while `store.isRecording` is true.
 *      Saved via `POST /api/recipes`.
 *   3. Play: validate that all referenced components exist; if not, pop a "request from catalog"
 *      modal so the user can satisfy requirements.
 *
 * `step.component` is the modern recipe schema; older recipes used `step.target`, which we
 * still tolerate when computing requirements.
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { fetchRecipes } from '../api/fetchers.js';
import { getComponentIcon } from './icons.js';
import { backendHeaders, withBackendQuery } from '../state/backend-selection.js';

let _recipeList = null;
let _recipeStepsContainer = null;
let _recipeEditorName = null;
let _recipeEditorSave = null;
let _recipeEditorCancel = null;
let _recIndicator = null;
let _recordBtn = null;

/**
 * Wire the panel's DOM nodes. Pass null for any element that isn't present yet —
 * the module will no-op for that slot.
 * @param {{
 *   recipeList?: HTMLElement | null,
 *   recipeStepsContainer?: HTMLElement | null,
 *   recipeEditorName?: HTMLInputElement | null,
 *   recipeEditorSave?: HTMLButtonElement | null,
 *   recipeEditorCancel?: HTMLButtonElement | null,
 *   recIndicator?: HTMLElement | null,
 *   recordBtn?: HTMLButtonElement | null,
 * }} deps
 */
export function initRecipes(deps) {
    _recipeList = deps.recipeList || null;
    _recipeStepsContainer = deps.recipeStepsContainer || null;
    _recipeEditorName = deps.recipeEditorName || null;
    _recipeEditorSave = deps.recipeEditorSave || null;
    _recipeEditorCancel = deps.recipeEditorCancel || null;
    _recIndicator = deps.recIndicator || null;
    _recordBtn = deps.recordBtn || null;

    if (_recordBtn) _recordBtn.addEventListener('click', toggleRecording);
    if (_recipeEditorSave) _recipeEditorSave.addEventListener('click', saveCurrentRecipe);
}

export function renderRecipes() {
    if (!_recipeList) return;
    _recipeList.innerHTML = '';
    if (store.availableRecipes.length === 0) {
        _recipeList.innerHTML =
            '<div style="color: #64748b; font-size: var(--text-sm); padding: 10px; text-align: center;">No recipes saved.</div>';
        return;
    }

    store.availableRecipes.forEach((recipe) => {
        const item = document.createElement('div');
        item.style.backgroundColor = 'rgba(255,255,255,0.03)';
        item.style.border = '1px solid #2a2e36';
        item.style.borderRadius = '6px';
        item.style.padding = '8px';
        item.style.marginBottom = '6px';
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.justifyContent = 'space-between';

        item.innerHTML = `
            <div style="overflow: hidden;">
                <div style="font-weight: 500; font-size: var(--text-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${recipe.name}</div>
                <div style="font-size: var(--text-xs); color: #64748b;">${recipe.steps.length} steps</div>
            </div>
        `;

        const playBtn = document.createElement('button');
        playBtn.className = 'btn btn-primary';
        playBtn.style.padding = '4px 8px';
        playBtn.style.fontSize = 'var(--text-xs)';
        playBtn.style.width = 'auto';
        playBtn.innerHTML = '<span class="material-icons-round" style="font-size: var(--text-base);">play_arrow</span>';
        playBtn.title = 'Run Recipe';
        playBtn.onclick = () => playRecipe(recipe.id);

        item.appendChild(playBtn);
        _recipeList.appendChild(item);
    });
}

export function updateRecipeEditorList() {
    if (!_recipeStepsContainer) return;
    _recipeStepsContainer.innerHTML = '';
    if (store.currentRecipeSteps.length === 0) {
        _recipeStepsContainer.innerHTML =
            '<div style="padding: 20px; text-align: center; color: #64748b; font-size: var(--text-sm);">No steps recorded yet.</div>';
        return;
    }

    store.currentRecipeSteps.forEach((step, index) => {
        const item = document.createElement('div');
        item.className = 'recipe-step-item';

        let desc = `${step.component}`;
        if (step.action === 'MOVE_COMPONENT') {
            desc += ` to (${step.parameters.target_x.toFixed(1)}, ${step.parameters.target_y.toFixed(1)})`;
        } else if (step.action === 'OPTIMIZE') {
            desc += ' (ensemble)';
        }

        item.innerHTML = `
            <div class="step-num">${index + 1}</div>
            <div class="step-action">${step.action === 'MOVE_COMPONENT' ? 'MOVE' : step.action}</div>
            <div class="step-desc">${desc}</div>
            <div class="material-icons-round step-del" title="Remove Step">delete</div>
        `;

        item.querySelector('.step-del').addEventListener('click', () => deleteStep(index));
        _recipeStepsContainer.appendChild(item);
    });
}

function deleteStep(index) {
    store.currentRecipeSteps.splice(index, 1);
    updateRecipeEditorList();
}

function toggleRecording() {
    store.isRecording = !store.isRecording;

    if (store.isRecording) {
        store.currentRecipeSteps = [];
        if (_recipeEditorName) _recipeEditorName.value = `Recipe ${new Date().toLocaleTimeString()}`;
        updateRecipeEditorList();
        if (_recIndicator) _recIndicator.style.display = 'flex';
        if (_recordBtn) {
            _recordBtn.classList.add('btn-primary');
            _recordBtn.classList.remove('btn-secondary');
        }
        log('Recording started. Perform actions on the canvas.', 'warn');
    } else {
        if (_recIndicator) _recIndicator.style.display = 'none';
        if (_recordBtn) {
            _recordBtn.classList.remove('btn-primary');
            _recordBtn.classList.add('btn-secondary');
        }
        log('Recording stopped.', 'info');
    }
}

async function saveCurrentRecipe() {
    if (store.currentRecipeSteps.length === 0) {
        alert('No actions recorded!');
        return;
    }

    const name = (_recipeEditorName && _recipeEditorName.value) || 'Untitled Recipe';
    const id =
        name.toLowerCase().replace(/[^a-z0-9]/g, '_') + '_' + Math.floor(Math.random() * 1000);

    const steps = store.currentRecipeSteps.map((s, i) => ({ ...s, step: i + 1 }));

    const recipe = { id, name, steps };

    try {
        const res = await fetch(withBackendQuery('/api/recipes'), {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(recipe),
        });

        if (res.ok) {
            log(`Recipe "${name}" saved!`, 'info');
            store.isRecording = false;
            if (_recIndicator) _recIndicator.style.display = 'none';
            if (_recordBtn) {
                _recordBtn.classList.remove('btn-primary');
                _recordBtn.classList.add('btn-secondary');
            }
            fetchRecipes();
        }
    } catch (e) {
        log('Failed to save recipe.', 'error');
    }
}

async function playRecipe(id) {
    const recipe = store.availableRecipes.find((r) => r.id === id);
    if (!recipe) {
        log('Recipe not found locally.', 'error');
        return;
    }

    const reqs = checkRecipeRequirements(recipe);

    if (!reqs.valid) {
        showRecipeRequirementModal(recipe.name, reqs);
        return;
    }

    try {
        log(`Playing recipe ${id}...`, 'info');
        const res = await fetch(withBackendQuery(`/api/recipes/${id}/play`), {
            method: 'POST',
            headers: backendHeaders(),
        });
        if (res.ok) {
            log('Recipe execution started.', 'info');
        } else {
            const err = await res.json();
            log(`Failed to start recipe: ${err.detail}`, 'error');
        }
    } catch (e) {
        log('Network error starting recipe.', 'error');
    }
}

function checkRecipeRequirements(recipe) {
    if (!store.labState || !store.labState.components) {
        return { valid: false, error: 'Lab state not loaded' };
    }

    const missingRequestable = [];
    const missingUnknown = [];

    const requiredComponents = new Set();
    recipe.steps.forEach((step) => {
        if (step.component) requiredComponents.add(step.component);
        // Fallback for older recipe formats if they used 'target' or 'target_id'
        if (step.target) requiredComponents.add(step.target);
    });

    requiredComponents.forEach((id) => {
        if (!store.labState.components[id]) {
            if (store.catalogMap[id]) {
                missingRequestable.push(store.catalogMap[id]);
            } else {
                missingUnknown.push(id);
            }
        }
    });

    return {
        valid: missingRequestable.length === 0 && missingUnknown.length === 0,
        missingRequestable,
        missingUnknown,
    };
}

function showRecipeRequirementModal(recipeName, reqs) {
    const existing = document.getElementById('req-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'req-modal';
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

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #f59e0b';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '500px';
    card.style.maxHeight = '80vh';
    card.style.overflowY = 'auto';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    let html = `
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
            <span class="material-icons-round" style="font-size: 32px; color: #f59e0b;">warning_amber</span>
            <h2 style="margin: 0; color: #e2e8f0; font-size: 18px;">Components Missing</h2>
        </div>
        <p style="color: #94a3b8; font-size: var(--text-base); margin-bottom: 20px;">
            The recipe <strong>"${recipeName}"</strong> cannot run because some components are not present in the lab.
        </p>
    `;

    if (reqs.missingUnknown.length > 0) {
        html += `
            <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 6px; padding: 12px; margin-bottom: 16px;">
                <div style="color: #ef4444; font-weight: 600; font-size: var(--text-sm); margin-bottom: 8px;">OUTDATED / UNKNOWN COMPONENTS</div>
                <div style="font-size: var(--text-sm); color: #cbd5e1;">
                    The following IDs are not found in the Catalog. The recipe may be outdated.
                    <ul style="margin: 8px 0 0 20px; padding: 0;">
                        ${reqs.missingUnknown.map((id) => `<li>${id}</li>`).join('')}
                    </ul>
                </div>
            </div>
        `;
    }

    if (reqs.missingRequestable.length > 0) {
        html += `
            <div style="margin-bottom: 16px;">
                <div style="color: #e2e8f0; font-weight: 600; font-size: var(--text-sm); margin-bottom: 8px;">AVAILABLE TO REQUEST</div>
                <div id="req-list" style="display: flex; flex-direction: column; gap: 8px;">
                </div>
            </div>
        `;
    }

    html += `
        <div style="display: flex; justify-content: flex-end; gap: 12px; margin-top: 24px;">
            <button id="req-close-btn" class="btn btn-secondary" style="width: auto;">Close</button>
        </div>
    `;

    card.innerHTML = html;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    document.getElementById('req-close-btn').onclick = () => overlay.remove();

    const listContainer = document.getElementById('req-list');
    if (listContainer && reqs.missingRequestable.length > 0) {
        reqs.missingRequestable.forEach((item) => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.justifyContent = 'space-between';
            row.style.background = '#0f1115';
            row.style.padding = '8px 12px';
            row.style.borderRadius = '4px';
            row.style.border = '1px solid #2a2e36';

            const icon = getComponentIcon(item.type);

            row.innerHTML = `
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span class="material-icons-round" style="color: #64748b; font-size: 18px;">${icon}</span>
                    <div>
                        <div style="font-size: var(--text-sm); color: #e2e8f0; font-weight: 500;">${item.name}</div>
                        <div style="font-size: var(--text-xs); color: #64748b;">${item.tag_id}</div>
                    </div>
                </div>
            `;

            const btn = document.createElement('button');
            btn.className = 'btn btn-primary';
            btn.style.width = 'auto';
            btn.style.padding = '4px 10px';
            btn.style.fontSize = 'var(--text-xs)';
            btn.textContent = 'Request';

            btn.onclick = async () => {
                btn.textContent = 'Requesting...';
                btn.disabled = true;
                try {
                    const res = await fetch(withBackendQuery('/api/components'), {
                        method: 'POST',
                        headers: backendHeaders({ 'Content-Type': 'application/json' }),
                        body: JSON.stringify(item),
                    });
                    if (res.ok) {
                        btn.textContent = 'Requested';
                        btn.style.backgroundColor = '#10b981';
                        btn.style.borderColor = '#10b981';
                    } else {
                        btn.textContent = 'Failed';
                        btn.disabled = false;
                    }
                } catch (e) {
                    btn.textContent = 'Error';
                    btn.disabled = false;
                }
            };

            row.appendChild(btn);
            listContainer.appendChild(row);
        });
    }
}

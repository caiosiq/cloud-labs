/**
 * Sidebar track / untrack controls (active catalog = controlled parts).
 */
import { store } from '../state/store.js';
import { isComponentControlled } from '../component-model.js';
import { runtimeEditableOrMessage } from '../control/control-state.js';
import { trackComponent, untrackComponent } from '../api/inventory.js';
import { fetchCatalogMap } from '../api/fetchers.js';
import { showErrorModal } from './modals.js';

let _fetchLabState = async () => {};
let _updateUI = () => {};
let _closePanel = () => {};

/**
 * @param {{ fetchLabState?: () => Promise<void>, updateUI?: () => void, closePanel?: (tagId: string) => void }} deps
 */
export function initInventoryAdd(deps = {}) {
    if (deps && typeof deps.fetchLabState === 'function') {
        _fetchLabState = deps.fetchLabState;
    }
    if (deps && typeof deps.updateUI === 'function') {
        _updateUI = deps.updateUI;
    }
    if (deps && typeof deps.closePanel === 'function') {
        _closePanel = deps.closePanel;
    }
}

/** Library tags not yet present in runtime. */
export function listLibraryOnlyTags() {
    const comps = store.labState?.components || {};
    const library = store.libraryTagIds?.length
        ? store.libraryTagIds
        : Object.keys(store.catalogMap || {});
    return library.filter((tagId) => !comps[tagId]).sort();
}

async function refreshAfterControlChange() {
    await fetchCatalogMap();
    await _fetchLabState();
    _updateUI();
}

/** Clear canvas/UI pose state immediately after untrack (before the next poll). */
function clearRuntimeUiStateForTag(tagId) {
    if (!tagId) return;
    delete store.ghostState[tagId];
    store.pendingCommands.delete(tagId);
    store.pendingActions.delete(tagId);
    delete store.teleopLivePose[tagId];
    delete store.teleopTarget[tagId];
    delete store.teleopTargetAwaitingLive[tagId];
    if (store.draggingComponent === tagId) {
        store.draggingComponent = null;
        store.isDragging = false;
    }
    if (store.dragFromStorageTag === tagId) {
        store.dragFromStorageTag = null;
        store.dragFromStorageStartPose = null;
    }
}

/**
 * @param {string} tagId
 */
export async function enableComponentControl(tagId) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        showErrorModal('Cannot track component', blocked);
        return false;
    }
    try {
        await trackComponent(tagId);
        await refreshAfterControlChange();
        return true;
    } catch (err) {
        showErrorModal('Track component', err.message || 'Failed to enable control');
        return false;
    }
}

/**
 * @param {string} tagId
 */
export async function disableComponentControl(tagId) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        showErrorModal('Cannot untrack component', blocked);
        return false;
    }
    try {
        await untrackComponent(tagId);
        _closePanel(tagId);
        clearRuntimeUiStateForTag(tagId);
        await refreshAfterControlChange();
        return true;
    } catch (err) {
        showErrorModal('Untrack component', err.message || 'Failed to disable control');
        return false;
    }
}

/**
 * @param {string} tagId
 * @param {boolean} tracked
 * @returns {HTMLButtonElement}
 */
export function createTrackToggleButton(tagId, tracked) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `track-toggle-btn${tracked ? ' is-tracked' : ''}`;
    if (tracked) {
        btn.title = 'Stop controlling this part';
        btn.setAttribute('aria-label', `Stop controlling ${tagId}`);
        btn.innerHTML = '<span class="material-icons-round" aria-hidden="true">remove</span>';
        btn.addEventListener('click', (ev) => {
            ev.stopPropagation();
            void disableComponentControl(tagId);
        });
    } else {
        btn.title = 'Start controlling this part';
        btn.setAttribute('aria-label', `Start controlling ${tagId}`);
        btn.innerHTML = '<span class="material-icons-round" aria-hidden="true">add</span>';
        btn.addEventListener('click', (ev) => {
            ev.stopPropagation();
            void enableComponentControl(tagId);
        });
    }
    return btn;
}

/** @deprecated use enableComponentControl */
export async function addComponentToTable(tagId) {
    return enableComponentControl(tagId);
}

/** @deprecated use createTrackToggleButton */
export function createAddToTableButton(tagId) {
    return createTrackToggleButton(tagId, isComponentControlled(tagId));
}

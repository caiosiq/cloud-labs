import { store } from '../state/store.js';
import {
    clearStoreToSlotMode,
    isStorageRegion,
    occupiedStorageSlots,
} from '../storage-region.js';
import { coordInput, dispatchPrimitive, primitiveRegion, runButton, secondaryButton } from './shared.js';

export function renderStoreComponent(ctx) {
    const { tagId, placementState, hooks, render, updateContextPanel } = ctx;
    if (placementState !== 'PLACED') return null;
    const { section, body } = primitiveRegion('STORE_COMPONENT', 'STORE COMPONENT');

    const tip = document.createElement('p');
    tip.className = 'opt-hint';
    tip.style.cssText = 'margin:0 0 4px;font-size:11px;color:#94a3b8;';
    tip.textContent =
        'Auto pack fills the next free cell (bottom row, left→right). Or pick a free cell on the canvas.';
    body.appendChild(tip);

    const btnAuto = runButton('Move to storage (auto pack)', 'inventory_2');
    btnAuto.onclick = () => {
        clearStoreToSlotMode();
        void dispatchPrimitive(hooks, {
            action: 'STORE_COMPONENT',
            target_id: tagId,
            parameters: {},
        });
        if (typeof render === 'function') render();
        if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
    };
    body.appendChild(btnAuto);

    const choosing = store.storeToSlotTag === tagId;
    const btnPick = secondaryButton(
        choosing ? 'Click a free storage cell…' : 'Store to cell…',
        'grid_view',
    );
    if (choosing) {
        btnPick.disabled = true;
        const btnCancel = secondaryButton('Cancel cell pick', 'close');
        btnCancel.onclick = () => {
            clearStoreToSlotMode();
            hooks.log('Store-to-cell cancelled.', 'info');
            if (typeof render === 'function') render();
            if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
        };
        body.appendChild(btnPick);
        body.appendChild(btnCancel);
        const occ = occupiedStorageSlots(tagId);
        const hint = document.createElement('p');
        hint.style.cssText = 'margin:0;font-size:11px;color:#67e8f9;';
        hint.textContent = `Choose mode on — ${occ.size} cell(s) occupied. Click empty grid square.`;
        body.appendChild(hint);
    } else {
        btnPick.onclick = () => {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            store.storeToSlotTag = tagId;
            hooks.log('Store-to-cell: click a free square in the storage grid.', 'info');
            if (typeof render === 'function') render();
            if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
        };
        body.appendChild(btnPick);
    }

    return section;
}

export function renderPlaceFromStorage(ctx) {
    const { tagId, placementState, hooks, render, updateContextPanel } = ctx;
    if (placementState !== 'STORED') return null;

    const { section, body } = primitiveRegion('PLACE_FROM_STORAGE', 'PLACE FROM STORAGE');
    const p = ctx.getPose() || {};
    const grid = document.createElement('div');
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = '1fr 1fr 1fr';
    grid.style.gap = '6px';
    const xInp = coordInput('X (mm)', p.x);
    const yInp = coordInput('Y (mm)', p.y);
    const rInp = coordInput('θ (°)', p.rotation ?? 0);
    grid.appendChild(xInp);
    grid.appendChild(yInp);
    grid.appendChild(rInp);
    body.appendChild(grid);

    const bPlace = runButton('Place from storage', 'north_east');
    bPlace.onclick = async () => {
        store.dragFromStorageTag = null;
        store.dragFromStorageStartPose = null;
        clearStoreToSlotMode();
        const tx = parseFloat(xInp.value);
        const ty = parseFloat(yInp.value);
        const trot = parseFloat(rInp.value);
        if (!Number.isFinite(tx) || !Number.isFinite(ty) || !Number.isFinite(trot)) {
            hooks.log('Invalid coordinates.', 'error');
            return;
        }
        if (isStorageRegion(tx, ty)) {
            hooks.log('Target must be outside the storage rectangle.', 'error');
            return;
        }
        await dispatchPrimitive(hooks, {
            action: 'PLACE_FROM_STORAGE',
            target_id: tagId,
            parameters: { target_x: tx, target_y: ty, rotation: trot },
        });
        if (typeof render === 'function') render();
    };
    body.appendChild(bPlace);

    const bRecenter = secondaryButton('Re-center (ArUco top → +X)', 'center_focus_strong');
    bRecenter.onclick = async () => {
        store.dragFromStorageTag = null;
        store.dragFromStorageStartPose = null;
        // HTTP edges often stay IDLE for the whole POST; without force sync the
        // canvas ghost keeps the post-Refresh off-center pose even after commit.
        store.forceGhostSync = true;
        await dispatchPrimitive(hooks, {
            action: 'RECENTER_IN_STORAGE',
            target_id: tagId,
            parameters: {},
        });
        if (typeof render === 'function') render();
        if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
    };
    body.appendChild(bRecenter);

    const bDrag = secondaryButton(
        store.dragFromStorageTag === tagId ? 'Drag mode — pull on canvas' : 'Drag from storage',
        'touch_app',
    );
    if (store.dragFromStorageTag === tagId) {
        bDrag.disabled = true;
        const bCancel = secondaryButton('Cancel drag mode', 'close');
        bCancel.onclick = () => {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            hooks.log('Drag from storage cancelled.', 'info');
            if (typeof render === 'function') render();
            if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
        };
        body.appendChild(bDrag);
        body.appendChild(bCancel);
    } else {
        bDrag.onclick = () => {
            clearStoreToSlotMode();
            store.dragFromStorageTag = tagId;
            hooks.log('Drag mode: pull part onto breadboard, then place.', 'info');
            if (typeof render === 'function') render();
            if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
        };
        body.appendChild(bDrag);
    }

    return section;
}

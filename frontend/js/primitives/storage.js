import { store } from '../state/store.js';
import { isStorageRegion } from '../storage-region.js';
import { drawPose } from '../component-model.js';
import { tunableValue } from '../component-state.js';
import { coordInput, dispatchPrimitive, primitiveRegion, runButton, secondaryButton } from './shared.js';

function placeFromStorageSeedPose(tagId, comp) {
    const saved = tunableValue(comp, 'last_breadboard_pose');
    if (
        saved
        && typeof saved === 'object'
        && Number.isFinite(Number(saved.x))
        && Number.isFinite(Number(saved.y))
    ) {
        return {
            x: Number(saved.x),
            y: Number(saved.y),
            rotation: Number(saved.rotation ?? 0),
        };
    }
    const cached = store.lastBreadboardPose?.[tagId];
    if (
        cached
        && Number.isFinite(Number(cached.x))
        && Number.isFinite(Number(cached.y))
    ) {
        return {
            x: Number(cached.x),
            y: Number(cached.y),
            rotation: Number(cached.rotation ?? 0),
        };
    }
    const ghost = store.ghostState?.[tagId];
    if (
        ghost
        && Number.isFinite(Number(ghost.x))
        && Number.isFinite(Number(ghost.y))
        && !isStorageRegion(Number(ghost.x), Number(ghost.y))
    ) {
        return {
            x: Number(ghost.x),
            y: Number(ghost.y),
            rotation: Number(ghost.rotation ?? 0),
        };
    }
    return drawPose(comp) || {};
}

export function renderStoreComponent(ctx) {
    const { tagId, placementState, hooks, comp } = ctx;
    if (placementState !== 'PLACED') return null;
    const { section, body } = primitiveRegion('STORE_COMPONENT', 'STORE COMPONENT');
    const btn = secondaryButton('Move to storage (auto pack)', 'inventory_2');
    btn.onclick = () => {
        const pose = drawPose(ctx.comp);
        if (pose && Number.isFinite(Number(pose.x)) && Number.isFinite(Number(pose.y))) {
            store.lastBreadboardPose[tagId] = {
                x: Number(pose.x),
                y: Number(pose.y),
                rotation: Number(pose.rotation ?? 0),
            };
        }
        void dispatchPrimitive(hooks, {
            action: 'STORE_COMPONENT',
            target_id: tagId,
            parameters: {},
        });
    };
    body.appendChild(btn);
    return section;
}

export function renderPlaceFromStorage(ctx) {
    const { tagId, placementState, hooks, render, updateContextPanel, comp } = ctx;
    if (placementState !== 'STORED') return null;

    const { section, body } = primitiveRegion('PLACE_FROM_STORAGE', 'PLACE FROM STORAGE');
    const p = placeFromStorageSeedPose(tagId, comp) || {};
    const grid = document.createElement('div');
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = '1fr 1fr 1fr';
    grid.style.gap = '6px';
    const xInp = coordInput('X mm', p.x);
    const yInp = coordInput('Y mm', p.y);
    const rInp = coordInput('Rot °', p.rotation ?? 0);
    grid.appendChild(xInp);
    grid.appendChild(yInp);
    grid.appendChild(rInp);
    body.appendChild(grid);

    const bPlace = runButton('Place from storage', 'north_east');
    bPlace.onclick = async () => {
        store.dragFromStorageTag = null;
        store.dragFromStorageStartPose = null;
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

    const bRecenter = secondaryButton('Re-center in cell (0°)', 'center_focus_strong');
    bRecenter.onclick = () => {
        store.dragFromStorageTag = null;
        store.dragFromStorageStartPose = null;
        void dispatchPrimitive(hooks, {
            action: 'RECENTER_IN_STORAGE',
            target_id: tagId,
            parameters: {},
        });
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
            store.dragFromStorageTag = tagId;
            hooks.log('Drag mode: pull part onto breadboard, then place.', 'info');
            if (typeof render === 'function') render();
            if (typeof updateContextPanel === 'function') updateContextPanel(tagId);
        };
        body.appendChild(bDrag);
    }

    return section;
}

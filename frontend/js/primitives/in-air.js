import { store } from '../state/store.js';
import { isStorageRegion } from '../storage-region.js';
import {
    getHolding,
    isHeldTag,
    isHoldingState,
    isHoldingUnconfirmed,
    isOnTableComponent,
} from '../component-model.js';
import { coordInput, dispatchPrimitive, primitiveRegion, runButton, secondaryButton } from './shared.js';
function poseInputs(ctx) {
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
    return { grid, xInp, yInp, rInp };
}

export function renderPickComponent(ctx) {
    const { tagId, comp, placementState, hooks } = ctx;
    if (!comp || !isOnTableComponent(comp) || placementState === 'STORED') return null;
    const labState = store.labState || {};
    if (isHoldingState(labState)) return null;

    const { section, body } = primitiveRegion('PICK_COMPONENT', 'PICK COMPONENT');
    const btn = secondaryButton('Pick up (start HOLDING)', 'pan_tool');
    btn.onclick = () =>
        void dispatchPrimitive(hooks, {
            action: 'PICK_COMPONENT',
            target_id: tagId,
            parameters: {},
        });
    body.appendChild(btn);
    return section;
}

export function renderConfirmHolding(ctx) {
    const { tagId, hooks } = ctx;
    const labState = store.labState || {};
    if (!isHoldingUnconfirmed(labState)) return null;

    const { section, body } = primitiveRegion('CONFIRM_HOLDING_TAG', 'CONFIRM HOLDING');
    const p = document.createElement('p');
    p.style.fontSize = '10px';
    p.style.color = '#fca5a5';
    p.style.lineHeight = '1.35';
    p.style.margin = '0 0 6px 0';
    p.textContent =
        'Gripper closed on startup. Confirm the tag in the gripper before other commands.';
    body.appendChild(p);
    const btn = runButton(`Confirm held: ${tagId}`, 'verified');
    btn.onclick = () =>
        void dispatchPrimitive(hooks, {
            action: 'CONFIRM_HOLDING_TAG',
            target_id: tagId,
            parameters: {},
        });
    body.appendChild(btn);
    return section;
}

export function renderHover(ctx) {
    const { tagId, hooks } = ctx;
    const labState = store.labState || {};
    if (!isHoldingState(labState) || !isHeldTag(tagId, labState)) return null;

    const hld = getHolding(labState);
    const currentZ =
        (hld.nominal_pose && Number.isFinite(Number(hld.nominal_pose.z)))
            ? Number(hld.nominal_pose.z)
            : 40.0;

    const { section, body } = primitiveRegion('HOVER', 'HOVER');
    const { grid, xInp, yInp, rInp } = poseInputs(ctx);
    body.appendChild(grid);
    const zInp = coordInput('Z (mm)', currentZ);
    body.appendChild(zInp);

    const btn = runButton('Hover to X/Y/Rot/Z', 'open_with');
    btn.onclick = async () => {
        const tx = parseFloat(xInp.value);
        const ty = parseFloat(yInp.value);
        const trot = parseFloat(rInp.value);
        const tz = parseFloat(zInp.value);
        if (![tx, ty, trot, tz].every(Number.isFinite)) {
            hooks.log('Invalid coordinates for HOVER.', 'error');
            return;
        }
        await dispatchPrimitive(hooks, {
            action: 'HOVER',
            target_id: tagId,
            parameters: { target_x: tx, target_y: ty, rotation: trot, z: tz },
        });
    };
    body.appendChild(btn);
    return section;
}

export function renderPlaceFromHover(ctx) {
    const { tagId, hooks } = ctx;
    const labState = store.labState || {};
    if (!isHoldingState(labState) || !isHeldTag(tagId, labState)) return null;

    const { section, body } = primitiveRegion('PLACE_FROM_HOVER', 'PLACE FROM HOVER');
    const { grid, xInp, yInp, rInp } = poseInputs(ctx);
    body.appendChild(grid);

    const btn = runButton('Place from hover', 'south_east');
    btn.onclick = async () => {
        const tx = parseFloat(xInp.value);
        const ty = parseFloat(yInp.value);
        const trot = parseFloat(rInp.value);
        if (![tx, ty, trot].every(Number.isFinite)) {
            hooks.log('Invalid coordinates.', 'error');
            return;
        }
        if (isStorageRegion(tx, ty)) {
            hooks.log('Place target must be outside the storage quadrant.', 'error');
            return;
        }
        await dispatchPrimitive(hooks, {
            action: 'PLACE_FROM_HOVER',
            target_id: tagId,
            parameters: { target_x: tx, target_y: ty, rotation: trot },
        });
    };
    body.appendChild(btn);
    return section;
}

export function renderHoldingNotice(ctx) {
    const { tagId } = ctx;
    const labState = store.labState || {};
    if (!isHoldingState(labState) || isHeldTag(tagId, labState) || isHoldingUnconfirmed(labState)) {
        return null;
    }
    const held = getHolding(labState).tag_id;
    const { section, body } = primitiveRegion('HOLDING_NOTICE', 'HOLDING');
    const p = document.createElement('p');
    p.style.fontSize = '10px';
    p.style.color = '#c4b5fd';
    p.style.margin = '0';
    p.textContent = `Robot is holding ${held || 'another part'}. Release before interacting with ${tagId}.`;
    body.appendChild(p);
    return section;
}

import { coordInput, dispatchPrimitive, primitiveRegion, runButton } from './shared.js';
import { store } from '../state/store.js';
import { isHeldTag } from '../component-model.js';

export function renderMoveComponent(ctx) {
    const { tagId, hooks, checkCollision, render } = ctx;
    // In-air only HOVER / PLACE_FROM_HOVER (and related) apply; table MOVE is invalid while held.
    if (isHeldTag(tagId, store.labState || {})) return null;

    const pose = (store.ghostState && store.ghostState[tagId]) || ctx.getPose?.() || {};

    const { section, body } = primitiveRegion('MOVE_COMPONENT', 'MOVE COMPONENT');
    const grid = document.createElement('div');
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = '1fr 1fr 1fr';
    grid.style.gap = '6px';

    const xInp = coordInput('X (mm)', pose.x, 2);
    const yInp = coordInput('Y (mm)', pose.y, 2);
    const rInp = coordInput('θ (°)', pose.rotation ?? 0, 2);
    grid.appendChild(xInp);
    grid.appendChild(yInp);
    grid.appendChild(rInp);
    body.appendChild(grid);

    const btn = runButton('Move on table', 'open_with');
    btn.onclick = async () => {
        const tx = parseFloat(xInp.value);
        const ty = parseFloat(yInp.value);
        const trot = parseFloat(rInp.value);
        if (![tx, ty, trot].every(Number.isFinite)) {
            hooks.log('Invalid coordinates for MOVE_COMPONENT.', 'error');
            return;
        }
        if (typeof checkCollision === 'function') {
            const collision = checkCollision(tagId, tx, ty, { rotation: trot });
            if (collision.detected) {
                hooks.log(`Move cancelled: collision with ${collision.other}`, 'error');
                return;
            }
        }
        if (store.ghostState && store.ghostState[tagId]) {
            store.ghostState[tagId].x = tx;
            store.ghostState[tagId].y = ty;
            store.ghostState[tagId].rotation = trot;
        }
        await dispatchPrimitive(hooks, {
            action: 'MOVE_COMPONENT',
            target_id: tagId,
            parameters: { target_x: tx, target_y: ty, rotation: trot },
        });
        if (typeof render === 'function') render();
    };
    body.appendChild(btn);
    return section;
}

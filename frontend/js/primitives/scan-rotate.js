import { coordInput, dispatchPrimitive, primitiveRegion, runButton } from './shared.js';

export function renderScanRotate(ctx, { contextHint = 'placed' } = {}) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion(
        'SCAN_ROTATE_IN_PLACE',
        'SCAN ROTATE IN PLACE',
    );

    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 6px 0';
    hint.style.lineHeight = '1.35';
    hint.textContent =
        contextHint === 'placed'
            ? 'Sweeps θ at constant rate (placed or held — backend decides).'
            : 'Rotates the held part in-air while XY + Z stay at the hover pose.';
    body.appendChild(hint);

    const grid = document.createElement('div');
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = '1fr 1fr 1fr';
    grid.style.gap = '6px';
    const tMin = coordInput('θ min°', -45);
    const tMax = coordInput('θ max°', 45);
    const spd = coordInput('deg/s', 30);
    grid.appendChild(tMin);
    grid.appendChild(tMax);
    grid.appendChild(spd);
    body.appendChild(grid);

    const btn = runButton('Start scan rotate', 'rotate_right');
    btn.onclick = async () => {
        const thetaMin = parseFloat(tMin.value);
        const thetaMax = parseFloat(tMax.value);
        const speed = parseFloat(spd.value);
        if (![thetaMin, thetaMax, speed].every(Number.isFinite) || !(speed > 0)) {
            hooks.log('Invalid scan params (need numbers; speed > 0).', 'error');
            return;
        }
        await dispatchPrimitive(hooks, {
            action: 'SCAN_ROTATE_IN_PLACE',
            target_id: tagId,
            parameters: {
                theta_min: thetaMin,
                theta_max: thetaMax,
                speed_deg_per_s: speed,
                axis: 'z',
            },
        });
    };
    body.appendChild(btn);
    return section;
}

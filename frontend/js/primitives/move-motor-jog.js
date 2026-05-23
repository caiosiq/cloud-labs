import { dispatchPrimitive, primitiveRegion, secondaryButton } from './shared.js';
import { measPose } from '../component-model.js';

export function renderMoveMotorJog(ctx) {
    const { tagId, comp, catalogRow, hooks } = ctx;
    const motorIds = catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;

    const step = 2.5;
    const tracked = (measPose(comp).motor_rotations) || {};

    const { section, body } = primitiveRegion('MOVE_MOTOR', 'MOVE MOTOR (relative jog)');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 4px 0';
    hint.textContent = `Relative jog in ±${step}° steps (does not change nominal setpoint).`;
    body.appendChild(hint);

    motorIds.forEach((mid) => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.gap = '6px';
        row.style.flexWrap = 'wrap';

        const label = document.createElement('span');
        label.textContent = `M${mid}`;
        label.style.fontSize = '11px';
        label.style.minWidth = '28px';

        const theta = document.createElement('span');
        theta.dataset.motorAngle = `${tagId}:${mid}`;
        const tr = tracked[String(mid)];
        const trN = Number.isFinite(Number(tr)) ? Number(tr) : 0;
        theta.style.fontSize = '10px';
        theta.style.color = '#64748b';
        theta.style.fontFamily = 'ui-monospace, monospace';
        theta.textContent = `θ ${trN.toFixed(2)}°`;

        const bRev = secondaryButton('', 'remove');
        bRev.style.width = 'auto';
        bRev.style.padding = '6px 10px';
        bRev.title = `Jog −${step}°`;
        bRev.onclick = () =>
            void dispatchPrimitive(hooks, {
                action: 'MOVE_MOTOR',
                target_id: tagId,
                parameters: { motor_id: Number(mid), distance: -step },
            });

        const bFwd = secondaryButton('', 'add');
        bFwd.style.width = 'auto';
        bFwd.style.padding = '6px 10px';
        bFwd.title = `Jog +${step}°`;
        bFwd.onclick = () =>
            void dispatchPrimitive(hooks, {
                action: 'MOVE_MOTOR',
                target_id: tagId,
                parameters: { motor_id: Number(mid), distance: step },
            });

        row.appendChild(label);
        row.appendChild(theta);
        row.appendChild(bRev);
        row.appendChild(bFwd);
        body.appendChild(row);
    });

    return section;
}

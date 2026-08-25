import { primitiveRegion, secondaryButton } from './shared.js';
import {
    createMotorActionStatusEl,
    dispatchMotorJog,
    MOTOR_ACTION,
} from '../ui/motor-action-ui.js';

export function renderMoveMotorJog(ctx) {
    const { tagId, catalogRow, hooks } = ctx;
    const motorIds = catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;

    const step = 2.5;

    const { section, body } = primitiveRegion('MOVE_MOTOR', 'MOVE MOTOR (relative jog)');
    const hint = document.createElement('p');
    hint.style.fontSize = 'var(--text-xs)';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 4px 0';
    hint.textContent = `Relative jog in ±${step}° steps; updates nominal_motor_positions to match.`;
    body.appendChild(hint);

    motorIds.forEach((mid) => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.gap = '6px';
        row.style.flexWrap = 'wrap';

        const label = document.createElement('span');
        label.textContent = `M${mid}`;
        label.style.fontSize = 'var(--text-sm)';
        label.style.minWidth = '28px';

        const bRev = secondaryButton('', 'remove');
        bRev.style.width = 'auto';
        bRev.style.padding = '6px 10px';
        bRev.title = `Jog −${step}°`;
        bRev.onclick = () => void dispatchMotorJog(hooks, tagId, mid, -step);

        const bFwd = secondaryButton('', 'add');
        bFwd.style.width = 'auto';
        bFwd.style.padding = '6px 10px';
        bFwd.title = `Jog +${step}°`;
        bFwd.onclick = () => void dispatchMotorJog(hooks, tagId, mid, step);

        const statusEl = createMotorActionStatusEl(MOTOR_ACTION.JOG, tagId, mid);

        row.appendChild(label);
        row.appendChild(bRev);
        row.appendChild(bFwd);
        row.appendChild(statusEl);
        body.appendChild(row);
    });

    return section;
}

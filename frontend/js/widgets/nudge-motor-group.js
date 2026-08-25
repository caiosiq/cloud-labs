/**
 * `NudgeMotorGroup` — tunable widget for ``nominal_motor_positions`` (§14.1).
 *
 * Renders one row per motor declared in ``descriptor.motor_ids`` with
 * ± buttons that dispatch ``MOVE_MOTOR`` at ``step_deg``.
 */
import { widgetCard, widgetTitle } from './common.js';
import {
    createMotorActionStatusEl,
    dispatchMotorJog,
    MOTOR_ACTION,
} from '../ui/motor-action-ui.js';
export default function NudgeMotorGroup({ tagId, fieldName, descriptor, comp, hooks }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const motorIds = Array.isArray(descriptor && descriptor.motor_ids)
        ? descriptor.motor_ids
        : [];
    const step = Number.isFinite(Number(descriptor && descriptor.step_deg))
        ? Number(descriptor.step_deg)
        : 2.5;

    if (!motorIds.length) {
        const el = document.createElement('div');
        el.style.fontStyle = 'italic';
        el.style.color = '#475569';
        el.style.fontSize = 'var(--text-sm)';
        el.textContent = '\u2014 no motor_ids declared in catalog';
        card.appendChild(el);
        return card;
    }

    motorIds.forEach((mid) => {
        const r = document.createElement('div');
        r.style.display = 'grid';
        r.style.gridTemplateColumns = 'auto auto auto auto';
        r.style.gap = '6px';
        r.style.alignItems = 'center';
        r.style.marginTop = '4px';

        const label = document.createElement('span');
        label.style.color = '#64748b';
        label.style.fontSize = 'var(--text-xs)';
        label.textContent = `M${mid}`;
        r.appendChild(label);

        const minus = document.createElement('button');
        minus.type = 'button';
        minus.className = 'btn btn-secondary';
        minus.style.fontSize = 'var(--text-xs)';
        minus.style.padding = '2px 6px';
        minus.title = `MOVE_MOTOR ${tagId} motor=${mid} \u0394=-${step}\u00b0`;
        minus.textContent = `\u2212${step}\u00b0`;
        minus.onclick = () => {
            if (hooks) void dispatchMotorJog(hooks, tagId, mid, -step);
        };
        r.appendChild(minus);

        const plus = document.createElement('button');
        plus.type = 'button';
        plus.className = 'btn btn-secondary';
        plus.style.fontSize = 'var(--text-xs)';
        plus.style.padding = '2px 6px';
        plus.title = `MOVE_MOTOR ${tagId} motor=${mid} \u0394=+${step}\u00b0`;
        plus.textContent = `+${step}\u00b0`;
        plus.onclick = () => {
            if (hooks) void dispatchMotorJog(hooks, tagId, mid, step);
        };
        r.appendChild(plus);

        const statusEl = createMotorActionStatusEl(MOTOR_ACTION.JOG, tagId, mid);
        r.appendChild(statusEl);

        card.appendChild(r);
    });

    return card;
}

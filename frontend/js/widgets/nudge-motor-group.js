/**
 * `NudgeMotorGroup` — tunable widget for ``nominal_motor_positions`` (§14.1).
 *
 * Renders one row per motor declared in ``descriptor.motor_ids`` with
 * ± buttons that dispatch ``MOVE_MOTOR`` commands at the descriptor's
 * ``step_deg`` increment. Read-only target value is taken from the
 * component's current ``tunables.nominal_pose.motor_rotations`` (the
 * lab's intent, not the encoder reading).
 *
 * Wiring: the widget calls ``hooks.sendCommand`` which is the same
 * dispatcher the rest of the UI uses. No new API surface introduced.
 */
import { widgetCard, widgetTitle, fmtNum } from './common.js';

export default function NudgeMotorGroup({ tagId, fieldName, descriptor, comp, hooks }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const motorIds = Array.isArray(descriptor && descriptor.motor_ids)
        ? descriptor.motor_ids
        : [];
    const step = Number.isFinite(Number(descriptor && descriptor.step_deg))
        ? Number(descriptor.step_deg)
        : 2.5;

    const targetMotors =
        (comp && comp.tunables && comp.tunables.nominal_pose && comp.tunables.nominal_pose.motor_rotations) || {};

    if (!motorIds.length) {
        const el = document.createElement('div');
        el.style.fontStyle = 'italic';
        el.style.color = '#475569';
        el.style.fontSize = '11px';
        el.textContent = '\u2014 no motor_ids declared in catalog';
        card.appendChild(el);
        return card;
    }

    const sendMove = async (mid, deltaDeg) => {
        if (!hooks || typeof hooks.sendCommand !== 'function') return;
        try {
            await hooks.sendCommand({
                action: 'MOVE_MOTOR',
                target_id: tagId,
                parameters: { motor_id: mid, distance: deltaDeg },
            });
        } catch (e) {
            if (hooks.log) hooks.log(`MOVE_MOTOR failed: ${(e && e.message) || e}`, 'error');
        }
    };

    motorIds.forEach((mid) => {
        const r = document.createElement('div');
        r.style.display = 'grid';
        r.style.gridTemplateColumns = 'auto 1fr auto auto';
        r.style.gap = '6px';
        r.style.alignItems = 'center';
        r.style.marginTop = '4px';

        const label = document.createElement('span');
        label.style.color = '#64748b';
        label.style.fontSize = '10px';
        label.textContent = `\u03b8_${mid}`;
        r.appendChild(label);

        const val = document.createElement('span');
        val.style.color = '#cbd5e1';
        val.style.fontFamily = 'ui-monospace, monospace';
        val.style.fontSize = '11px';
        val.style.textAlign = 'right';
        const cur = Number(targetMotors[mid]);
        val.textContent = `${fmtNum(Number.isFinite(cur) ? cur : 0, 2)}\u00b0`;
        r.appendChild(val);

        const minus = document.createElement('button');
        minus.type = 'button';
        minus.className = 'btn btn-secondary';
        minus.style.fontSize = '10px';
        minus.style.padding = '2px 6px';
        minus.title = `MOVE_MOTOR ${tagId} motor=${mid} \u0394=-${step}\u00b0`;
        minus.textContent = `\u2212${step}\u00b0`;
        minus.onclick = () => sendMove(mid, -step);
        r.appendChild(minus);

        const plus = document.createElement('button');
        plus.type = 'button';
        plus.className = 'btn btn-secondary';
        plus.style.fontSize = '10px';
        plus.style.padding = '2px 6px';
        plus.title = `MOVE_MOTOR ${tagId} motor=${mid} \u0394=+${step}\u00b0`;
        plus.textContent = `+${step}\u00b0`;
        plus.onclick = () => sendMove(mid, step);
        r.appendChild(plus);

        card.appendChild(r);
    });

    return card;
}

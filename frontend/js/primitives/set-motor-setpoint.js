import { coordInput, primitiveRegion, runButton } from './shared.js';
import { nominalMotorPositions } from '../component-model.js';
import { store } from '../state/store.js';
import {
    createMotorActionStatusEl,
    dispatchMotorCommand,
    MOTOR_ACTION,
    motorActionKey,
    paintMotorActionStatus,
    resolveMotorSetpointInputValue,
} from '../ui/motor-action-ui.js';

export function renderSetMotorSetpoint(ctx) {
    const { tagId, comp, catalogRow, hooks } = ctx;
    const motorIds = catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;

    const nominal = nominalMotorPositions(comp);

    const { section, body } = primitiveRegion('SET_MOTOR_SETPOINT', 'SET MOTOR SETPOINT');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 4px 0';
    hint.style.lineHeight = '1.35';
    hint.textContent =
        'Commits nominal_motor_positions and jogs hardware to the set angle.';
    body.appendChild(hint);

    motorIds.forEach((mid) => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.gap = '8px';
        row.style.flexWrap = 'wrap';

        const label = document.createElement('span');
        label.textContent = `M${mid}`;
        label.style.fontSize = '11px';
        label.style.color = '#cbd5e1';
        label.style.minWidth = '28px';

        const nom = nominal[String(mid)];
        const defaultAngle = resolveMotorSetpointInputValue(tagId, mid, nom);
        const inp = coordInput('setpoint °', defaultAngle);
        inp.style.flex = '1';
        inp.style.minWidth = '72px';

        const btn = runButton('Apply', 'adjust');
        btn.style.width = 'auto';
        btn.style.flex = '0 0 auto';

        const statusEl = createMotorActionStatusEl(MOTOR_ACTION.SETPOINT, tagId, mid);

        btn.onclick = async () => {
            const v = parseFloat(inp.value);
            if (!Number.isFinite(v)) {
                hooks.log('Invalid motor setpoint angle.', 'error');
                return;
            }
            inp.value = String(v);
            btn.disabled = true;

            const result = await dispatchMotorCommand(
                hooks,
                {
                    action: 'SET_MOTOR_SETPOINT',
                    target_id: tagId,
                    parameters: { motor_id: Number(mid), angle_deg: v },
                },
                MOTOR_ACTION.SETPOINT,
                tagId,
                mid,
                { value: v },
            );

            btn.disabled = false;
            if (!result?.ok && hooks.log) {
                hooks.log(`Setpoint failed: ${result?.error || 'unknown'}`, 'warn');
            }
        };

        inp.addEventListener('input', () => {
            const key = motorActionKey(MOTOR_ACTION.SETPOINT, tagId, mid);
            if (store.motorActionApply[key]) {
                delete store.motorActionApply[key];
                paintMotorActionStatus(MOTOR_ACTION.SETPOINT, tagId, mid);
            }
        });

        row.appendChild(label);
        row.appendChild(inp);
        row.appendChild(btn);
        row.appendChild(statusEl);
        body.appendChild(row);
    });

    return section;
}

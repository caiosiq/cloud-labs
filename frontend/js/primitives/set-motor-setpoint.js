import { coordInput, dispatchPrimitive, primitiveRegion, runButton } from './shared.js';
import { measPose } from '../component-model.js';

export function renderSetMotorSetpoint(ctx) {
    const { tagId, comp, catalogRow, hooks } = ctx;
    const motorIds = catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;

    const nominal =
        (comp?.tunables?.nominal_motor_positions && typeof comp.tunables.nominal_motor_positions === 'object')
            ? comp.tunables.nominal_motor_positions
            : {};
    const tracked = (measPose(comp).motor_rotations) || {};

    const { section, body } = primitiveRegion('SET_MOTOR_SETPOINT', 'SET MOTOR SETPOINT');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 4px 0';
    hint.style.lineHeight = '1.35';
    hint.textContent =
        'Commits nominal_motor_positions and jogs hardware by the delta from tracked θ.';
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

        const theta = document.createElement('span');
        theta.dataset.motorAngle = `${tagId}:${mid}`;
        const tr = tracked[String(mid)];
        const trN = Number.isFinite(Number(tr)) ? Number(tr) : 0;
        theta.style.fontSize = '10px';
        theta.style.color = '#64748b';
        theta.style.fontFamily = 'ui-monospace, monospace';
        theta.textContent = `θ ${trN.toFixed(2)}°`;

        const nom = nominal[String(mid)];
        const defaultAngle = Number.isFinite(Number(nom)) ? Number(nom) : trN;
        const inp = coordInput('setpoint °', defaultAngle);
        inp.style.flex = '1';
        inp.style.minWidth = '72px';

        const btn = runButton('Apply', 'adjust');
        btn.style.width = 'auto';
        btn.style.flex = '0 0 auto';
        btn.onclick = async () => {
            const v = parseFloat(inp.value);
            if (!Number.isFinite(v)) {
                hooks.log('Invalid motor setpoint angle.', 'error');
                return;
            }
            await dispatchPrimitive(hooks, {
                action: 'SET_MOTOR_SETPOINT',
                target_id: tagId,
                parameters: { motor_id: Number(mid), angle_deg: v },
            });
        };

        row.appendChild(label);
        row.appendChild(theta);
        row.appendChild(inp);
        row.appendChild(btn);
        body.appendChild(row);
    });

    return section;
}

import { primitiveRegion, secondaryButton } from './shared.js';
import { dispatchMotorCommand, MOTOR_ACTION } from '../ui/motor-action-ui.js';

function motorRow(ctx, primitiveId, title, label, icon, action) {
    const motorIds = ctx.catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;
    const { section, body } = primitiveRegion(primitiveId, title);
    motorIds.forEach((mid) => {
        const btn = secondaryButton(`${label} M${mid}`, icon);
        btn.onclick = () =>
            void dispatchMotorCommand(
                ctx.hooks,
                {
                    action,
                    target_id: ctx.tagId,
                    parameters: { motor_id: Number(mid) },
                },
                MOTOR_ACTION.JOG,
                ctx.tagId,
                mid,
            );
        body.appendChild(btn);
    });
    return section;
}

export function renderMotorSendHome(ctx) {
    return motorRow(
        ctx,
        'MOTOR_SEND_HOME',
        'MOTOR SEND HOME',
        'Send home',
        'home',
        'MOTOR_SEND_HOME',
    );
}

export function renderMotorSetZero(ctx) {
    return motorRow(
        ctx,
        'MOTOR_SET_ZERO',
        'MOTOR SET ZERO',
        'Set 0 at',
        'flag',
        'MOTOR_SET_ZERO',
    );
}

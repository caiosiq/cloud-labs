import { dispatchPrimitive, primitiveRegion, secondaryButton } from './shared.js';

function motorRow(ctx, primitiveId, title, label, icon, onRun) {
    const motorIds = ctx.catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;
    const { section, body } = primitiveRegion(primitiveId, title);
    motorIds.forEach((mid) => {
        const btn = secondaryButton(`${label} M${mid}`, icon);
        btn.onclick = () => void onRun(mid);
        body.appendChild(btn);
    });
    return section;
}

export function renderMotorSendHome(ctx) {
    return motorRow(ctx, 'MOTOR_SEND_HOME', 'MOTOR SEND HOME', 'Send home', 'home', (mid) =>
        dispatchPrimitive(ctx.hooks, {
            action: 'MOTOR_SEND_HOME',
            target_id: ctx.tagId,
            parameters: { motor_id: Number(mid) },
        }),
    );
}

export function renderMotorSetZero(ctx) {
    return motorRow(ctx, 'MOTOR_SET_ZERO', 'MOTOR SET ZERO', 'Set 0 at', 'flag', (mid) =>
        dispatchPrimitive(ctx.hooks, {
            action: 'MOTOR_SET_ZERO',
            target_id: ctx.tagId,
            parameters: { motor_id: Number(mid) },
        }),
    );
}

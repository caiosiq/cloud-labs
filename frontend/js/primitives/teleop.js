import { primitiveRegion, sessionStartButton, sessionEndButton, afterCommandDispatch } from './shared.js';
import { startTeleop, endTeleop } from '../api/teleop.js';

export function renderStartTeleop(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('START_TELEOP', 'START TELEOP', { accent: 'teleop' });
    const btn = sessionStartButton('teleop', 'Acquire teleop lease', 'gamepad');
    btn.onclick = () => {
        btn.disabled = true;
        void startTeleop(tagId)
            .then(async (result) => {
                if (!result.ok) {
                    hooks.log(`Teleop start failed: ${result.error || 'unknown'}`, 'error');
                    return;
                }
                await afterCommandDispatch(hooks, tagId);
            })
            .finally(() => {
                btn.disabled = false;
            });
    };
    body.appendChild(btn);
    return section;
}

export function renderEndTeleop(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('END_TELEOP', 'END TELEOP', { accent: 'teleop', active: true });
    const btn = sessionEndButton('teleop', 'Release teleop lease', 'stop_circle');
    btn.onclick = () => {
        btn.disabled = true;
        void endTeleop(tagId)
            .then(async (result) => {
                if (!result.ok) {
                    hooks.log(`Teleop end failed: ${result.error || 'unknown'}`, 'error');
                    return;
                }
                await afterCommandDispatch(hooks, tagId);
            })
            .finally(() => {
                btn.disabled = false;
            });
    };
    body.appendChild(btn);
    return section;
}

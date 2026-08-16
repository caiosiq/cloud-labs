import { primitiveRegion, sessionStartButton, sessionEndButton, afterCommandDispatch } from './shared.js';
import { startTeleop, endTeleop } from '../api/teleop.js';

export function renderStartTeleop(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('START_TELEOP', 'START TELEOP', { accent: 'teleop' });
    const btn = sessionStartButton('teleop', 'Acquire teleop lease', 'gamepad');
    btn.onclick = () => {
        btn.disabled = true;
        // Rebuild panel immediately after optimistic pending patch inside startTeleop.
        void (async () => {
            const startPromise = startTeleop(tagId);
            // Yield so optimistic active/!ready is applied, then show Loading UI.
            await Promise.resolve();
            if (typeof hooks.resetPanelSnapshot === 'function') hooks.resetPanelSnapshot();
            if (typeof hooks.refreshPanel === 'function') hooks.refreshPanel(tagId);
            if (typeof hooks.render === 'function') hooks.render();
            const result = await startPromise;
            if (!result.ok) {
                if (result.error !== 'cancelled') {
                    hooks.log(`Teleop start failed: ${result.error || 'unknown'}`, 'error');
                }
            }
            await afterCommandDispatch(hooks, tagId);
        })().finally(() => {
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
                    if (result.error !== 'cancelled') {
                        hooks.log(`Teleop end failed: ${result.error || 'unknown'}`, 'error');
                    }
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

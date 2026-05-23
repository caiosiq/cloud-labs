import { dispatchPrimitive, primitiveRegion, runButton, secondaryButton } from './shared.js';

export function renderStartTeleop(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('START_TELEOP', 'START TELEOP');
    const btn = runButton('Acquire teleop lease', 'gamepad');
    btn.onclick = () =>
        void fetch(`/api/components/${encodeURIComponent(tagId)}/teleop/start`, {
            method: 'POST',
        }).then(async (r) => {
            if (!r.ok) {
                const data = await r.json().catch(() => ({}));
                hooks.log(`Teleop start failed: ${JSON.stringify(data.detail || r.status)}`, 'error');
                return;
            }
            if (typeof hooks.fetchLabState === 'function') await hooks.fetchLabState();
            if (typeof hooks.refreshPanel === 'function') hooks.refreshPanel(tagId);
        });
    body.appendChild(btn);
    return section;
}

export function renderEndTeleop(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('END_TELEOP', 'END TELEOP');
    const btn = secondaryButton('Release teleop lease', 'stop_circle');
    btn.onclick = () =>
        void fetch(`/api/components/${encodeURIComponent(tagId)}/teleop/end`, {
            method: 'POST',
        }).then(async (r) => {
            if (!r.ok) {
                const data = await r.json().catch(() => ({}));
                hooks.log(`Teleop end failed: ${JSON.stringify(data.detail || r.status)}`, 'error');
                return;
            }
            if (typeof hooks.fetchLabState === 'function') await hooks.fetchLabState();
            if (typeof hooks.refreshPanel === 'function') hooks.refreshPanel(tagId);
        });
    body.appendChild(btn);
    return section;
}

export function renderTeleopJog(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('TELEOP_JOG', 'TELEOP JOG');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0';
    hint.textContent =
        'Use the TeleopJog widget in telemetry when teleop is active, or POST jog frames via the API.';
    body.appendChild(hint);
    return section;
}

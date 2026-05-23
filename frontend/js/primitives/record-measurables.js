import { primitiveRegion, runButton } from './shared.js';

export function renderRecordMeasurables(ctx) {
    const { tagId } = ctx;
    const { section, body } = primitiveRegion('RECORD_MEASURABLES', 'RECORD MEASURABLES');
    const status = document.createElement('span');
    status.style.fontSize = '10px';
    status.style.color = '#94a3b8';

    const btn = runButton('Capture measurables', 'photo_camera');
    btn.onclick = async () => {
        status.textContent = '…';
        btn.disabled = true;
        try {
            const r = await fetch(
                `/api/components/${encodeURIComponent(tagId)}/measurables/record`,
                { method: 'POST' },
            );
            const data = await r.json().catch(() => ({}));
            if (!r.ok) {
                const det = data.detail !== undefined ? data.detail : r.status;
                const msg = typeof det === 'string' ? det : JSON.stringify(det);
                ctx.hooks.log(`Record failed: ${msg}`, 'error');
                status.textContent = 'Failed';
                return;
            }
            status.textContent = 'OK';
            const ci = data.measurables && data.measurables.camera_image;
            if (ci && typeof ci === 'object' && ci.path) {
                status.textContent = `OK · ${String(ci.path).replace(/^.*[/\\\\]/, '')}`;
            }
            if (typeof ctx.hooks.fetchLabState === 'function') {
                await ctx.hooks.fetchLabState();
            }
            if (typeof ctx.hooks.refreshPanel === 'function') {
                ctx.hooks.refreshPanel(tagId);
            }
        } catch (e) {
            ctx.hooks.log(`Record error: ${e && e.message ? e.message : e}`, 'error');
            status.textContent = 'Error';
        } finally {
            btn.disabled = false;
        }
    };

    const line = document.createElement('div');
    line.style.display = 'flex';
    line.style.alignItems = 'center';
    line.style.gap = '8px';
    line.style.flexWrap = 'wrap';
    line.appendChild(btn);
    line.appendChild(status);
    body.appendChild(line);
    return section;
}

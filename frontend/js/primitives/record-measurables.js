import { labClient } from '../cloudlabs/client.js';
import { primitiveRegion, runButton, afterCommandDispatch } from './shared.js';

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
            // Same primitive as Python ``lab.capture_measurable`` / ``recordMeasurables``.
            const data = await labClient.recordMeasurables(tagId);
            status.textContent = 'OK';
            const ci = data.measurables && data.measurables.camera_image;
            // Tensor-native: LazyRef under data.href; legacy wire still has .path.
            const path =
                ci && typeof ci === 'object'
                    ? (ci.path ||
                          (ci.data && typeof ci.data === 'object' && ci.data.href) ||
                          '')
                    : '';
            if (path) {
                status.textContent = `OK · ${String(path).replace(/^.*[/\\\\]/, '')}`;
            }
            await afterCommandDispatch(ctx.hooks, tagId);
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

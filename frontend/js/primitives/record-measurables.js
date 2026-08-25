import { labClient } from '../cloudlabs/client.js';
import { primitiveRegion, runButton, afterCommandDispatch } from './shared.js';

function _captureSummary(data) {
    const ci = data && data.measurables && data.measurables.camera_image;
    if (!ci || typeof ci !== 'object') {
        return {
            status: data && data.status === 'ok' ? 'OK · no camera_image' : 'OK',
            detail: `fields=${Object.keys((data && data.measurables) || {}).join(',') || '(none)'}`,
        };
    }
    const path =
        ci.path ||
        (ci.data && typeof ci.data === 'object' && ci.data.href) ||
        '';
    const shape = Array.isArray(ci.shape) ? ci.shape.join('×') : '';
    const leaf = path ? String(path).replace(/^.*[/\\]/, '') : '';
    const bits = ['OK'];
    if (leaf) bits.push(leaf);
    if (shape) bits.push(shape);
    if (data.epoch_ms != null) bits.push(`epoch=${data.epoch_ms}`);
    return {
        status: bits.join(' · '),
        detail: `camera_image shape=${shape || '?'} href=${path || '(none)'} epoch_ms=${data.epoch_ms ?? '—'}`,
    };
}

export function renderRecordMeasurables(ctx) {
    const { tagId } = ctx;
    const { section, body } = primitiveRegion('RECORD_MEASURABLES', 'RECORD MEASURABLES');
    const status = document.createElement('span');
    status.style.fontSize = 'var(--text-xs)';
    status.style.color = '#94a3b8';

    const btn = runButton('Capture measurables', 'photo_camera');
    btn.onclick = async () => {
        status.textContent = '…';
        btn.disabled = true;
        if (typeof ctx.hooks.log === 'function') {
            ctx.hooks.log(`RECORD_MEASURABLES ${tagId}…`, 'info');
        }
        try {
            // Same primitive as Python ``lab.capture_measurable`` / ``recordMeasurables``.
            const data = await labClient.recordMeasurables(tagId);
            const summary = _captureSummary(data);
            status.textContent = summary.status;
            if (typeof ctx.hooks.log === 'function') {
                ctx.hooks.log(`RECORD_MEASURABLES ${tagId}: ${summary.detail}`, 'info');
            }
            await afterCommandDispatch(ctx.hooks, tagId);
        } catch (e) {
            const msg = e && e.message ? e.message : String(e);
            if (typeof ctx.hooks.log === 'function') {
                ctx.hooks.log(`Record error: ${msg}`, 'error');
            }
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

/**
 * `ImageViewer` — measurable widget (§14.2).
 *
 * Loads the recorded camera image via
 * ``GET /api/components/{tag_id}/camera-image`` (Phase 4 route). Adds
 * a per-render cache-buster so a fresh ``RECORD_MEASURABLES`` is
 * reflected immediately; ``onerror`` collapses the element so a 404
 * (no record yet) doesn't leave a broken-image icon in the panel.
 *
 * ``backend_id`` must be on the query string (``<img>`` cannot send
 * ``X-CloudLabs-Backend``); without it the coordinator returns 400.
 *
 * Accepts both legacy ``{path, source}`` wire and canonical
 * MeasurableTensor LazyRef (``data.kind=url``, ``data.href``).
 */
import { widgetCard, widgetTitle, nullPlaceholder } from './common.js';
import { withBackendQuery } from '../state/backend-selection.js';

function _hasRecordedImage(value) {
    if (!value || typeof value !== 'object') return false;
    if (typeof value.path === 'string' && value.path) return true;
    const data = value.data;
    if (data && typeof data === 'object') {
        if (data.kind === 'url' && typeof data.href === 'string' && data.href) {
            return true;
        }
        if (typeof data.href === 'string' && data.href) return true;
    }
    // Shape alone (after a successful RECORD) is enough to attempt the proxy.
    if (Array.isArray(value.shape) && value.shape.length >= 2) return true;
    return false;
}

function _caption(value) {
    if (typeof value.path === 'string' && value.path) {
        const fname = String(value.path).replace(/^.*[\\/]/, '');
        const src = value.source ? ` (${value.source})` : '';
        return `${fname}${src}`;
    }
    const data = value.data && typeof value.data === 'object' ? value.data : null;
    const href = data && data.href ? String(data.href) : '';
    const fmt = data && data.format ? String(data.format) : 'jpeg';
    const shape = Array.isArray(value.shape) ? value.shape.join('×') : '';
    const parts = [];
    if (href) parts.push(href.replace(/^.*[/\\]/, '') || href);
    else parts.push(`LazyRef · ${fmt}`);
    if (shape) parts.push(shape);
    return parts.join(' · ');
}

export default function ImageViewer({ tagId, fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    if (!_hasRecordedImage(value)) {
        card.appendChild(nullPlaceholder('\u2014 null (no record yet)'));
        return card;
    }

    const meta = document.createElement('div');
    meta.style.color = '#64748b';
    meta.style.fontSize = 'var(--text-xs)';
    meta.style.marginBottom = '4px';
    meta.textContent = _caption(value);
    card.appendChild(meta);

    const stage = document.createElement('div');
    stage.className = 'meas-kernel-stage';
    stage.style.position = 'relative';
    stage.style.display = 'inline-block';
    stage.style.maxWidth = '100%';
    stage.style.lineHeight = '0';

    const img = document.createElement('img');
    img.src = withBackendQuery(
        `/api/components/${encodeURIComponent(tagId)}/camera-image?t=${Date.now()}`,
    );
    img.alt = `${fieldName} of ${tagId}`;
    img.style.maxWidth = '100%';
    img.style.maxHeight = '160px';
    img.style.objectFit = 'contain';
    img.style.borderRadius = '4px';
    img.style.border = '1px solid #2a2e36';
    img.style.background = '#000';
    img.style.display = 'block';

    const canvas = document.createElement('canvas');
    canvas.className = 'meas-kernel-overlay';
    canvas.style.position = 'absolute';
    canvas.style.left = '0';
    canvas.style.top = '0';
    canvas.style.pointerEvents = 'none';

    img.onerror = () => {
        img.style.display = 'none';
        canvas.style.display = 'none';
        meta.textContent = `${meta.textContent} · image fetch failed`;
    };
    stage.appendChild(img);
    stage.appendChild(canvas);
    card.appendChild(stage);
    return card;
}

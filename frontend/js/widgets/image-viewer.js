/**
 * `ImageViewer` — measurable widget (§14.2).
 *
 * Loads the recorded camera image via
 * ``GET /api/components/{tag_id}/camera-image`` (Phase 4 route). Adds
 * a per-render cache-buster so a fresh ``RECORD_MEASURABLES`` is
 * reflected immediately; ``onerror`` collapses the element so a 404
 * (no record yet) doesn't leave a broken-image icon in the panel.
 */
import { widgetCard, widgetTitle, nullPlaceholder } from './common.js';

export default function ImageViewer({ tagId, fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    if (!value || typeof value !== 'object' || !value.path) {
        card.appendChild(nullPlaceholder('\u2014 null (no record yet)'));
        return card;
    }

    const meta = document.createElement('div');
    meta.style.color = '#64748b';
    meta.style.fontSize = '9px';
    meta.style.marginBottom = '4px';
    const fname = String(value.path).replace(/^.*[\\/]/, '');
    const src = value.source ? ` (${value.source})` : '';
    meta.textContent = `${fname}${src}`;
    card.appendChild(meta);

    const img = document.createElement('img');
    img.src = `/api/components/${encodeURIComponent(tagId)}/camera-image?t=${Date.now()}`;
    img.alt = `${fieldName} of ${tagId}`;
    img.style.maxWidth = '100%';
    img.style.maxHeight = '160px';
    img.style.objectFit = 'contain';
    img.style.borderRadius = '4px';
    img.style.border = '1px solid #2a2e36';
    img.style.background = '#000';
    img.style.display = 'block';
    img.onerror = () => { img.style.display = 'none'; };
    card.appendChild(img);
    return card;
}

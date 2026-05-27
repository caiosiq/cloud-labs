/**
 * `OptimizationReferencePreview` — pinned COBYLA reference image (tunable intent).
 */
import { widgetCard, widgetTitle, nullPlaceholder } from './common.js';

export default function OptimizationReferencePreview({ tagId, fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    if (!value || typeof value !== 'object' || !value.path) {
        card.appendChild(nullPlaceholder('\u2014 no reference pinned (record + set from camera_image)'));
        return card;
    }

    const meta = document.createElement('div');
    meta.style.color = '#64748b';
    meta.style.fontSize = '9px';
    meta.style.marginBottom = '4px';
    const fname = String(value.path).replace(/^.*[\\/]/, '');
    meta.textContent = `Reference: ${fname}`;
    card.appendChild(meta);

    const img = document.createElement('img');
    img.src = `/api/components/${encodeURIComponent(tagId)}/camera-image?ref=1&t=${Date.now()}`;
    img.alt = `optimization reference for ${tagId}`;
    img.style.maxWidth = '100%';
    img.style.maxHeight = '120px';
    img.style.objectFit = 'contain';
    img.style.borderRadius = '4px';
    img.style.border = '1px solid #334155';
    img.style.background = '#000';
    img.onerror = () => { img.style.display = 'none'; };
    card.appendChild(img);
    return card;
}

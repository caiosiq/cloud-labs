/**
 * `ImageViewer` — measurable widget (§14.2).
 *
 * Loads the recorded camera image via
 * ``GET /api/components/{tag_id}/camera-image``. Includes a control to
 * pin the capture as the lab-wide COBYLA optimization reference.
 */
import { widgetCard, widgetTitle, nullPlaceholder } from './common.js';
import { pinLabOptimizationReference } from '../api/lab-optimization.js';

export default function ImageViewer({ tagId, fieldName, descriptor, value, hooks }) {
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

    if (fieldName === 'camera_image') {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary';
        btn.style.width = '100%';
        btn.style.marginTop = '6px';
        btn.style.fontSize = '10px';
        btn.innerHTML = '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">push_pin</span> Set as lab optimization reference';
        btn.classList.add('widget-action-btn');
        btn.onclick = async () => {
            btn.disabled = true;
            try {
                await pinLabOptimizationReference(tagId);
                if (typeof hooks?.fetchLabState === 'function') {
                    await hooks.fetchLabState();
                }
                if (typeof hooks?.refreshPanel === 'function') {
                    hooks.refreshPanel(tagId);
                }
            } catch (err) {
                if (typeof hooks?.log === 'function') {
                    hooks.log(err.message || String(err), 'error');
                }
            } finally {
                btn.disabled = false;
            }
        };
        card.appendChild(btn);
    }

    return card;
}

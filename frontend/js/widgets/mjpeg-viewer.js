/**
 * `MJPEGViewer` — telemetry widget (§14.3).
 *
 * Mounts an ``<img>`` whose ``src`` is the catalog-declared MJPEG URL
 * (multipart/x-mixed-replace). Browser renders the live frames
 * natively; no JS frame pump required. The catalog URL carries a
 * ``{tag_id}`` token resolved per-instance via {@link resolveTokens}.
 *
 * Lifetime: the browser keeps the connection open for as long as the
 * element is in the DOM. Re-rendering the component viewer (which
 * destroys and recreates the card) closes the old connection cleanly.
 */
import { widgetCard, widgetTitle, resolveTokens, nullPlaceholder } from './common.js';
import { isLiveFeedActive } from '../component-state.js';

export default function MJPEGViewer({ tagId, fieldName, descriptor, comp }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const url = resolveTokens(descriptor && descriptor.url, { tagId });
    if (!url) {
        card.appendChild(nullPlaceholder('\u2014 no telemetry URL in catalog'));
        return card;
    }

    if (!isLiveFeedActive(comp, fieldName || 'stream')) {
        card.appendChild(nullPlaceholder('\u2014 live feed off (turn on in PRIMITIVES)'));
        return card;
    }

    const img = document.createElement('img');
    img.src = url;
    img.alt = `MJPEG stream for ${tagId}`;
    img.style.width = '100%';
    img.style.maxHeight = '360px';
    img.style.objectFit = 'contain';
    img.style.background = '#000';
    img.style.border = '1px solid #2a2e36';
    img.style.borderRadius = '4px';
    img.style.display = 'block';
    img.onerror = () => {
        img.style.display = 'none';
        const msg = document.createElement('div');
        msg.style.color = '#64748b';
        msg.style.fontSize = 'var(--text-xs)';
        msg.style.fontStyle = 'italic';
        msg.textContent = `\u2014 stream unavailable (${url})`;
        card.appendChild(msg);
    };
    card.appendChild(img);

    const meta = document.createElement('div');
    meta.style.color = '#475569';
    meta.style.fontSize = 'var(--text-xs)';
    meta.style.marginTop = '4px';
    meta.style.fontFamily = 'ui-monospace, monospace';
    meta.textContent = url;
    card.appendChild(meta);
    return card;
}

/**
 * `JPEGPoll` — telemetry widget (§14.3).
 *
 * Lower-latency alternative to ``MJPEGViewer`` for single-frame
 * previews. Polls ``/api/components/{tag_id}/telemetry/preview`` at
 * ``descriptor.default_fps`` (default 10 fps) and swaps in each new
 * frame as it arrives. The poll timer is attached to the element via
 * ``setInterval`` and cleared when the element leaves the DOM
 * (MutationObserver guard) so re-rendering the viewer doesn't leak
 * polls.
 */
import { widgetCard, widgetTitle, resolveTokens, nullPlaceholder } from './common.js';
import { isLiveFeedActive } from '../component-state.js';
import { registerJpegPollStop } from './jpeg-poll-registry.js';
import { openLiveFeedPopout } from '../ui/live-feed-popout.js';

export { stopJpegPollForTag } from './jpeg-poll-registry.js';

export default function JPEGPoll({ tagId, fieldName, descriptor, comp, hooks }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const url = resolveTokens(descriptor && descriptor.url, { tagId });
    if (!url) {
        card.appendChild(nullPlaceholder('\u2014 no telemetry URL in catalog'));
        return card;
    }

    if (comp && !isLiveFeedActive(comp, fieldName || 'stream')) {
        card.appendChild(nullPlaceholder('\u2014 live feed off (turn on in PRIMITIVES)'));
        return card;
    }

    const fps = Number(descriptor && descriptor.default_fps) > 0
        ? Number(descriptor.default_fps)
        : 10;
    const intervalMs = Math.max(50, Math.round(1000 / fps));

    const img = document.createElement('img');
    img.alt = `JPEG poll for ${tagId}`;
    img.style.width = '100%';
    img.style.maxHeight = '360px';
    img.style.objectFit = 'contain';
    img.style.background = '#000';
    img.style.border = '1px solid #2a2e36';
    img.style.borderRadius = '4px';
    img.style.display = 'block';
    let consecutiveErrors = 0;
    const errHint = document.createElement('div');
    errHint.style.color = '#fca5a5';
    errHint.style.fontSize = '10px';
    errHint.style.fontStyle = 'italic';
    errHint.style.display = 'none';
    const refresh = () => {
        img.src = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    };
    img.onload = () => {
        consecutiveErrors = 0;
        img.style.display = 'block';
        errHint.style.display = 'none';
    };
    img.onerror = () => {
        consecutiveErrors += 1;
        if (consecutiveErrors >= 2) {
            errHint.textContent = `\u2014 preview unavailable (${url})`;
            errHint.style.display = 'block';
        }
        if (consecutiveErrors > 3) {
            img.style.display = 'none';
            stop();
        }
    };
    refresh();
    let timer = setInterval(refresh, intervalMs);
    function stop() {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    }
    const unregister = registerJpegPollStop(tagId, stop);
    // Stop polling when the element is removed from the DOM.
    const obs = new MutationObserver(() => {
        if (!card.isConnected) {
            unregister();
            obs.disconnect();
        }
    });
    // Defer observing to the next tick so the card has a parent.
    setTimeout(() => {
        if (card.parentNode) obs.observe(card.parentNode, { childList: true, subtree: true });
    }, 0);

    card.appendChild(img);
    card.appendChild(errHint);

    const pop = document.createElement('button');
    pop.type = 'button';
    pop.className = 'live-feed-pop-btn';
    pop.innerHTML =
        '<span class="material-icons-round" style="font-size:14px" aria-hidden="true">open_in_new</span> Pop out';
    pop.onclick = () => {
        openLiveFeedPopout(tagId, { fetchLabState: hooks?.fetchLabState });
    };
    card.appendChild(pop);

    const meta = document.createElement('div');
    meta.style.color = '#475569';
    meta.style.fontSize = '9px';
    meta.style.marginTop = '4px';
    meta.style.fontFamily = 'ui-monospace, monospace';
    meta.textContent = `${url}  \u2022  ${fps} fps`;
    card.appendChild(meta);
    return card;
}

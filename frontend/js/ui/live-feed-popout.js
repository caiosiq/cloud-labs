/**
 * Detachable always-on-top live-feed preview (in-app floater).
 *
 * Closing the floater does **not** end the feed; End live feed does.
 * Pop-outs survive switching the focused component panel so teleop on one
 * tag can run while watching another camera.
 */
import { store } from '../state/store.js';
import { isLiveFeedActive } from '../component-state.js';
import { withBackendQuery } from '../state/backend-selection.js';
import { endLiveFeed } from '../api/live-feed.js';
import { getCatalogRow } from '../component-model.js';
import { registerJpegPollStop } from '../widgets/jpeg-poll-registry.js';

/** @type {Map<string, { el: HTMLElement, stop: () => void, channel: string }>} */
const _open = new Map();

const FPS = 8;
const INTERVAL_MS = Math.round(1000 / FPS);

export function listActiveLiveFeedTags(labState = store.labState) {
    const comps = labState?.components || {};
    const tags = [];
    Object.entries(comps).forEach(([tagId, entry]) => {
        if (entry && isLiveFeedActive(entry, 'stream')) tags.push(tagId);
    });
    return tags.sort();
}

export function isLiveFeedPopoutOpen(tagId) {
    return _open.has(String(tagId || '').trim());
}

function _previewUrl(tagId) {
    return withBackendQuery(
        `/api/components/${encodeURIComponent(tagId)}/telemetry/preview`,
    );
}

function _displayName(tagId) {
    const row = getCatalogRow(tagId);
    return (row && row.name) || tagId;
}

function _ensureLayer() {
    let layer = document.getElementById('live-feed-popout-layer');
    if (layer) return layer;
    layer = document.createElement('div');
    layer.id = 'live-feed-popout-layer';
    layer.setAttribute('aria-live', 'polite');
    document.body.appendChild(layer);
    return layer;
}

function _nextOffset() {
    const n = _open.size;
    return { left: 24 + (n % 4) * 28, top: 72 + (n % 4) * 28 };
}

/**
 * Open (or focus) a detachable live preview for ``tagId``.
 * @param {string} tagId
 * @param {{ channel?: string, fetchLabState?: () => Promise<unknown> }} [opts]
 */
export function openLiveFeedPopout(tagId, opts = {}) {
    const id = String(tagId || '').trim();
    if (!id) return;
    const existing = _open.get(id);
    if (existing) {
        existing.el.classList.add('live-feed-popout--flash');
        existing.el.querySelector('.live-feed-popout__frame')?.focus?.();
        setTimeout(() => existing.el.classList.remove('live-feed-popout--flash'), 400);
        return;
    }

    const channel = opts.channel || 'stream';
    const layer = _ensureLayer();
    const offset = _nextOffset();

    const el = document.createElement('div');
    el.className = 'live-feed-popout';
    el.dataset.tagId = id;
    el.style.left = `${offset.left}px`;
    el.style.top = `${offset.top}px`;
    el.innerHTML = `
        <div class="live-feed-popout__header" data-drag-handle>
            <span class="live-feed-popout__title">
                <span class="material-icons-round" aria-hidden="true">videocam</span>
                LIVE · ${_escape(id)}
            </span>
            <span class="live-feed-popout__actions">
                <button type="button" class="live-feed-popout__btn" data-action="end" title="End live feed on this camera">End feed</button>
                <button type="button" class="live-feed-popout__btn live-feed-popout__btn--ghost" data-action="close" title="Hide pop-out (feed stays on)">Hide</button>
            </span>
        </div>
        <div class="live-feed-popout__hint">RECORD / OPTIMIZE blocked on this camera while live · ${_escape(_displayName(id))}</div>
        <div class="live-feed-popout__frame" tabindex="-1">
            <img alt="Live feed ${id}" class="live-feed-popout__img"/>
            <div class="live-feed-popout__err" hidden></div>
        </div>
    `;

    const img = el.querySelector('.live-feed-popout__img');
    const err = el.querySelector('.live-feed-popout__err');
    const url = _previewUrl(id);
    let consecutiveErrors = 0;
    let timer = null;

    const refresh = () => {
        if (!img) return;
        img.src = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    };
    img.onload = () => {
        consecutiveErrors = 0;
        if (err) err.hidden = true;
        img.hidden = false;
    };
    img.onerror = () => {
        consecutiveErrors += 1;
        if (consecutiveErrors >= 2 && err) {
            err.hidden = false;
            err.textContent = 'No JPEG (is live feed still on?)';
        }
    };

    function stop() {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    }

    const unregister = registerJpegPollStop(id, stop);
    refresh();
    timer = setInterval(refresh, INTERVAL_MS);

    const closePopoutOnly = () => {
        stop();
        unregister();
        el.remove();
        _open.delete(id);
        syncLiveFeedSessionChrome();
    };

    el.querySelector('[data-action="close"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        closePopoutOnly();
    });
    el.querySelector('[data-action="end"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const btn = e.currentTarget;
        if (btn) btn.disabled = true;
        void endLiveFeed(id, 'all')
            .then(async () => {
                closePopoutOnly();
                if (typeof opts.fetchLabState === 'function') await opts.fetchLabState();
            })
            .catch((errObj) => {
                console.warn('[live-feed-popout] end failed', errObj);
                if (btn) btn.disabled = false;
            });
    });

    _makeDraggable(el, el.querySelector('[data-drag-handle]'));
    layer.appendChild(el);
    _open.set(id, { el, stop, channel });
    syncLiveFeedSessionChrome();
}

export function closeLiveFeedPopout(tagId) {
    const id = String(tagId || '').trim();
    const entry = _open.get(id);
    if (!entry) return;
    entry.stop();
    entry.el.remove();
    _open.delete(id);
}

/** Close pop-outs whose live session has ended; keep open ones in sync. */
export function reconcileLiveFeedPopouts(labState = store.labState) {
    const live = new Set(listActiveLiveFeedTags(labState));
    for (const tagId of [..._open.keys()]) {
        if (!live.has(tagId)) closeLiveFeedPopout(tagId);
    }
    syncLiveFeedSessionChrome();
}

/**
 * Bench-header strip: which cameras are live + Pop out / End.
 * @param {{ fetchLabState?: () => Promise<unknown>, log?: Function }} [hooks]
 */
export function syncLiveFeedSessionChrome(hooks = {}) {
    const host = document.getElementById('live-feed-session-bar');
    if (!host) return;

    const tags = listActiveLiveFeedTags();
    if (!tags.length) {
        host.hidden = true;
        host.innerHTML = '';
        return;
    }

    host.hidden = false;
    host.innerHTML = `
        <span class="live-feed-session-bar__label">
            <span class="material-icons-round" aria-hidden="true">videocam</span>
            Live
        </span>
        ${tags
            .map((tagId) => {
                const open = isLiveFeedPopoutOpen(tagId);
                return `<span class="live-feed-session-bar__chip" data-tag="${_escape(tagId)}">
                    <code>${_escape(tagId)}</code>
                    <button type="button" data-action="pop" title="Pop out preview">${open ? 'Focus' : 'Pop out'}</button>
                    <button type="button" data-action="end" title="End live feed">End</button>
                </span>`;
            })
            .join('')}
        <span class="live-feed-session-bar__note">RECORD/OPTIMIZE blocked on these cameras</span>
    `;

    host.querySelectorAll('.live-feed-session-bar__chip').forEach((chip) => {
        const tagId = chip.getAttribute('data-tag');
        chip.querySelector('[data-action="pop"]')?.addEventListener('click', () => {
            openLiveFeedPopout(tagId, { fetchLabState: hooks.fetchLabState });
        });
        chip.querySelector('[data-action="end"]')?.addEventListener('click', () => {
            void endLiveFeed(tagId, 'all')
                .then(async () => {
                    closeLiveFeedPopout(tagId);
                    if (typeof hooks.fetchLabState === 'function') await hooks.fetchLabState();
                    else reconcileLiveFeedPopouts();
                })
                .catch((e) => hooks.log?.(`End live feed failed: ${e.message || e}`, 'error'));
        });
    });
}

function _escape(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _makeDraggable(el, handle) {
    if (!handle) return;
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let origLeft = 0;
    let origTop = 0;
    handle.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('button')) return;
        dragging = true;
        startX = e.clientX;
        startY = e.clientY;
        origLeft = el.offsetLeft;
        origTop = el.offsetTop;
        handle.setPointerCapture(e.pointerId);
        e.preventDefault();
    });
    handle.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        el.style.left = `${Math.max(0, origLeft + e.clientX - startX)}px`;
        el.style.top = `${Math.max(0, origTop + e.clientY - startY)}px`;
    });
    handle.addEventListener('pointerup', () => {
        dragging = false;
    });
    handle.addEventListener('pointercancel', () => {
        dragging = false;
    });
}

/**
 * Detachable always-on-top live-feed preview (in-app floater).
 *
 * Closing the floater does **not** end the feed; End live feed does.
 * Pop-outs survive switching the focused component panel so teleop on one
 * tag can run while watching another camera.
 *
 * "Measure beam CoM" briefly pauses the live stream, runs the same science
 * RECORD path as the camera panel, evaluates ``builtin.beam_com``, then
 * restarts live — without changing the normal RECORD_MEASURABLES UI.
 */
import { store } from '../state/store.js';
import {
    applyComponentTelemetryFromServer,
    isLiveFeedActive,
    normalizeCapabilities,
} from '../component-state.js';
import { withBackendQuery } from '../state/backend-selection.js';
import { endLiveFeed, setLiveExposure } from '../api/live-feed.js';
import { labClient } from '../cloudlabs/client.js';
import { probeKernel } from '../api/kernels.js';
import { overlaysFromProbeResult } from './measurable-kernels.js';
import { paintKernelOverlays } from '../optimize-session/kernel-overlay.js';
import { getCatalogRow } from '../component-model.js';
import { registerJpegPollStop } from '../widgets/jpeg-poll-registry.js';
import { log } from './log.js';

/** @type {Map<string, { el: HTMLElement, stop: () => void, channel: string, resumePoll?: () => void }>} */
const _open = new Map();

/** Tags mid pause→RECORD→kernel→resume; reconcile must not close their pop-out. */
const _snapshotHold = new Set();

const FPS = 8;
const INTERVAL_MS = Math.round(1000 / FPS);
const BEAM_COM_KERNEL = 'builtin.beam_com';

/** Science cameras declare exposure; overhead table-top does not. */
function _supportsLiveExposure(tagId) {
    const row = getCatalogRow(tagId);
    const caps = normalizeCapabilities(row?.capabilities);
    if (caps.statecontrol?.tunables?.exposure_time_ms) return true;
    const prims = caps.primitives || [];
    return prims.includes('SET_LIVE_EXPOSURE') || prims.includes('SET_EXPOSURE');
}

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

function _liveExposureMs(tagId) {
    const entry = store.labState?.components?.[tagId];
    const live = entry?.telemetry?.live_feed?.stream?.live_exposure_time_ms;
    if (live !== undefined && live !== null && Number.isFinite(Number(live))) {
        return Number(live);
    }
    const science = entry?.statecontrol?.tunables?.exposure_time_ms;
    if (science !== undefined && science !== null && Number.isFinite(Number(science))) {
        return Number(science);
    }
    return 50;
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
    const exp0 = _liveExposureMs(id);
    const showExp = _supportsLiveExposure(id);
    const showBeamCom = showExp; // beam CoM is for science cams, not overhead overview

    const el = document.createElement('div');
    el.className = 'live-feed-popout';
    el.dataset.tagId = id;
    el.style.left = `${offset.left}px`;
    el.style.top = `${offset.top}px`;
    const hintParts = ['OPTIMIZE blocked while live'];
    if (showBeamCom) {
        hintParts.push('Measure beam CoM pauses briefly for a science capture');
    }
    hintParts.push(_escape(_displayName(id)));
    const exposureRow = showExp
        ? `<div class="live-feed-popout__exposure">
            <label title="Preview only (VEXP) — not science SET_EXPOSURE">
                Preview ms
                <input type="number" min="0.1" max="1000" step="0.1" value="${exp0}" data-live-exp />
            </label>
            <button type="button" class="live-feed-popout__btn" data-action="apply-exp">Apply</button>
            ${
                showBeamCom
                    ? `<button type="button" class="live-feed-popout__btn live-feed-popout__btn--measure" data-action="measure-com" title="Pause live, science RECORD, measure beam CoM, resume live">Measure beam CoM</button>`
                    : ''
            }
        </div>`
        : '';
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
        <div class="live-feed-popout__hint">${hintParts.join(' · ')}</div>
        ${exposureRow}
        <div class="live-feed-popout__measure-status" data-measure-status aria-live="polite"></div>
        <div class="live-feed-popout__frame" tabindex="-1">
            <img alt="Live feed ${id}" class="live-feed-popout__img"/>
            <canvas class="live-feed-popout__overlay" hidden aria-hidden="true"></canvas>
            <div class="live-feed-popout__err" hidden></div>
            <div class="live-feed-popout__resize-hint" aria-hidden="true" title="Drag corner to resize"></div>
        </div>
    `;

    // Explicit pixel size so CSS resize:both has a real height to grow/shrink.
    const vw = Math.max(320, window.innerWidth || 1280);
    const vh = Math.max(240, window.innerHeight || 800);
    el.style.width = `${Math.min(780, Math.round(vw * 0.88))}px`;
    el.style.height = `${Math.min(560, Math.round(vh * 0.78))}px`;

    const img = el.querySelector('.live-feed-popout__img');
    const overlay = el.querySelector('.live-feed-popout__overlay');
    const err = el.querySelector('.live-feed-popout__err');
    const statusEl = el.querySelector('[data-measure-status]');
    const url = _previewUrl(id);
    let consecutiveErrors = 0;
    let timer = null;
    /** @type {ReturnType<typeof setTimeout>|null} */
    let overlayRedrawTimer = null;
    /** @type {object[]} */
    let lastOverlays = [];

    const refresh = () => {
        if (!img) return;
        img.src = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    };
    const paintOverlay = () => {
        if (!overlay || !img) return;
        if (!lastOverlays.length) {
            overlay.hidden = true;
            const ctx = overlay.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, overlay.width, overlay.height);
            return;
        }
        overlay.hidden = false;
        paintKernelOverlays(overlay, img, lastOverlays);
    };
    img.onload = () => {
        consecutiveErrors = 0;
        if (err) err.hidden = true;
        img.hidden = false;
        paintOverlay();
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

    function resumePoll() {
        if (timer) return;
        refresh();
        timer = setInterval(refresh, INTERVAL_MS);
    }

    const unregister = registerJpegPollStop(id, stop);
    refresh();
    timer = setInterval(refresh, INTERVAL_MS);

    const frame = el.querySelector('.live-feed-popout__frame');
    if (frame && typeof ResizeObserver !== 'undefined') {
        const ro = new ResizeObserver(() => {
            if (overlayRedrawTimer) clearTimeout(overlayRedrawTimer);
            overlayRedrawTimer = setTimeout(paintOverlay, 40);
        });
        ro.observe(frame);
    }

    const closePopoutOnly = () => {
        stop();
        unregister();
        if (overlayRedrawTimer) clearTimeout(overlayRedrawTimer);
        el.remove();
        _open.delete(id);
        _snapshotHold.delete(id);
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
                if (String(errObj?.message || errObj) === 'cancelled') {
                    if (btn) btn.disabled = false;
                    return;
                }
                console.warn('[live-feed-popout] end failed', errObj);
                if (btn) btn.disabled = false;
            });
    });
    el.querySelector('[data-action="apply-exp"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const inp = el.querySelector('[data-live-exp]');
        const btn = e.currentTarget;
        const v = parseFloat(inp?.value);
        if (!Number.isFinite(v) || v <= 0) {
            console.warn('[live-feed-popout] invalid preview exposure');
            return;
        }
        if (btn) btn.disabled = true;
        void setLiveExposure(id, v)
            .then(async () => {
                if (typeof opts.fetchLabState === 'function') await opts.fetchLabState();
            })
            .catch((errObj) => {
                if (String(errObj?.message || errObj) === 'cancelled') return;
                console.warn('[live-feed-popout] set live exposure failed', errObj);
            })
            .finally(() => {
                if (btn) btn.disabled = false;
            });
    });
    el.querySelector('[data-action="measure-com"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        void _measureBeamComFromLive(id, {
            el,
            stop,
            resumePoll,
            statusEl,
            setOverlays: (list) => {
                lastOverlays = Array.isArray(list) ? list : [];
                paintOverlay();
            },
            fetchLabState: opts.fetchLabState,
        });
    });

    _makeDraggable(el, el.querySelector('[data-drag-handle]'));
    layer.appendChild(el);
    _open.set(id, { el, stop, channel, resumePoll });
    syncLiveFeedSessionChrome();
}

export function closeLiveFeedPopout(tagId) {
    const id = String(tagId || '').trim();
    const entry = _open.get(id);
    if (!entry) return;
    entry.stop();
    entry.el.remove();
    _open.delete(id);
    _snapshotHold.delete(id);
}

/** Close pop-outs whose live session has ended; keep open ones in sync. */
export function reconcileLiveFeedPopouts(labState = store.labState) {
    const live = new Set(listActiveLiveFeedTags(labState));
    for (const tagId of [..._open.keys()]) {
        if (_snapshotHold.has(tagId)) continue;
        if (!live.has(tagId)) closeLiveFeedPopout(tagId);
    }
    syncLiveFeedSessionChrome();
}

/**
 * Pause live → science RECORD → builtin.beam_com → resume live.
 * Keeps the pop-out open; does not change the normal camera RECORD button flow.
 *
 * @param {string} tagId
 * @param {{
 *   el: HTMLElement,
 *   stop: () => void,
 *   resumePoll: () => void,
 *   statusEl?: HTMLElement|null,
 *   setOverlays?: (list: object[]) => void,
 *   fetchLabState?: () => Promise<unknown>,
 * }} ctx
 */
async function _measureBeamComFromLive(tagId, ctx) {
    const id = String(tagId || '').trim();
    if (!id || !ctx?.el) return;
    if (_snapshotHold.has(id)) return;

    const measureBtn = ctx.el.querySelector('[data-action="measure-com"]');
    const applyBtn = ctx.el.querySelector('[data-action="apply-exp"]');
    const endBtn = ctx.el.querySelector('[data-action="end"]');
    const setBusy = (busy) => {
        if (measureBtn) measureBtn.disabled = busy;
        if (applyBtn) applyBtn.disabled = busy;
        if (endBtn) endBtn.disabled = busy;
    };
    const setStatus = (text, isErr = false) => {
        if (!ctx.statusEl) return;
        ctx.statusEl.textContent = text || '';
        ctx.statusEl.classList.toggle('live-feed-popout__measure-status--err', !!isErr);
    };

    const previewMs = (() => {
        const inp = ctx.el.querySelector('[data-live-exp]');
        const v = parseFloat(inp?.value);
        if (Number.isFinite(v) && v > 0) return v;
        return _liveExposureMs(id);
    })();

    setBusy(true);
    _snapshotHold.add(id);
    ctx.stop();
    ctx.setOverlays?.([]);
    setStatus('Pausing live feed…');

    let resumed = false;
    try {
        // Soft end: do not close the pop-out (unlike api/live-feed.endLiveFeed).
        const endBody = await labClient.endLiveFeed(id, 'all');
        applyComponentTelemetryFromServer(id, endBody?.telemetry);

        setStatus('Science capture (RECORD)…');
        const recorded = await labClient.recordMeasurables(id);
        const frameHw = _shapeHw(recorded?.measurables?.camera_image);

        setStatus('Measuring beam CoM…');
        const probe = await probeKernel(id, {
            kernel_id: BEAM_COM_KERNEL,
            field: 'camera_image',
            skipConfirm: true,
        });
        if (!probe.ok) {
            throw new Error(probe.error || 'beam CoM probe failed');
        }

        const result = probe.result || {};
        const feats = Array.isArray(result.features) ? result.features : [];
        const names = Array.isArray(result.feature_names) ? result.feature_names : ['cx', 'cy', 'peak'];
        const summary = _formatBeamCom(feats, names);
        setStatus(`Beam CoM · ${summary}`);

        const img = ctx.el.querySelector('.live-feed-popout__img');
        const overlays = overlaysFromProbeResult({
            kernelId: BEAM_COM_KERNEL,
            features: feats,
            featureNames: names,
            frameHw,
            natW: img?.naturalWidth,
            natH: img?.naturalHeight,
        });
        ctx.setOverlays?.(overlays);

        setStatus(`Resuming live… · ${summary}`);
        const startBody = await labClient.startLiveFeed(id, 'stream', {
            exposure_time_ms: previewMs,
        });
        applyComponentTelemetryFromServer(id, startBody?.telemetry);
        resumed = true;
        ctx.resumePoll?.();
        setStatus(`Beam CoM · ${summary}`);
        log(`Live measure beam CoM on ${id}: ${summary}`, 'info');

        if (typeof ctx.fetchLabState === 'function') {
            await ctx.fetchLabState();
        }
    } catch (errObj) {
        const msg = String(errObj?.message || errObj);
        console.warn('[live-feed-popout] measure beam CoM failed', errObj);
        setStatus(msg, true);
        log(`Live measure beam CoM failed (${id}): ${msg}`, 'error');
        ctx.setOverlays?.([]);
        if (!resumed) {
            try {
                const startBody = await labClient.startLiveFeed(id, 'stream', {
                    exposure_time_ms: previewMs,
                });
                applyComponentTelemetryFromServer(id, startBody?.telemetry);
                ctx.resumePoll?.();
                if (typeof ctx.fetchLabState === 'function') await ctx.fetchLabState();
            } catch (resumeErr) {
                console.warn('[live-feed-popout] resume live after measure failed', resumeErr);
                setStatus(
                    `${msg} · resume failed: ${resumeErr?.message || resumeErr}`,
                    true,
                );
            }
        }
    } finally {
        _snapshotHold.delete(id);
        setBusy(false);
        syncLiveFeedSessionChrome();
    }
}

function _shapeHw(value) {
    if (!value || typeof value !== 'object') return null;
    const shape = value.shape;
    if (Array.isArray(shape) && shape.length >= 2) {
        const h = Number(shape[0]);
        const w = Number(shape[1]);
        if (Number.isFinite(h) && Number.isFinite(w) && h > 0 && w > 0) {
            return /** @type {[number, number]} */ ([h, w]);
        }
    }
    return null;
}

function _formatBeamCom(feats, names) {
    const get = (name, idx) => {
        const i = Array.isArray(names) ? names.indexOf(name) : -1;
        const v = i >= 0 ? Number(feats[i]) : Number(feats[idx]);
        return Number.isFinite(v) ? v : null;
    };
    const cx = get('cx', 0);
    const cy = get('cy', 1);
    const peak = get('peak', 2);
    const fmt = (n) => (n == null ? '—' : Number(n).toFixed(1));
    const peakFmt =
        peak == null
            ? ''
            : ` · peak=${Number(peak).toFixed(3).replace(/\.?0+$/, '')}`;
    return `cx=${fmt(cx)} cy=${fmt(cy)}${peakFmt}`;
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
        <span class="live-feed-session-bar__note">OPTIMIZE blocked · use Measure beam CoM in pop-out</span>
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
                .catch((e) => {
                    if (String(e?.message || e) === 'cancelled') return;
                    hooks.log?.(`End live feed failed: ${e.message || e}`, 'error');
                });
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

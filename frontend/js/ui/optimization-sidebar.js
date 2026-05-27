/**
 * Modular right-sidebar controller for autonomous OPTIMIZE.
 * Driven entirely by the OPTIMIZE primitive payload / session descriptor.
 */
import { store } from '../state/store.js';
import { getCatalogRow } from '../component-model.js';
import { normalizeCapabilities } from '../component-state.js';
import { resolveTokens } from '../widgets/common.js';
import { startLiveFeed, endLiveFeed } from '../api/live-feed.js';
import { supportsLiveVideo } from '../catalog-support.js';
import {
    drawOptimizationLossChart,
    freezeOptimizationPreviewReadout,
    clearOptimizationPreviewReadout,
    prepareOptimizationPreviewReadout,
    pushOptimizationTick,
    resetOptimizationPreview,
    initOptimizationPreview,
} from './optimization-preview.js';

let _lossCanvas = null;
let _statusEl = null;
let _refSlot = null;
let _feedSlot = null;
let _refPanel = null;
let _feedPanel = null;
let _feedStop = null;
let _activeFeed = null;
/** Last rendered ``optimization_reference`` key — avoid rebuilding static sidebar image every poll. */
let _lastRenderedRefKey = null;

function optimizationReferenceKey(ref) {
    if (!ref || typeof ref !== 'object' || !ref.path) return '';
    return [
        String(ref.path),
        String(ref.pinned_at || ''),
        String(ref.sensor_component || ''),
        String(ref.format || 'png'),
    ].join('|');
}

function liveFeedDescriptor(sensorId) {
    const row = getCatalogRow(sensorId);
    const lf = normalizeCapabilities(row?.capabilities).telemetry?.live_feed || {};
    const channel = Object.keys(lf)[0] || 'stream';
    const desc = lf[channel] || {};
    const url = resolveTokens(
        desc.url || '/api/components/{tag_id}/telemetry/preview',
        { tagId: sensorId },
    );
    return {
        tag_id: sensorId,
        channel,
        url,
        fps: Number(desc.default_fps) > 0 ? Number(desc.default_fps) : 20,
        widget: desc.widget || 'JPEGPoll',
    };
}

/** Build session descriptor from an OPTIMIZE command (what the sidebar renders). */
export function buildOptimizeSessionDescriptor(targetId, parameters = {}) {
    const sensor = parameters.sensor_component || parameters.sensor_tag_id || null;
    const liveFeed = sensor && supportsLiveVideo(sensor) ? liveFeedDescriptor(sensor) : null;
    return {
        target_id: targetId,
        strategy: parameters.strategy || 'COBYLA',
        loss_metric: parameters.loss_metric || null,
        sensor_component: sensor,
        live_feed: liveFeed,
        reference: store.labState?.optimization_reference || null,
        started_at: Date.now(),
    };
}

export function initOptimizationSidebar() {
    initOptimizationPreview();
    _lossCanvas = document.getElementById('optimization-loss-chart');
    _statusEl = document.getElementById('optimization-preview-status');
    _refSlot = document.getElementById('optimization-reference-slot');
    _feedSlot = document.getElementById('optimization-live-feed-slot');
    _refPanel = document.getElementById('optimization-reference-panel');
    _feedPanel = document.getElementById('optimization-live-feed-panel');
    syncLabReferenceDisplay();
}

export function syncLabReferenceDisplay({ force = false } = {}) {
    if (!_refSlot) return;
    const ref = store.labState?.optimization_reference;
    const refKey = optimizationReferenceKey(ref);
    if (!force && refKey === _lastRenderedRefKey && _refSlot.childElementCount > 0) {
        return;
    }
    _lastRenderedRefKey = refKey;
    _refSlot.replaceChildren();
    if (!refKey) {
        const empty = document.createElement('div');
        empty.className = 'opt-sidebar-empty';
        empty.textContent = 'No lab reference pinned — capture on a camera, then pin from measurables.';
        _refSlot.appendChild(empty);
        return;
    }
    const meta = document.createElement('div');
    meta.className = 'opt-sidebar-meta';
    const fname = String(ref.path).replace(/^.*[\\/]/, '');
    meta.textContent = ref.sensor_component
        ? `${fname} · ${ref.sensor_component}`
        : fname;
    _refSlot.appendChild(meta);
    const img = document.createElement('img');
    img.className = 'opt-sidebar-img';
    img.alt = 'Lab optimization reference';
    // Stable URL for a pinned reference — only changes when the pin metadata changes.
    const cacheKey = encodeURIComponent(ref.pinned_at || ref.path);
    img.src = `/api/lab/optimization-reference/image?v=${cacheKey}`;
    img.onerror = () => {
        img.replaceWith(
            Object.assign(document.createElement('div'), {
                className: 'opt-sidebar-empty',
                textContent: 'Reference image unavailable.',
            }),
        );
    };
    _refSlot.appendChild(img);
}

function stopLiveFeedPreview() {
    if (_feedStop) {
        try { _feedStop(); } catch (_) { /* noop */ }
        _feedStop = null;
    }
    if (_activeFeed) {
        void endLiveFeed(_activeFeed.tag_id, 'all').catch(() => {});
        _activeFeed = null;
    }
    if (_feedSlot) _feedSlot.replaceChildren();
    if (_feedPanel) _feedPanel.hidden = true;
}

function startLiveFeedPreview(session) {
    stopLiveFeedPreview();
    const lf = session?.live_feed;
    if (!lf || !lf.tag_id || !_feedSlot) {
        if (_feedPanel) _feedPanel.hidden = true;
        return;
    }
    if (_feedPanel) _feedPanel.hidden = false;

    const img = document.createElement('img');
    img.className = 'opt-sidebar-img opt-sidebar-img--feed';
    img.alt = `Live feed ${lf.tag_id}`;
    const intervalMs = Math.max(50, Math.round(1000 / (lf.fps || 20)));
    let timer = null;
    let errors = 0;
    const refresh = () => {
        img.src = `${lf.url}${lf.url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    };
    img.onload = () => { errors = 0; img.style.display = 'block'; };
    img.onerror = () => {
        errors += 1;
        if (errors > 3) stopLiveFeedPreview();
    };
    refresh();
    timer = setInterval(refresh, intervalMs);
    _feedStop = () => {
        if (timer) clearInterval(timer);
        timer = null;
    };
    _feedSlot.replaceChildren(img);

    _activeFeed = { tag_id: lf.tag_id, channel: lf.channel };
    void startLiveFeed(lf.tag_id, lf.channel).catch(() => {
        /* preview poll still works for mock jpeg endpoints */
    });
}

/** Called when OPTIMIZE is dispatched — sidebar reads primitive payload only. */
export function applyOptimizeSession(session) {
    // User engaged with live bench state — skip boot checkpoint restore for this tab.
    store.sessionReconciliationFetched = true;
    const existingModal = document.getElementById('session-reconcile-modal');
    if (existingModal) existingModal.remove();

    store.optimizeSession = session || null;
    store.optimizeLossSeries = [];
    store.optimizeTick = null;
    if (session) {
        prepareOptimizationPreviewReadout();
    } else {
        clearOptimizationPreviewReadout();
    }
    drawOptimizationLossChart();

    if (_statusEl && session) {
        _statusEl.textContent = `Running ${session.strategy} on ${session.target_id}…`;
        _statusEl.style.color = '#c4b5fd';
    }

    syncLabReferenceDisplay();
    startLiveFeedPreview(session);
}

/** OPTIMIZE finished (BUSY→IDLE): stop live feed, keep loss chart. */
export function finalizeOptimizeSession() {
    const session = store.optimizeSession;
    stopLiveFeedPreview();
    store.optimizeSession = null;
    freezeOptimizationPreviewReadout();
    if (_statusEl) {
        if (session) {
            _statusEl.textContent = `${session.strategy} on ${session.target_id} complete.`;
        } else {
            _statusEl.textContent = 'Optimization complete.';
        }
        _statusEl.style.color = '#94a3b8';
    }
}

export function clearOptimizeSession() {
    store.optimizeSession = null;
    stopLiveFeedPreview();
    resetOptimizationPreview();
    if (_statusEl) {
        _statusEl.textContent = 'Idle — loss curve appears when optimization runs.';
        _statusEl.style.color = '#64748b';
    }
}

export function onOptimizationTick(tick) {
    pushOptimizationTick(tick);
}

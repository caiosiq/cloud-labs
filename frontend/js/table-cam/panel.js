/**
 * Table-cam panel — visuals, button wiring, capture flow, exposure debouncing.
 *
 * `initTableCamPanel()` wires every `<button>` / `<input>` in the dock and kicks off the initial
 * connect for cam 1. It returns `{ tableCamImg }` so callers (e.g. cobyla-reference) can read
 * the latest capture for upload without needing to re-query the DOM.
 *
 * The panel renders three orthogonal pieces of state:
 *   1. Connect phase  — disconnected / connecting / connected (`syncTableCamConnectToggleAppearance`)
 *   2. Live vs Still  — `syncTableCamLiveButtonAppearance` + `restoreTableCamPanelVisuals`
 *   3. Capture-busy   — `syncTableCamCaptureButtonAppearance`
 * Plus an extra MOCK/REAL chrome chip via `updateTableCamMockPreviewChrome`.
 */
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import {
    applyTableCamPreviewConfig,
    stopTableCamLivePreview,
    stopTableCamStreamImg,
    syncTableCamPreviewPoll,
} from './preview-engine.js';
import {
    apiTableCamConnect,
    apiTableCamDisconnect,
    apiTableCamLive,
    fetchTableCamStatus,
    tableCamApiErrorMessage,
} from './api.js';

// DOM refs (resolved during init).
let tableCamBtn1 = null;
let tableCamBtn2 = null;
let tableCamLinkToggle = null;
let tableCamLiveBtn = null;
let tableCamCaptureBtn = null;
let tableCamImg = null;
let tableCamPlaceholder = null;
let tableCamError = null;

// Pushed to /api/table-cam/{cid}/vexp ~320 ms after the last keystroke in the exposure input.
let tableCamVexpDebounceTimer = null;

/** Wire up the table-cam dock and start initial connect. Returns `{ tableCamImg }`. */
export function initTableCamPanel() {
    tableCamBtn1 = document.getElementById('table-cam-btn-1');
    tableCamBtn2 = document.getElementById('table-cam-btn-2');
    tableCamLinkToggle = document.getElementById('table-cam-link-toggle');
    tableCamLiveBtn = document.getElementById('table-cam-live-btn');
    tableCamCaptureBtn = document.getElementById('table-cam-capture-btn');
    tableCamImg = document.getElementById('table-cam-img');
    tableCamPlaceholder = document.getElementById('table-cam-placeholder');
    tableCamError = document.getElementById('table-cam-error');

    if (tableCamBtn1) tableCamBtn1.addEventListener('click', () => void setTableCamSelection(1));
    if (tableCamBtn2) tableCamBtn2.addEventListener('click', () => void setTableCamSelection(2));

    if (tableCamLinkToggle) tableCamLinkToggle.addEventListener('click', onLinkToggleClick);
    if (tableCamLiveBtn) tableCamLiveBtn.addEventListener('click', onLiveBtnClick);
    if (tableCamCaptureBtn) tableCamCaptureBtn.addEventListener('click', onCaptureBtnClick);

    wireTableCamExposureVexpDebounce();
    void bootstrapTableCam();

    return { tableCamImg };
}

// --- Visual sync (also exported for updateUI() in app-main) ---

export function syncTableCamMockHint() {
    const mockEl = document.getElementById('table-cam-mock-hint');
    const realEl = document.getElementById('table-cam-real-hint');
    if (!store.labState) return;
    const mock = store.labState.lab_mode === 'MOCK';
    if (mockEl) mockEl.style.display = mock ? 'block' : 'none';
    if (realEl) realEl.style.display = mock ? 'none' : 'block';
}

function setTableCamPreviewLoading(message, visible) {
    const el = document.getElementById('table-cam-preview-loading');
    const text = document.getElementById('table-cam-preview-loading-text');
    if (!el) return;
    if (text && message) text.textContent = message;
    el.classList.toggle('table-cam-preview-loading--visible', !!visible);
    el.setAttribute('aria-hidden', visible ? 'false' : 'true');
}

function tableCamConnectPhase(camId) {
    if (store.tableCamConnecting[camId]) return 'connecting';
    if (store.tableCamConnected[camId]) return 'connected';
    return 'disconnected';
}

export function updateTableCamMockPreviewChrome() {
    const preview = document.getElementById('table-cam-preview');
    const chip = document.getElementById('table-cam-mode-chip');
    if (!preview || !chip) return;

    chip.textContent = '';
    chip.className = 'table-cam-mode-chip';
    chip.style.display = 'none';

    preview.classList.remove(
        'table-cam-preview--mock-live',
        'table-cam-preview--mock-capture',
        'table-cam-preview--mock-neutral',
    );

    if (store.isOptimizingFeedActive) {
        preview.classList.add('table-cam-preview--mock-neutral');
        return;
    }

    const mock = !!(store.labState && store.labState.lab_mode === 'MOCK');
    if (!mock) {
        // REAL mode: chip shows raw hardware/connect/stream status in monospaced parts.
        preview.classList.add('table-cam-preview--mock-neutral');
        const cid = store.selectedTableCam;
        const parts = [];
        if (!store.tableCamRecorderAlive) parts.push('RECORDER DOWN');
        else parts.push((store.tableCamRecorderVariant || 'recorder').toUpperCase());
        parts.push((store.tableCamHardware[cid] || 'none').toUpperCase());
        if (store.tableCamConnecting[cid]) parts.push('CONNECTING');
        else if (store.tableCamConnected[cid]) parts.push('CONNECTED');
        if (store.tableCamLive[cid]) parts.push('LIVE');
        if (store.tableCamLivePending[cid]) parts.push('…');
        chip.textContent = parts.join(' · ');
        chip.style.display = 'block';
        if (store.tableCamLastError[cid]) {
            chip.title = String(store.tableCamLastError[cid]);
        } else {
            chip.removeAttribute('title');
        }
        return;
    }

    const cid = store.selectedTableCam;
    preview.classList.add('table-cam-preview--mock-neutral');

    if (store.tableCamLive[cid]) {
        preview.classList.remove('table-cam-preview--mock-neutral');
        preview.classList.add('table-cam-preview--mock-live');
        chip.textContent = 'MOCK · LIVE STREAM';
        chip.classList.add('mode-live');
        chip.style.display = 'block';
        return;
    }
    if (getTableCamLastBlobUrl(cid)) {
        preview.classList.remove('table-cam-preview--mock-neutral');
        preview.classList.add('table-cam-preview--mock-capture');
        chip.textContent = 'MOCK · STILL CAPTURE';
        chip.classList.add('mode-capture');
        chip.style.display = 'block';
    }
}

function getTableCamLastBlobUrl(camId) {
    return store.tableCamLastBlobUrl[camId] || null;
}

function setTableCamLastBlobUrl(camId, objectUrl) {
    const prev = store.tableCamLastBlobUrl[camId];
    if (prev) URL.revokeObjectURL(prev);
    store.tableCamLastBlobUrl[camId] = objectUrl;
}

export function restoreTableCamPanelVisuals() {
    const img = document.getElementById('table-cam-img');
    const placeholder = document.getElementById('table-cam-placeholder');
    if (!img || !placeholder) return;
    if (store.isOptimizingFeedActive) return;

    const cid = store.selectedTableCam;
    const hint =
        '<span style="font-size: 11px;line-height:1.45;color:var(--text-muted);">Use <strong>Connected</strong>, then the <strong>Live / Still</strong> toggle for stream vs frozen preview. <strong>Capture</strong> grabs a fresh still and switches to Still.</span>';

    if (!store.tableCamConnected[cid]) {
        img.src = '';
        img.style.display = 'none';
        const lastCap = getTableCamLastBlobUrl(cid);
        if (lastCap) {
            img.src = lastCap;
            img.style.display = 'block';
            placeholder.style.display = 'none';
            placeholder.textContent = '';
            return;
        }
        placeholder.style.display = 'block';
        placeholder.innerHTML = hint;
        return;
    }

    if (store.tableCamCapturePending[cid]) {
        stopTableCamStreamImg(cid);
        img.src = '';
        img.style.display = 'none';
        placeholder.textContent = 'Capturing…';
        placeholder.style.display = 'block';
        setTableCamPreviewLoading('', false);
        return;
    }

    if (store.tableCamLive[cid] || store.tableCamLivePending[cid]) {
        if (!store.tableCamLive[cid]) {
            stopTableCamLivePreview(cid);
            img.style.display = 'none';
            placeholder.style.display = 'none';
        }
        setTableCamPreviewLoading(
            'Obtaining live feed…',
            store.tableCamLivePending[cid] || !store.tableCamLive[cid],
        );
        syncTableCamPreviewPoll(cid);
        return;
    }

    stopTableCamLivePreview(cid);
    setTableCamPreviewLoading('', false);

    const lastCap = getTableCamLastBlobUrl(cid);
    if (lastCap) {
        img.src = lastCap;
        img.style.display = 'block';
        placeholder.style.display = 'none';
        placeholder.textContent = '';
        return;
    }
    img.src = '';
    img.style.display = 'none';
    placeholder.style.display = 'block';
    placeholder.innerHTML = hint;
}

function syncTableCamConnectToggleAppearance() {
    const btn = document.getElementById('table-cam-link-toggle');
    const label = document.getElementById('table-cam-link-toggle-label');
    const icon = document.getElementById('table-cam-link-dot');
    if (!btn || !label || !icon) return;
    const cid = store.selectedTableCam;
    const phase = tableCamConnectPhase(cid);
    const on = phase === 'connected';
    const connecting = phase === 'connecting';
    btn.classList.toggle('table-cam-connect-toggle--connected', on);
    btn.classList.toggle('table-cam-connect-toggle--connecting', connecting);
    btn.classList.toggle('table-cam-connect-toggle--disconnected', phase === 'disconnected');
    btn.disabled = connecting;
    icon.textContent = connecting ? 'sync' : on ? 'link' : 'link_off';
    label.textContent = connecting ? 'Connecting' : on ? 'Connected' : 'Disconnected';
    btn.title = connecting
        ? 'Opening camera session…'
        : on
          ? 'Tap to disconnect and release the camera session'
          : 'Tap to connect / open SDK session';
}

function syncTableCamLiveButtonAppearance() {
    const liveBtn = document.getElementById('table-cam-live-btn');
    if (!liveBtn) return;
    const cid = store.selectedTableCam;
    const streaming = !!store.tableCamLive[cid];
    const pending = !!store.tableCamLivePending[cid];
    const connected = !!store.tableCamConnected[cid];
    const connecting = !!store.tableCamConnecting[cid];
    liveBtn.classList.toggle('table-cam-live-btn--streaming', streaming);
    liveBtn.classList.toggle('table-cam-live-btn--pending', pending);
    liveBtn.setAttribute('aria-pressed', streaming ? 'true' : 'false');
    liveBtn.disabled = !connected || connecting || pending;
    const icon = document.getElementById('table-cam-live-btn-icon');
    const label = document.getElementById('table-cam-live-btn-label');
    if (icon && label) {
        if (!connected) {
            icon.textContent = 'videocam';
            label.textContent = 'Live';
        } else if (pending) {
            icon.textContent = 'sync';
            label.textContent = streaming ? 'Live' : 'Still';
        } else if (streaming) {
            icon.textContent = 'videocam';
            label.textContent = 'Live';
        } else {
            icon.textContent = 'photo';
            label.textContent = 'Still';
        }
    }
    liveBtn.title = streaming
        ? 'Live preview (STREAM_ON) — click to show still / last capture'
        : connected
          ? 'Still / last capture — click to start live preview'
          : 'Connect the camera to toggle live preview vs still';
}

function syncTableCamCaptureButtonAppearance() {
    const btn = document.getElementById('table-cam-capture-btn');
    const cid = store.selectedTableCam;
    if (!btn) return;
    btn.disabled =
        !store.tableCamConnected[cid] ||
        store.tableCamConnecting[cid] ||
        store.tableCamCapturePending[cid] ||
        store.tableCamLivePending[cid];
}

async function refreshTableCamLiveUi() {
    syncTableCamConnectToggleAppearance();
    syncTableCamLiveButtonAppearance();
    syncTableCamCaptureButtonAppearance();
    updateTableCamMockPreviewChrome();
    restoreTableCamPanelVisuals();
}

// --- Connect / disconnect orchestration ---

function showTableCamError(message) {
    if (!tableCamError) return;
    tableCamError.textContent = message || '';
    tableCamError.style.display = message ? 'block' : 'none';
}

export function clearTableCamError() {
    showTableCamError('');
}

function optimisticTableCamDisconnect(camId) {
    store.tableCamConnected[camId] = false;
    store.tableCamConnecting[camId] = false;
    store.tableCamLive[camId] = false;
    store.tableCamLivePending[camId] = false;
    store.tableCamCapturePending[camId] = false;
    stopTableCamStreamImg(camId);
}

function beginTableCamConnect(camId) {
    store.tableCamConnecting[camId] = true;
    store.tableCamConnected[camId] = false;
    clearTableCamError();
    void refreshTableCamLiveUi();
    return (async () => {
        try {
            await apiTableCamConnect(camId);
            scheduleTableCamVexpPush();
        } catch (e) {
            store.tableCamConnected[camId] = false;
            store.tableCamHardware[camId] = 'none';
            showTableCamError(e.message || String(e));
            throw e;
        } finally {
            store.tableCamConnecting[camId] = false;
            await refreshTableCamLiveUi();
        }
    })();
}

function scheduleTableCamVexpPush() {
    if (tableCamVexpDebounceTimer) clearTimeout(tableCamVexpDebounceTimer);
    tableCamVexpDebounceTimer = setTimeout(async () => {
        const cid = store.selectedTableCam;
        const exp = getTableCamExposureSeconds();
        if (!store.tableCamConnected[cid]) return;
        try {
            await fetch(`/api/table-cam/${cid}/vexp`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ exposure: exp }),
            });
        } catch {
            /* non-fatal */
        }
    }, 320);
}

async function setTableCamSelection(camId) {
    store.selectedTableCam = camId;
    if (tableCamBtn1) {
        tableCamBtn1.classList.toggle('btn-primary', camId === 1);
        tableCamBtn1.classList.toggle('btn-secondary', camId !== 1);
    }
    if (tableCamBtn2) {
        tableCamBtn2.classList.toggle('btn-primary', camId === 2);
        tableCamBtn2.classList.toggle('btn-secondary', camId !== 2);
    }
    await refreshTableCamLiveUi();
    if (!store.tableCamConnected[camId] && !store.tableCamConnecting[camId]) {
        void beginTableCamConnect(camId);
    }
}

async function onLinkToggleClick() {
    const cid = store.selectedTableCam;
    if (store.tableCamConnecting[cid]) return;
    clearTableCamError();
    const wasConnected = !!store.tableCamConnected[cid];
    if (wasConnected) {
        optimisticTableCamDisconnect(cid);
        await refreshTableCamLiveUi();
    } else {
        store.tableCamConnecting[cid] = true;
        await refreshTableCamLiveUi();
    }
    try {
        if (wasConnected) {
            await apiTableCamDisconnect(cid);
        } else {
            await apiTableCamConnect(cid);
            scheduleTableCamVexpPush();
        }
    } catch (e) {
        if (wasConnected) {
            try {
                await fetchTableCamStatus(cid);
            } catch {
                /* ignore */
            }
        } else {
            store.tableCamConnected[cid] = false;
        }
        showTableCamError(e.message || 'Toggle failed');
    } finally {
        store.tableCamConnecting[cid] = false;
        await refreshTableCamLiveUi();
    }
}

async function onLiveBtnClick() {
    const cid = store.selectedTableCam;
    clearTableCamError();
    if (store.tableCamConnecting[cid]) {
        showTableCamError('Wait for the camera to finish connecting.');
        return;
    }
    if (!store.tableCamConnected[cid]) {
        showTableCamError('Connect the camera before enabling live preview.');
        return;
    }
    if (store.tableCamLivePending[cid]) return;
    const prevLive = !!store.tableCamLive[cid];
    const next = !prevLive;
    store.tableCamLivePending[cid] = true;
    store.tableCamLive[cid] = next;
    await refreshTableCamLiveUi();
    try {
        await apiTableCamLive(cid, next);
        if (next) scheduleTableCamVexpPush();
    } catch (e) {
        store.tableCamLive[cid] = prevLive;
        stopTableCamStreamImg(cid);
        showTableCamError(e.message || 'Live toggle failed');
    } finally {
        store.tableCamLivePending[cid] = false;
        await refreshTableCamLiveUi();
    }
}

async function onCaptureBtnClick() {
    if (!tableCamImg || !tableCamPlaceholder || !tableCamError) return;
    const exp = getTableCamExposureSeconds();
    const cid = store.selectedTableCam;
    clearTableCamError();
    if (store.tableCamConnecting[cid]) {
        showTableCamError('Wait for the camera to finish connecting.');
        return;
    }
    if (!store.tableCamConnected[cid]) {
        showTableCamError('Connect the camera before capturing.');
        return;
    }
    if (store.tableCamCapturePending[cid]) return;

    const wasLive = !!store.tableCamLive[cid];
    store.tableCamCapturePending[cid] = true;
    store.tableCamLive[cid] = false;
    store.tableCamLivePending[cid] = false;
    stopTableCamStreamImg(cid);
    await refreshTableCamLiveUi();

    // Server-side: ensure STREAM_OFF before issuing a still capture so we don't fight the recorder.
    const streamOffPromise = wasLive
        ? apiTableCamLive(cid, false).catch((e) => {
              showTableCamError(e.message || 'Could not stop live stream for capture');
              throw e;
          })
        : Promise.resolve();

    try {
        await streamOffPromise;
        const res = await fetch(
            `/api/table-cam/capture?cam_id=${cid}&exposure=${encodeURIComponent(exp)}`,
        );
        if (res.ok) {
            const blob = await res.blob();
            setTableCamLastBlobUrl(cid, URL.createObjectURL(blob));
        } else {
            let msg = 'Capture failed';
            try {
                const j = await res.json().catch(() => ({}));
                msg = tableCamApiErrorMessage(j) || msg;
            } catch (_) {
                /* ignore */
            }
            showTableCamError(msg);
        }
    } catch (e) {
        if (!tableCamError.textContent) {
            showTableCamError(e.message || 'Request failed');
        }
    } finally {
        store.tableCamCapturePending[cid] = false;
        await refreshTableCamLiveUi();
    }
}

// --- Exposure input → vexp ---

export function getTableCamExposureSeconds() {
    const el = document.getElementById('table-cam-exposure');
    if (!el) return store.tableCamExposure;
    const v = parseFloat(el.value);
    if (!Number.isFinite(v) || v <= 0) return store.tableCamExposure;
    store.tableCamExposure = v;
    return v;
}

function wireTableCamExposureVexpDebounce() {
    const expEl = document.getElementById('table-cam-exposure');
    if (!expEl) return;
    expEl.addEventListener('change', () => {
        getTableCamExposureSeconds();
        scheduleTableCamVexpPush();
    });
    expEl.addEventListener('input', () => {
        getTableCamExposureSeconds();
        scheduleTableCamVexpPush();
    });
}

// --- Bootstrap (initial selection + first connect for cam 1) ---

async function bootstrapTableCam() {
    store.selectedTableCam = 1;
    if (tableCamBtn1) {
        tableCamBtn1.classList.add('btn-primary');
        tableCamBtn1.classList.remove('btn-secondary');
    }
    if (tableCamBtn2) {
        tableCamBtn2.classList.add('btn-secondary');
        tableCamBtn2.classList.remove('btn-primary');
    }
    try {
        const res = await fetch('/api/table-cam/status');
        if (res.ok) {
            const data = await res.json();
            applyTableCamPreviewConfig(data.preview_config);
        }
    } catch {
        /* defaults */
    }
    store.tableCamConnecting[1] = true;
    await refreshTableCamLiveUi();
    void beginTableCamConnect(1);
}

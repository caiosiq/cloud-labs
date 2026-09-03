/**
 * /optimize-session — full-detail live OPTIMIZE viewer (separate from Twin sidebar).
 */
import {
    getSelectedBackendId,
    setSelectedBackendId,
    withBackendQuery,
    backendHeaders,
} from '../state/backend-selection.js';
import { acceptJob } from '../api/jobs.js';
import {
    renderSessionDetailHtml,
    sessionContext,
    collectTrace,
    resolveCameraPreviewSource,
    formatTraceTableText,
} from './session-detail.js';
import { attachLossChart, lossPointsFromTrace } from './loss-chart.js';
import {
    bindKernelOverlayStage,
    extractKernelOverlays,
} from './kernel-overlay.js';
import { promptAndSaveTextFile } from '../util/save-text-file.js';

const root = document.getElementById('osd-root');
const lossShell = document.getElementById('osd-loss-shell');
const lossCanvas = document.getElementById('osd-loss-chart');
const backendSelect = document.getElementById('osd-backend');
const errorEl = document.getElementById('osd-error');
const pollHint = document.getElementById('osd-poll-hint');
const acceptBtn = document.getElementById('osd-accept');
const refreshBtn = document.getElementById('osd-refresh');

let _pollTimer = null;
let _jobId = '';
let _labState = null;
/** @type {ReturnType<typeof attachLossChart>|null} */
let _lossChart = null;
/** @type {{ destroy?: () => void }|null} */
let _kernelOverlay = null;
let _detailSig = '';
let _overlaySig = '';

function setError(msg) {
    if (errorEl) errorEl.textContent = msg || '';
}

async function fetchJson(url, opts = {}) {
    const res = await fetch(withBackendQuery(url), {
        ...opts,
        headers: backendHeaders(opts.headers || {}),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body?.detail || res.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return res.json();
}

function ensureLossChart() {
    if (!lossCanvas) return null;
    if (!_lossChart) {
        _lossChart = attachLossChart(lossCanvas, { compact: false });
    }
    return _lossChart;
}

function updateLossChart(labState) {
    const ctx = sessionContext(labState);
    if (lossShell) lossShell.hidden = !ctx;
    const chart = ensureLossChart();
    if (!chart || !ctx) return;
    chart.setPoints(lossPointsFromTrace(collectTrace(labState)));
}

function remountKernelOverlay(labState) {
    _kernelOverlay?.destroy?.();
    _kernelOverlay = null;
    const preview = resolveCameraPreviewSource(
        labState,
        sessionContext(labState)?.lastEval,
    );
    const overlays = extractKernelOverlays(preview.row);
    _overlaySig = JSON.stringify(
        overlays.map((o) => [
            o.termId,
            o.detected?.x,
            o.detected?.y,
            o.target?.x,
            o.target?.y,
            preview.evalN,
        ]),
    );
    _kernelOverlay = bindKernelOverlayStage(root, overlays);
}

function bindSaveHistoryButton(labState) {
    const btn = root?.querySelector('#osd-save-history');
    if (!btn) return;
    btn.onclick = async () => {
        const text = formatTraceTableText(collectTrace(labState));
        if (!text.trim()) {
            setError('No iteration rows to save yet');
            return;
        }
        const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
        const result = await promptAndSaveTextFile({
            title: 'File name',
            defaultName: `optimization-history-${stamp}.txt`,
            content: text,
        });
        if (result.error === 'cancelled') return;
        if (!result.ok) {
            setError(result.error || 'Save failed');
            return;
        }
        setError('');
        if (pollHint) {
            pollHint.textContent = `Saved ${result.filename}`;
        }
    };
}

function syncKernelOverlay(labState) {
    const preview = resolveCameraPreviewSource(
        labState,
        sessionContext(labState)?.lastEval,
    );
    const overlays = extractKernelOverlays(preview.row);
    const sig = JSON.stringify(
        overlays.map((o) => [
            o.termId,
            o.detected?.x,
            o.detected?.y,
            o.target?.x,
            o.target?.y,
            preview.evalN,
        ]),
    );
    if (sig !== _overlaySig || !_kernelOverlay) {
        remountKernelOverlay(labState);
    } else {
        _kernelOverlay.redraw?.();
    }
}

async function loadBackends() {
    const rows = await fetchJson('/api/backends');
    const list = Array.isArray(rows) ? rows : rows?.backends || [];
    backendSelect.innerHTML = '';
    list.forEach((b) => {
        const id = b.backend_id || b.id;
        if (!id) return;
        const opt = document.createElement('option');
        opt.value = id;
        const ready = b.availability === 'ready' ? 'ready' : b.availability || '?';
        opt.textContent = `${id} (${ready})`;
        backendSelect.appendChild(opt);
    });
    let current = getSelectedBackendId();
    if (current && [...backendSelect.options].some((o) => o.value === current)) {
        backendSelect.value = current;
    } else if (backendSelect.options.length) {
        const q = new URLSearchParams(window.location.search).get('backend_id');
        if (q && [...backendSelect.options].some((o) => o.value === q)) {
            backendSelect.value = q;
            setSelectedBackendId(q);
        } else {
            backendSelect.value = backendSelect.options[0].value;
            setSelectedBackendId(backendSelect.value);
        }
    }
}

async function refresh() {
    try {
        if (!getSelectedBackendId()) {
            setError('Select a backend');
            return;
        }
        _labState = await fetchJson('/api/lab-state');
        const jobId =
            new URLSearchParams(window.location.search).get('job_id') ||
            _labState?.active_job_id ||
            '';
        _jobId = jobId || '';
        const ctx = sessionContext(_labState);
        const maxEvals =
            Number(new URLSearchParams(window.location.search).get('max_evals')) || null;

        // Avoid wiping interactive widgets (camera/hover) when only eval counters move.
        const rows = collectTrace(_labState);
        const tail = rows.length ? rows[rows.length - 1] : null;
        const detailSig = [
            ctx?.running ? '1' : '0',
            ctx?.queued ? '1' : '0',
            ctx?.eval ?? 0,
            ctx?.bestLoss ?? '',
            tail?.loss ?? '',
            rows.length,
            _jobId,
            tail?.stages ? JSON.stringify(tail.stages).slice(0, 120) : '',
            resolveCameraSig(_labState, tail),
        ].join('|');

        if (detailSig !== _detailSig) {
            _detailSig = detailSig;
            root.innerHTML = renderSessionDetailHtml(_labState, {
                jobId: _jobId,
                maxEvals,
            });
            const img = root.querySelector('.osd-camera img');
            if (img) {
                img.onerror = () => {
                    img.style.display = 'none';
                };
            }
            remountKernelOverlay(_labState);
            bindSaveHistoryButton(_labState);
        } else {
            syncKernelOverlay(_labState);
            bindSaveHistoryButton(_labState);
        }

        updateLossChart(_labState);

        if (acceptBtn) {
            const show = !!(ctx?.running && _jobId);
            acceptBtn.hidden = !show;
            if (!acceptBtn.disabled || acceptBtn.textContent !== 'Accepting…') {
                acceptBtn.disabled = false;
                acceptBtn.textContent = 'Accept · good enough';
            }
        }
        setError('');
        if (pollHint) {
            pollHint.textContent = ctx?.running
                ? `live · eval ${ctx.eval ?? 0}`
                : 'idle · last completed session';
        }
    } catch (err) {
        setError(err.message || String(err));
        if (pollHint) pollHint.textContent = 'error';
    }
}

function resolveCameraSig(labState, lastEval) {
    const cam = lastEval?.camera_image;
    if (!cam || typeof cam !== 'object') return '';
    return String(cam.epoch_ms ?? lastEval?.eval ?? '');
}

function startPolling() {
    if (_pollTimer) clearInterval(_pollTimer);
    _pollTimer = setInterval(() => {
        void refresh();
    }, 1000);
}

backendSelect?.addEventListener('change', () => {
    setSelectedBackendId(backendSelect.value);
    const url = new URL(window.location.href);
    url.searchParams.set('backend_id', backendSelect.value);
    window.history.replaceState({}, '', url);
    _detailSig = '';
    void refresh();
});

refreshBtn?.addEventListener('click', () => {
    _detailSig = '';
    void refresh();
});

acceptBtn?.addEventListener('click', async () => {
    if (!_jobId) return;
    acceptBtn.disabled = true;
    acceptBtn.textContent = 'Accepting…';
    const res = await acceptJob(_jobId);
    if (!res.ok) {
        setError(res.error || 'Accept failed');
        acceptBtn.disabled = false;
        acceptBtn.textContent = 'Accept · good enough';
        return;
    }
    setError('');
    void refresh();
});

await loadBackends();
await refresh();
startPolling();

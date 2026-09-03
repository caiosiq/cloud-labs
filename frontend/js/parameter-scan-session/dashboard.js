/**
 * /parameter-scan-session — live viewer for Parameter Scan (sibling of optimize-session).
 * Receives snapshots from Twin via BroadcastChannel / localStorage.
 */
import {
    getSelectedBackendId,
    setSelectedBackendId,
    withBackendQuery,
} from '../state/backend-selection.js';
import {
    readParameterScanSnapshot,
    subscribeParameterScan,
} from '../state/parameter-scan-bus.js';
import { overlaysFromProbeResult } from '../ui/measurable-kernels.js';
import { paintKernelOverlays } from '../optimize-session/kernel-overlay.js';

import { promptAndSaveTextFile } from '../util/save-text-file.js';

const root = document.getElementById('psd-root');
const errorEl = document.getElementById('psd-error');
const pollHint = document.getElementById('psd-poll-hint');

const params = new URLSearchParams(window.location.search);
const scanId = String(params.get('scan_id') || '').trim();
const backendFromUrl = params.get('backend_id');
if (backendFromUrl) setSelectedBackendId(backendFromUrl);

function setError(msg) {
    if (errorEl) errorEl.textContent = msg || '';
}

function fmt(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return '—';
    return Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.001)
        ? x.toExponential(3)
        : x.toFixed(3).replace(/\.?0+$/, '');
}

function escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function statusLabel(status) {
    if (status === 'running') return 'Running';
    if (status === 'done') return 'Completed';
    if (status === 'aborted') return 'Aborted';
    if (status === 'error') return 'Error';
    return status || 'Idle';
}

function paintChart(canvas, rows) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#0f141b';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#2a3340';
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1);

    const ok = (rows || []).filter((r) => r.ok);
    if (!ok.length) {
        ctx.fillStyle = '#94a3b8';
        ctx.font = '12px Inter, sans-serif';
        ctx.fillText('Waiting for points…', 16, 28);
        return;
    }
    const xs = ok.map((r) => Number(r.value));
    const cxs = ok.map((r) => Number(r.features?.[0]));
    const cys = ok.map((r) => Number(r.features?.[1]));
    const xmin = Math.min(...xs);
    const xmax = Math.max(...xs);
    const ys = [...cxs, ...cys].filter((n) => Number.isFinite(n));
    const ymin = ys.length ? Math.min(...ys) : 0;
    const ymax = ys.length ? Math.max(...ys) : 1;
    const pad = 36;
    const xspan = xmax - xmin || 1;
    const yspan = ymax - ymin || 1;
    const toX = (v) => pad + ((v - xmin) / xspan) * (w - pad * 2);
    const toY = (v) => h - pad - ((v - ymin) / yspan) * (h - pad * 2);

    const draw = (vals, color) => {
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        let started = false;
        vals.forEach((yv, i) => {
            if (!Number.isFinite(yv)) return;
            const x = toX(xs[i]);
            const y = toY(yv);
            if (!started) {
                ctx.moveTo(x, y);
                started = true;
            } else ctx.lineTo(x, y);
        });
        ctx.stroke();
        vals.forEach((yv, i) => {
            if (!Number.isFinite(yv)) return;
            ctx.beginPath();
            ctx.arc(toX(xs[i]), toY(yv), 3, 0, Math.PI * 2);
            ctx.fill();
        });
    };
    draw(cxs, '#22d3ee');
    draw(cys, '#fbbf24');
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px Inter, sans-serif';
    ctx.fillText('cx', 12, 18);
    ctx.fillStyle = '#22d3ee';
    ctx.fillRect(30, 11, 14, 4);
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('cy', 54, 18);
    ctx.fillStyle = '#fbbf24';
    ctx.fillRect(72, 11, 14, 4);
}

function cameraSrc(url) {
    if (!url) return '';
    if (url.includes('backend_id=')) return url;
    return withBackendQuery(url);
}

function paintComOverlay(snap) {
    const img = document.getElementById('psd-camera');
    const canvas = document.getElementById('psd-camera-overlay');
    if (!img || !canvas) return;

    let feats = Array.isArray(snap.lastFeatures) ? snap.lastFeatures : [];
    let names = Array.isArray(snap.feature_names) ? snap.feature_names : ['cx', 'cy', 'peak'];
    if (!feats.length && Array.isArray(snap.results)) {
        for (let i = snap.results.length - 1; i >= 0; i -= 1) {
            const r = snap.results[i];
            if (r?.ok && Array.isArray(r.features) && r.features.length) {
                feats = r.features.map(Number);
                if (Array.isArray(r.feature_names) && r.feature_names.length) {
                    names = r.feature_names;
                }
                break;
            }
        }
    }
    if (!feats.length || !Number.isFinite(Number(feats[0])) || !Number.isFinite(Number(feats[1]))) {
        canvas.hidden = true;
        return;
    }

    const draw = () => {
        const natW = img.naturalWidth || 0;
        const natH = img.naturalHeight || 0;
        let frameHw = snap.lastFrameHw || null;
        if (
            (!frameHw || !frameHw[0] || !frameHw[1]) &&
            natW > 0 &&
            natH > 0
        ) {
            frameHw = [natH, natW];
        }
        // Last resort: size the frame so (cx,cy) fit inside with margin.
        if (!frameHw || !frameHw[0] || !frameHw[1]) {
            const cx = Number(feats[0]);
            const cy = Number(feats[1]);
            frameHw = [
                Math.max(Math.ceil(cy * 2), 100),
                Math.max(Math.ceil(cx * 2), 100),
            ];
        }
        const overlays = overlaysFromProbeResult({
            kernelId: snap.kernelId || 'builtin.beam_com',
            features: feats,
            featureNames: names,
            frameHw,
            natW: natW || frameHw[1],
            natH: natH || frameHw[0],
        });
        if (!overlays.length) {
            canvas.hidden = true;
            return;
        }
        canvas.hidden = false;
        paintKernelOverlays(canvas, img, overlays);
    };
    if (img.complete && img.naturalWidth) draw();
    else img.addEventListener('load', draw, { once: true });
}

function formatScanTableText(snap) {
    const names = Array.isArray(snap?.feature_names) ? snap.feature_names : ['cx', 'cy', 'peak'];
    const rows = Array.isArray(snap?.results) ? snap.results : [];
    if (!rows.length) return '';
    const header = ['#', 'value', ...names, 'ok'];
    const lines = [header.join('\t')];
    rows.forEach((r, idx) => {
        const f = Array.isArray(r.features) ? r.features : [];
        lines.push(
            [
                (r.i ?? idx) + 1,
                Number.isFinite(Number(r.value)) ? String(r.value) : '',
                ...names.map((_, i) =>
                    Number.isFinite(Number(f[i])) ? String(f[i]) : '',
                ),
                r.ok ? 'ok' : 'fail',
            ].join('\t'),
        );
    });
    return `${lines.join('\n')}\n`;
}

function bindSaveScanButton(snap) {
    const btn = root?.querySelector('#psd-save-table');
    if (!btn) return;
    btn.onclick = async () => {
        const text = formatScanTableText(snap);
        if (!text.trim()) {
            setError('No scan steps to save yet');
            return;
        }
        const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
        const result = await promptAndSaveTextFile({
            title: 'File name',
            defaultName: `parameter-scan-${stamp}.txt`,
            content: text,
        });
        if (result.error === 'cancelled') return;
        if (!result.ok) {
            setError(result.error || 'Save failed');
            return;
        }
        setError('');
        if (pollHint) pollHint.textContent = `Saved ${result.filename}`;
    };
}

function trackStatsHtml(snap) {
    const stats = snap.trackStats;
    const feature = snap.trackFeature || stats?.feature || 'cy';
    if (!stats || !stats.n) {
        return `<div class="psd-track-stats">
            <strong>Tracked · ${escapeHtml(feature)}</strong>
            <div class="psd-meta">No successful samples yet.</div>
        </div>`;
    }
    return `<div class="psd-track-stats">
        <strong>Tracked · ${escapeHtml(stats.feature || feature)}</strong>
        <div>min ${fmt(stats.min)} <span class="psd-meta">@ tunable ${fmt(stats.atMin)}</span></div>
        <div>max ${fmt(stats.max)} <span class="psd-meta">@ tunable ${fmt(stats.atMax)}</span></div>
        <div class="psd-meta">${stats.n} successful step(s)</div>
    </div>`;
}

function renderSnapshot(snap) {
    if (!root) return;
    if (!snap) {
        root.innerHTML = `
            <div class="psd-empty">
                <p>No snapshot yet for <code>${escapeHtml(scanId || '(missing scan_id)')}</code>.</p>
                <p>Start a scan from Twin — this page will update automatically.</p>
            </div>`;
        if (pollHint) pollHint.textContent = 'waiting…';
        return;
    }

    const total = Number(snap.total) || 0;
    const step = Number(snap.step) || 0;
    const pct = total > 0 ? Math.min(100, Math.round((step / total) * 100)) : 0;
    const names = Array.isArray(snap.feature_names) ? snap.feature_names : ['cx', 'cy', 'peak'];
    const rows = Array.isArray(snap.results) ? snap.results : [];
    const lastIdx = rows.length ? rows.length - 1 : -1;
    const feats = Array.isArray(snap.lastFeatures)
        ? snap.lastFeatures
        : (() => {
              const rows = Array.isArray(snap.results) ? snap.results : [];
              for (let i = rows.length - 1; i >= 0; i -= 1) {
                  if (rows[i]?.ok && Array.isArray(rows[i].features) && rows[i].features.length) {
                      return rows[i].features;
                  }
              }
              return [];
          })();
    const featLine = names
        .map((n, i) => `${n}=${fmt(feats[i])}`)
        .join(' · ');

    root.innerHTML = `
        <section class="psd-hero">
            <div class="psd-kicker">Parameter scan</div>
            <h2>${escapeHtml(statusLabel(snap.status))} · ${escapeHtml(snap.measureLabel || snap.kernelId || 'measure')}</h2>
            <div class="psd-meta">
                ${escapeHtml(snap.axisLabel || '—')}
                · camera ${escapeHtml(snap.cameraTagId || '—')}
                · step ${step}${total ? ` / ${total}` : ''}
                · track ${escapeHtml(snap.trackFeature || 'cy')}
            </div>
            <div class="psd-meta">${escapeHtml(snap.statusText || '')}</div>
            <div class="psd-progress" title="Progress"><span style="width:${pct}%"></span></div>
        </section>
        <div class="psd-grid">
            <section class="psd-panel">
                <h3>Latest capture</h3>
                <div class="psd-camera-wrap">
                    ${
                        snap.lastCameraUrl
                            ? `<div class="psd-camera-stage">
                                 <img id="psd-camera" alt="Scan capture" src="${escapeHtml(cameraSrc(snap.lastCameraUrl))}" />
                                 <canvas id="psd-camera-overlay" class="psd-camera-overlay" aria-hidden="true"></canvas>
                               </div>`
                            : `<div class="psd-camera-empty">Waiting for first RECORD…</div>`
                    }
                </div>
                <div class="psd-feats">${escapeHtml(featLine || '—')}</div>
            </section>
            <section class="psd-panel">
                <h3>CoM vs tunable</h3>
                <canvas id="psd-chart" width="640" height="280" aria-label="Scan chart"></canvas>
            </section>
        </div>
        <section class="psd-panel" style="margin-top:16px;">
            <div class="psd-panel__head">
                <h3>Step table</h3>
                <button type="button" class="psd-btn" id="psd-save-table" ${rows.length ? '' : 'disabled'}>Save</button>
            </div>
            <div class="psd-table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>value</th>
                            ${names.map((n) => `<th>${escapeHtml(n)}</th>`).join('')}
                            <th>ok</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${
                            rows.length
                                ? rows
                                      .map((r, idx) => {
                                          const f = Array.isArray(r.features) ? r.features : [];
                                          return `<tr class="${r.ok ? '' : 'is-err'}${idx === lastIdx ? ' is-latest' : ''}">
                                            <td>${(r.i ?? idx) + 1}</td>
                                            <td>${fmt(r.value)}</td>
                                            ${names.map((_, i) => `<td>${fmt(f[i])}</td>`).join('')}
                                            <td>${r.ok ? '✓' : '✗'}</td>
                                          </tr>`;
                                      })
                                      .join('')
                                : `<tr><td colspan="${3 + names.length}" style="color:#94a3b8;text-align:center;">No steps yet</td></tr>`
                        }
                    </tbody>
                </table>
            </div>
            ${trackStatsHtml(snap)}
        </section>
    `;

    paintChart(document.getElementById('psd-chart'), rows);
    paintComOverlay(snap);
    bindSaveScanButton(snap);
    if (pollHint) {
        const t = snap.updatedAt ? new Date(snap.updatedAt).toLocaleTimeString() : '';
        pollHint.textContent = `${statusLabel(snap.status)}${t ? ` · ${t}` : ''}`;
    }
    setError(snap.status === 'error' ? snap.statusText || 'Scan error' : '');
}

if (!scanId) {
    setError('Missing scan_id — start a scan from Twin to open this page with an id.');
    renderSnapshot(null);
} else {
    document.title = `Scan ${scanId.slice(0, 18)}… | Cloud Labs`;
    renderSnapshot(readParameterScanSnapshot(scanId));
    subscribeParameterScan(scanId, (snap) => renderSnapshot(snap));
}

if (getSelectedBackendId() && pollHint) {
    pollHint.title = `backend ${getSelectedBackendId()}`;
}

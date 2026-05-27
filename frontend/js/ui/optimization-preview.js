/**
 * Right-sidebar loss curve + live readout during autonomous OPTIMIZE.
 */
import { store } from '../state/store.js';
import { createOptimizeLiveReadoutGrid, optimizeReadoutMode, resolveOptimizeMotorIds } from '../optimize-live-readout.js';

let _canvas = null;
let _statusEl = null;
let _readout = null;

const PAD = { l: 40, r: 8, t: 12, b: 26 };

export function initOptimizationPreview() {
    _canvas = document.getElementById('optimization-loss-chart');
    _statusEl = document.getElementById('optimization-preview-status');
    const readoutSlot = document.getElementById('optimization-preview-readout-slot');
    if (readoutSlot && !_readout) {
        _readout = createOptimizeLiveReadoutGrid();
        readoutSlot.appendChild(_readout.root);
    }
    if (_canvas && typeof ResizeObserver !== 'undefined') {
        const ro = new ResizeObserver(() => drawOptimizationLossChart());
        ro.observe(_canvas);
    }
}

export function prepareOptimizationPreviewReadout() {
    syncPreviewReadout(null);
}

function syncPreviewReadout(tick) {
    if (!_readout) return;
    const tagId = store.optimizeActiveTarget || store.optimizeSession?.target_id;
    const mode = tagId ? optimizeReadoutMode(tagId) : undefined;
    if (tick) {
        _readout.update(tick, { mode });
    } else if (mode === 'motors' && tagId) {
        _readout.showIdle({ mode, motorIds: resolveOptimizeMotorIds(tagId) });
    } else {
        _readout.update(null, { clear: true });
    }
}

export function freezeOptimizationPreviewReadout() {
    if (!_readout) return;
    syncPreviewReadout(store.optimizeTick);
}

export function clearOptimizationPreviewReadout() {
    if (!_readout) return;
    _readout.update(null, { clear: true });
}

function syncCanvasDimensions() {
    if (!_canvas) return { w: 0, h: 0, ctx: null };
    const ctx = _canvas.getContext('2d');
    if (!ctx) return { w: 0, h: 0, ctx: null };

    const rect = _canvas.getBoundingClientRect();
    const cssW = Math.max(1, rect.width || _canvas.clientWidth || 320);
    const cssH = Math.max(1, rect.height || _canvas.clientHeight || 140);
    const dpr = window.devicePixelRatio || 1;
    const pxW = Math.round(cssW * dpr);
    const pxH = Math.round(cssH * dpr);

    if (_canvas.width !== pxW || _canvas.height !== pxH) {
        _canvas.width = pxW;
        _canvas.height = pxH;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w: cssW, h: cssH, ctx };
}

function formatAxisNumber(v) {
    if (!Number.isFinite(v)) return '';
    const a = Math.abs(v);
    if (a >= 100) return String(Math.round(v));
    if (a >= 10) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    if (a >= 0.01) return v.toFixed(3);
    return v.toExponential(1);
}

function iterationFromTick(tick, seriesLen) {
    const raw = Number(tick.iteration);
    if (Number.isFinite(raw)) return raw;
    return seriesLen + 1;
}

export function resetOptimizationPreview() {
    store.optimizeLossSeries = [];
    store.optimizeTick = null;
    if (_statusEl) {
        _statusEl.textContent = 'Idle — loss curve appears when optimization runs.';
        _statusEl.style.color = '#64748b';
    }
    clearOptimizationPreviewReadout();
    drawOptimizationLossChart();
}

export function pushOptimizationTick(tick) {
    if (!tick || typeof tick !== 'object') return;
    store.optimizeTick = tick;
    if (Number.isFinite(Number(tick.loss))) {
        store.optimizeLossSeries.push({
            iteration: iterationFromTick(tick, store.optimizeLossSeries.length),
            loss: Number(tick.loss),
        });
        if (store.optimizeLossSeries.length > 120) {
            store.optimizeLossSeries.shift();
        }
    }
    if (_statusEl) {
        _statusEl.textContent = 'Optimizing…';
        _statusEl.style.color = '#c4b5fd';
    }
    syncPreviewReadout(tick);
    drawOptimizationLossChart();
}

export function drawOptimizationLossChart() {
    const { w, h, ctx } = syncCanvasDimensions();
    if (!ctx || w <= 0 || h <= 0) return;

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#0f1115';
    ctx.fillRect(0, 0, w, h);

    const series = store.optimizeLossSeries || [];
    const plotW = w - PAD.l - PAD.r;
    const plotH = h - PAD.t - PAD.b;
    const plotLeft = PAD.l;
    const plotTop = PAD.t;
    const plotBottom = plotTop + plotH;

    if (!series.length) {
        ctx.fillStyle = '#475569';
        ctx.font = '11px Inter, sans-serif';
        ctx.fillText('Loss vs iteration', plotLeft, plotTop + 14);
        return;
    }

    const losses = series.map((p) => p.loss);
    const iters = series.map((p) => p.iteration);
    let minLoss = Math.min(...losses);
    let maxLoss = Math.max(...losses);
    const minIter = Math.min(...iters);
    const maxIter = Math.max(...iters);

    if (minLoss === maxLoss) {
        const bump = Math.max(0.001, Math.abs(minLoss) * 0.1 || 0.001);
        minLoss -= bump;
        maxLoss += bump;
    }

    const yMargin = (maxLoss - minLoss) * 0.1;
    const yLo = minLoss - yMargin;
    const yHi = maxLoss + yMargin;
    const ySpan = Math.max(1e-9, yHi - yLo);

    const iterSpan = Math.max(1, maxIter - minIter);

    const xFor = (iter) => {
        if (series.length === 1) return plotLeft + plotW * 0.5;
        return plotLeft + ((iter - minIter) / iterSpan) * plotW;
    };
    const yFor = (loss) => plotBottom - ((loss - yLo) / ySpan) * plotH;

    // Plot frame + light grid
    ctx.strokeStyle = '#2a2e36';
    ctx.lineWidth = 1;
    ctx.strokeRect(plotLeft, plotTop, plotW, plotH);

    ctx.strokeStyle = '#1e293b';
    ctx.beginPath();
    ctx.moveTo(plotLeft, plotTop + plotH * 0.5);
    ctx.lineTo(plotLeft + plotW, plotTop + plotH * 0.5);
    ctx.stroke();

    // Loss curve (clipped to plot area)
    ctx.save();
    ctx.beginPath();
    ctx.rect(plotLeft, plotTop, plotW, plotH);
    ctx.clip();

    ctx.beginPath();
    ctx.strokeStyle = '#a855f7';
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    series.forEach((pt, i) => {
        const x = xFor(pt.iteration);
        const y = yFor(pt.loss);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // End marker
    const last = series[series.length - 1];
    ctx.fillStyle = '#c4b5fd';
    ctx.beginPath();
    ctx.arc(xFor(last.iteration), yFor(last.loss), 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Axis labels
    ctx.fillStyle = '#94a3b8';
    ctx.font = '10px Inter, sans-serif';

    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(formatAxisNumber(yHi), plotLeft - 4, plotTop + 2);
    ctx.fillText(formatAxisNumber(yLo), plotLeft - 4, plotBottom - 2);

    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText(String(minIter), plotLeft, h - 6);
    ctx.textAlign = 'right';
    ctx.fillText(String(maxIter), plotLeft + plotW, h - 6);

    ctx.textAlign = 'center';
    ctx.fillStyle = '#64748b';
    ctx.fillText('iter', plotLeft + plotW * 0.5, h - 6);

    ctx.textAlign = 'left';
    ctx.fillStyle = '#64748b';
    ctx.save();
    ctx.translate(10, plotTop + plotH * 0.5);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('loss', 0, 0);
    ctx.restore();
}

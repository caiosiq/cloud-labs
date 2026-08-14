/**
 * Interactive OPTIMIZE loss chart — shared by Twin sidebar + /optimize-session.
 *
 * Axes: X grows in steps of 10 (0–10, 0–20, …); Y defaults to 0–2 and expands
 * only when data leaves that band. Scatter points are emphasized; connecting
 * lines stay thin. Hover shows eval + loss.
 */

/**
 * @typedef {{ step: number, loss: number, best?: number|null }} LossPoint
 */

/**
 * @param {LossPoint[]} points
 * @returns {{ xMax: number, yMin: number, yMax: number }}
 */
export function computeLossAxisRanges(points) {
    const steps = (points || [])
        .map((p) => Number(p.step))
        .filter((n) => Number.isFinite(n) && n >= 0);
    const maxStep = steps.length ? Math.max(...steps) : 0;
    const xMax = Math.max(10, Math.ceil(Math.max(maxStep, 1) / 10) * 10);

    const vals = [];
    (points || []).forEach((p) => {
        const loss = Number(p.loss);
        if (Number.isFinite(loss)) vals.push(loss);
        const best = Number(p.best);
        if (Number.isFinite(best)) vals.push(best);
    });

    let yMin = 0;
    let yMax = 2;
    if (vals.length) {
        const dataMin = Math.min(...vals);
        const dataMax = Math.max(...vals);
        if (dataMin < 0) {
            yMin = Math.floor(dataMin * 10) / 10;
        }
        if (dataMax > 2) {
            const pad = Math.max(0.05, (dataMax - Math.min(dataMin, 0)) * 0.08);
            yMax = Math.ceil((dataMax + pad) * 10) / 10;
        }
    }
    if (yMax <= yMin) yMax = yMin + 1;
    return { xMax, yMin, yMax };
}

function _cssVar(el, name, fallback) {
    try {
        const v = getComputedStyle(el).getPropertyValue(name).trim();
        return v || fallback;
    } catch (_) {
        return fallback;
    }
}

/**
 * Attach an interactive loss chart to a canvas. Safe to call repeatedly —
 * returns a controller; call setPoints() on updates.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{ compact?: boolean }} [opts]
 */
export function attachLossChart(canvas, opts = {}) {
    if (!canvas) return null;
    const compact = !!opts.compact;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    /** @type {LossPoint[]} */
    let points = [];
    let hoverIdx = -1;
    let ro = null;

    const pad = compact
        ? { l: 36, r: 10, t: 22, b: 26 }
        : { l: 44, r: 14, t: 28, b: 34 };

    function cssSize() {
        const rect = canvas.getBoundingClientRect();
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const cssW = Math.max(160, Math.floor(rect.width || canvas.clientWidth || 320));
        const cssH = Math.max(
            compact ? 120 : 200,
            Math.floor(rect.height || canvas.clientHeight || (compact ? 140 : 260)),
        );
        const need =
            canvas.width !== Math.round(cssW * dpr) ||
            canvas.height !== Math.round(cssH * dpr);
        if (need) {
            canvas.width = Math.round(cssW * dpr);
            canvas.height = Math.round(cssH * dpr);
            canvas.style.width = `${cssW}px`;
            canvas.style.height = `${cssH}px`;
        }
        return { cssW, cssH, dpr };
    }

    function plotGeom() {
        const { cssW, cssH, dpr } = cssSize();
        const plotW = cssW - pad.l - pad.r;
        const plotH = cssH - pad.t - pad.b;
        const ranges = computeLossAxisRanges(points);
        return { cssW, cssH, dpr, plotW, plotH, ...ranges };
    }

    function xy(step, loss, g) {
        const x = pad.l + (step / g.xMax) * g.plotW;
        const y = pad.t + g.plotH - ((loss - g.yMin) / (g.yMax - g.yMin)) * g.plotH;
        return { x, y };
    }

    function draw() {
        const g = plotGeom();
        ctx.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
        const bg = _cssVar(canvas, '--osd-chart-bg', '#0f141b');
        const grid = _cssVar(canvas, '--osd-chart-grid', '#1e293b');
        const axis = _cssVar(canvas, '--osd-chart-axis', '#64748b');
        const ink = _cssVar(canvas, '--osd-chart-ink', '#cbd5e1');
        const line = _cssVar(canvas, '--osd-chart-line', '#a78bfa');
        const bestLine = _cssVar(canvas, '--osd-chart-best', '#34d399');
        const dot = _cssVar(canvas, '--osd-chart-dot', '#c4b5fd');

        ctx.clearRect(0, 0, g.cssW, g.cssH);
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, g.cssW, g.cssH);

        // Title
        ctx.fillStyle = ink;
        ctx.font = compact
            ? '600 10px Inter, system-ui, sans-serif'
            : '600 12px Inter, system-ui, sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText('Loss vs eval', pad.l, 6);

        // Grid + Y ticks
        const yTicks = 4;
        ctx.font = compact
            ? '9px JetBrains Mono, ui-monospace, monospace'
            : '10px JetBrains Mono, ui-monospace, monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        for (let i = 0; i <= yTicks; i += 1) {
            const t = i / yTicks;
            const val = g.yMin + (g.yMax - g.yMin) * (1 - t);
            const y = pad.t + g.plotH * t;
            ctx.strokeStyle = grid;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(pad.l, y);
            ctx.lineTo(pad.l + g.plotW, y);
            ctx.stroke();
            ctx.fillStyle = axis;
            ctx.fillText(val.toFixed(val >= 10 || val <= -10 ? 1 : 2), pad.l - 6, y);
        }

        // X ticks every 10 (or half-range when small)
        const xStep = g.xMax <= 10 ? 2 : 10;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        for (let xVal = 0; xVal <= g.xMax; xVal += xStep) {
            const x = pad.l + (xVal / g.xMax) * g.plotW;
            ctx.strokeStyle = grid;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, pad.t);
            ctx.lineTo(x, pad.t + g.plotH);
            ctx.stroke();
            ctx.fillStyle = axis;
            ctx.fillText(String(xVal), x, pad.t + g.plotH + 6);
        }

        // Axis box
        ctx.strokeStyle = axis;
        ctx.lineWidth = 1;
        ctx.strokeRect(pad.l + 0.5, pad.t + 0.5, g.plotW - 1, g.plotH - 1);

        // Axis labels
        ctx.fillStyle = axis;
        ctx.font = compact
            ? '8px Inter, system-ui, sans-serif'
            : '9px Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('eval', pad.l + g.plotW / 2, g.cssH - 12);
        ctx.save();
        ctx.translate(12, pad.t + g.plotH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText('loss', 0, 0);
        ctx.restore();

        if (!points.length) {
            ctx.fillStyle = axis;
            ctx.font = compact
                ? '10px Inter, system-ui, sans-serif'
                : '12px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(
                'Waiting for evals…',
                pad.l + g.plotW / 2,
                pad.t + g.plotH / 2,
            );
            return;
        }

        const hasBest = points.some((p) => Number.isFinite(Number(p.best)));

        // Best-so-far thin line (behind)
        if (hasBest) {
            ctx.beginPath();
            ctx.strokeStyle = bestLine;
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 3]);
            let started = false;
            points.forEach((p) => {
                const b = Number(p.best);
                if (!Number.isFinite(b)) return;
                const { x, y } = xy(Number(p.step), b, g);
                if (!started) {
                    ctx.moveTo(x, y);
                    started = true;
                } else ctx.lineTo(x, y);
            });
            ctx.stroke();
            ctx.setLineDash([]);
        }

        // Per-eval loss thin polyline
        ctx.beginPath();
        ctx.strokeStyle = line;
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.85;
        points.forEach((p, i) => {
            const { x, y } = xy(Number(p.step), Number(p.loss), g);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.globalAlpha = 1;

        // Scatter points
        const r = compact ? 2.6 : 3.4;
        points.forEach((p, i) => {
            const { x, y } = xy(Number(p.step), Number(p.loss), g);
            ctx.beginPath();
            ctx.fillStyle = i === hoverIdx ? '#fbbf24' : dot;
            ctx.arc(x, y, i === hoverIdx ? r + 1.2 : r, 0, Math.PI * 2);
            ctx.fill();
            if (i === hoverIdx) {
                ctx.strokeStyle = '#fde68a';
                ctx.lineWidth = 1.5;
                ctx.stroke();
            }
        });

        // Legend
        ctx.font = compact
            ? '8px Inter, system-ui, sans-serif'
            : '9px Inter, system-ui, sans-serif';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'top';
        let lx = pad.l + g.plotW;
        const ly = 6;
        ctx.fillStyle = dot;
        ctx.fillText('● loss', lx, ly);
        if (hasBest) {
            ctx.fillStyle = bestLine;
            ctx.fillText('— best', lx - (compact ? 42 : 52), ly);
        }

        // Hover tooltip
        if (hoverIdx >= 0 && hoverIdx < points.length) {
            const p = points[hoverIdx];
            const { x, y } = xy(Number(p.step), Number(p.loss), g);
            const bestBit =
                Number.isFinite(Number(p.best))
                    ? ` · best ${Number(p.best).toFixed(4)}`
                    : '';
            const label = `eval ${p.step} · loss ${Number(p.loss).toFixed(4)}${bestBit}`;
            ctx.font = compact
                ? '10px JetBrains Mono, ui-monospace, monospace'
                : '11px JetBrains Mono, ui-monospace, monospace';
            const tw = ctx.measureText(label).width + 12;
            const th = compact ? 20 : 22;
            let tx = x - tw / 2;
            let ty = y - th - 10;
            if (tx < pad.l) tx = pad.l;
            if (tx + tw > pad.l + g.plotW) tx = pad.l + g.plotW - tw;
            if (ty < 4) ty = y + 12;
            ctx.fillStyle = 'rgba(15, 23, 42, 0.92)';
            ctx.strokeStyle = '#334155';
            ctx.lineWidth = 1;
            ctx.beginPath();
            const rr = 4;
            ctx.moveTo(tx + rr, ty);
            ctx.arcTo(tx + tw, ty, tx + tw, ty + th, rr);
            ctx.arcTo(tx + tw, ty + th, tx, ty + th, rr);
            ctx.arcTo(tx, ty + th, tx, ty, rr);
            ctx.arcTo(tx, ty, tx + tw, ty, rr);
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = '#e2e8f0';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(label, tx + 6, ty + th / 2);
        }
    }

    function nearestIndex(cssX, cssY) {
        if (!points.length) return -1;
        const g = plotGeom();
        let best = -1;
        let bestD = 14; // px hit radius
        points.forEach((p, i) => {
            const { x, y } = xy(Number(p.step), Number(p.loss), g);
            const d = Math.hypot(x - cssX, y - cssY);
            if (d < bestD) {
                bestD = d;
                best = i;
            }
        });
        return best;
    }

    function onPointer(ev) {
        const rect = canvas.getBoundingClientRect();
        const x = ev.clientX - rect.left;
        const y = ev.clientY - rect.top;
        const next = nearestIndex(x, y);
        if (next !== hoverIdx) {
            hoverIdx = next;
            canvas.style.cursor = next >= 0 ? 'crosshair' : 'default';
            draw();
        }
    }

    function onLeave() {
        if (hoverIdx !== -1) {
            hoverIdx = -1;
            canvas.style.cursor = 'default';
            draw();
        }
    }

    canvas.addEventListener('pointermove', onPointer);
    canvas.addEventListener('pointerleave', onLeave);
    canvas.addEventListener('pointerdown', onPointer);

    if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(() => draw());
        ro.observe(canvas);
        if (canvas.parentElement) ro.observe(canvas.parentElement);
    }

    draw();

    return {
        canvas,
        /** @param {LossPoint[]} next */
        setPoints(next) {
            points = Array.isArray(next)
                ? next
                      .map((p) => ({
                          step: Number(p.step),
                          loss: Number(p.loss),
                          best:
                              p.best != null && Number.isFinite(Number(p.best))
                                  ? Number(p.best)
                                  : null,
                      }))
                      .filter(
                          (p) =>
                              Number.isFinite(p.step) && Number.isFinite(p.loss),
                      )
                : [];
            if (hoverIdx >= points.length) hoverIdx = -1;
            draw();
        },
        redraw: draw,
        destroy() {
            canvas.removeEventListener('pointermove', onPointer);
            canvas.removeEventListener('pointerleave', onLeave);
            canvas.removeEventListener('pointerdown', onPointer);
            ro?.disconnect();
            ro = null;
        },
    };
}

/**
 * Build chart points from an ensemble trace (eval / loss / best_loss).
 * @param {object[]} rows
 * @returns {LossPoint[]}
 */
export function lossPointsFromTrace(rows) {
    return (rows || [])
        .map((row) => {
            const step = Number(row.eval);
            const loss = Number(row.loss);
            const best =
                row.best_loss != null && Number.isFinite(Number(row.best_loss))
                    ? Number(row.best_loss)
                    : null;
            return { step, loss, best };
        })
        .filter((p) => Number.isFinite(p.step) && Number.isFinite(p.loss));
}

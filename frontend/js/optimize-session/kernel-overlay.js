/**
 * Kernel result overlays on OPTIMIZE sparse camera previews.
 * Pure UI — consumes stage debug (kernel/policy) already on the trace; does
 * not re-run kernels or touch the closed-loop path.
 */

/**
 * @typedef {{
 *   kind: string,
 *   termId: string,
 *   kernelId?: string,
 *   metric?: string,
 *   detected?: {x:number,y:number}|null,
 *   target?: {x:number,y:number}|null,
 *   peak?: number|null,
 *   sigmaX?: number|null,
 *   sigmaY?: number|null,
 *   amplitude?: number|null,
 *   frameHw?: [number, number]|null,
 *   presenceOk?: boolean|null,
 * }} KernelOverlay
 */

function _xyFromTarget(raw) {
    if (!raw) return null;
    if (Array.isArray(raw) && raw.length >= 2) {
        const x = Number(raw[0]);
        const y = Number(raw[1]);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    }
    if (typeof raw === 'object') {
        const x = Number(raw.x ?? raw[0]);
        const y = Number(raw.y ?? raw[1]);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    }
    return null;
}

function _frameHwFromRow(row) {
    const fov = row?.stages?.policy?.fov;
    if (fov && typeof fov === 'object') {
        const h = Number(fov.height);
        const w = Number(fov.width);
        if (Number.isFinite(h) && Number.isFinite(w) && h > 0 && w > 0) {
            return /** @type {[number, number]} */ ([h, w]);
        }
    }
    const frames = row?.stages?.capture?.frames;
    if (Array.isArray(frames)) {
        for (const fr of frames) {
            const shape = fr?.shape;
            if (Array.isArray(shape) && shape.length >= 2) {
                const h = Number(shape[0]);
                const w = Number(shape[1]);
                if (Number.isFinite(h) && Number.isFinite(w) && h > 0 && w > 0) {
                    return /** @type {[number, number]} */ ([h, w]);
                }
            }
        }
    }
    return null;
}

function _isCentroidish(kernelId, metric) {
    const k = String(kernelId || '').toLowerCase();
    const m = String(metric || '').toLowerCase();
    if (m === 'rms_distance' || m === 'rms_distance_px' || m === 'signed_axis_offset') {
        return true;
    }
    if (k.includes('roi_centroid') || k.includes('centroid') || k.includes('beam_com')) {
        return true;
    }
    if (k.includes('beam_shift')) return true;
    return false;
}

function _isGaussianish(kernelId) {
    return String(kernelId || '').toLowerCase().includes('gaussian');
}

/**
 * Build overlay descriptors for one eval/trace row.
 * @param {object|null|undefined} row
 * @returns {KernelOverlay[]}
 */
export function extractKernelOverlays(row) {
    if (!row || typeof row !== 'object') return [];
    const kernelTerms =
        row.stages?.kernel?.terms && typeof row.stages.kernel.terms === 'object'
            ? row.stages.kernel.terms
            : {};
    const policyTerms =
        row.stages?.policy?.terms && typeof row.stages.policy.terms === 'object'
            ? row.stages.policy.terms
            : {};
    const frameHw = _frameHwFromRow(row);
    const ids = new Set([
        ...Object.keys(kernelTerms),
        ...Object.keys(policyTerms),
    ]);
    /** @type {KernelOverlay[]} */
    const out = [];
    ids.forEach((termId) => {
        const kt = kernelTerms[termId] || {};
        const pt = policyTerms[termId] || {};
        const kernelId = kt.kernel_id || pt.kernel_id || '';
        const metric = kt.metric || pt.metric || '';
        const preview = kt.preview || pt.features_preview || [];

        if (_isGaussianish(kernelId) && Array.isArray(preview) && preview.length >= 5) {
            const cx = Number(preview[1]);
            const cy = Number(preview[2]);
            const sigmaX = Number(preview[3]);
            const sigmaY = Number(preview[4]);
            if (
                Number.isFinite(cx) &&
                Number.isFinite(cy) &&
                Number.isFinite(sigmaX) &&
                Number.isFinite(sigmaY)
            ) {
                const amp = Number(preview[0]);
                out.push({
                    kind: 'gaussian_moments',
                    termId,
                    kernelId,
                    metric,
                    detected: { x: cx, y: cy },
                    sigmaX,
                    sigmaY,
                    amplitude: Number.isFinite(amp) ? amp : null,
                    frameHw:
                        Array.isArray(kt.frame_hw) && kt.frame_hw.length >= 2
                            ? /** @type {[number, number]} */ ([
                                  Number(kt.frame_hw[0]),
                                  Number(kt.frame_hw[1]),
                              ])
                            : frameHw,
                    presenceOk: null,
                });
            }
            return;
        }

        if (!_isCentroidish(kernelId, metric)) return;

        let detected = null;
        if (Array.isArray(kt.detected_xy) && kt.detected_xy.length >= 2) {
            detected = _xyFromTarget(kt.detected_xy);
        } else if (Array.isArray(pt.detected_xy) && pt.detected_xy.length >= 2) {
            detected = _xyFromTarget(pt.detected_xy);
        } else if (Array.isArray(preview) && preview.length >= 2) {
            detected = _xyFromTarget(preview);
        }
        const target =
            _xyFromTarget(kt.origin_px) ||
            _xyFromTarget(kt.target_px) ||
            _xyFromTarget(kt.target) ||
            _xyFromTarget(pt.origin_px) ||
            _xyFromTarget(pt.target_px) ||
            _xyFromTarget(pt.target);
        const peakRaw = kt.preview?.[2] ?? pt.features_preview?.[2] ?? pt.peak;
        const peak = peakRaw != null && Number.isFinite(Number(peakRaw)) ? Number(peakRaw) : null;
        const presenceOk =
            typeof pt.presence_ok === 'boolean' ? pt.presence_ok : null;
        const termHw =
            Array.isArray(kt.frame_hw) && kt.frame_hw.length >= 2
                ? /** @type {[number, number]} */ ([
                      Number(kt.frame_hw[0]),
                      Number(kt.frame_hw[1]),
                  ])
                : frameHw;
        if (!detected && !target) return;
        out.push({
            kind: 'centroid_target',
            termId,
            kernelId,
            metric,
            detected,
            target,
            peak,
            frameHw: termHw,
            presenceOk,
        });
    });
    return out;
}

/**
 * @param {KernelOverlay[]} overlays
 */
export function overlayLegendHtml(overlays) {
    if (!overlays?.length) {
        return `<div class="osd-overlay-legend osd-idle">No kernel overlay for this frame</div>`;
    }
    return `<div class="osd-overlay-legend">${overlays
        .map((o) => {
            if (o.kind === 'gaussian_moments') {
                const det = o.detected
                    ? `(${o.detected.x.toFixed(1)}, ${o.detected.y.toFixed(1)})`
                    : '—';
                const sig =
                    o.sigmaX != null && o.sigmaY != null
                        ? `σ=(${Number(o.sigmaX).toFixed(1)}, ${Number(o.sigmaY).toFixed(1)})`
                        : '';
                return `<span class="osd-overlay-chip"><i class="osd-dot osd-dot--detected"></i> moments ${det} ${sig} <small>${o.termId}</small></span>`;
            }
            const det = o.detected
                ? `(${o.detected.x.toFixed(1)}, ${o.detected.y.toFixed(1)})`
                : '—';
            const tgt = o.target
                ? `(${o.target.x.toFixed(1)}, ${o.target.y.toFixed(1)})`
                : '—';
            const peak =
                o.peak != null ? ` · peak ${Number(o.peak).toFixed(3)}` : '';
            const absent = o.presenceOk === false ? ' · ABSENT' : '';
            const isOrigin =
                String(o.metric || '').includes('signed_axis') ||
                String(o.kernelId || '').includes('beam_com');
            const tgtChip = o.target
                ? `<i class="osd-dot osd-dot--target"></i> ${isOrigin ? 'origin' : 'target'} ${tgt}`
                : '';
            return `<span class="osd-overlay-chip"><i class="osd-dot osd-dot--detected"></i> detected ${det} ${tgtChip} <small>${o.termId}${peak}${absent}</small></span>`;
        })
        .join('')}</div>`;
}

/**
 * Draw overlays onto a canvas sized to the displayed image box.
 * @param {HTMLCanvasElement} canvas
 * @param {HTMLImageElement} img
 * @param {KernelOverlay[]} overlays
 */
export function paintKernelOverlays(canvas, img, overlays) {
    if (!canvas || !img) return;
    const rect = img.getBoundingClientRect();
    const cssW = Math.max(1, Math.floor(rect.width || img.clientWidth || 1));
    const cssH = Math.max(1, Math.floor(rect.height || img.clientHeight || 1));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
        canvas.style.width = `${cssW}px`;
        canvas.style.height = `${cssH}px`;
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const natW = img.naturalWidth || cssW;
    const natH = img.naturalHeight || cssH;
    if (!natW || !natH) return;

    // object-fit: contain mapping from natural pixels → CSS box
    const scale = Math.min(cssW / natW, cssH / natH);
    const drawW = natW * scale;
    const drawH = natH * scale;
    const offsetX = (cssW - drawW) / 2;
    const offsetY = (cssH - drawH) / 2;

    (overlays || []).forEach((o) => {
        const fw = o.frameHw?.[1] || natW; // width
        const fh = o.frameHw?.[0] || natH; // height
        const sx = natW / fw;
        const sy = natH / fh;

        const toCanvas = (pt) => {
            if (!pt) return null;
            const nx = pt.x * sx;
            const ny = pt.y * sy;
            return {
                x: offsetX + nx * scale,
                y: offsetY + ny * scale,
            };
        };
        const lenToCanvas = (px) => Math.max(1, Number(px) * sx * scale);

        if (o.kind === 'gaussian_moments') {
            const det = toCanvas(o.detected);
            if (!det) return;
            const rx = lenToCanvas(o.sigmaX ?? 0);
            const ry = lenToCanvas(o.sigmaY ?? 0);
            ctx.strokeStyle = 'rgba(167, 243, 208, 0.95)';
            ctx.lineWidth = 1.75;
            ctx.beginPath();
            ctx.ellipse(det.x, det.y, rx, ry, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.strokeStyle = 'rgba(52, 211, 153, 0.55)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.ellipse(det.x, det.y, rx * 2, ry * 2, 0, 0, Math.PI * 2);
            ctx.stroke();
            _drawCross(ctx, det.x, det.y, {
                color: '#34d399',
                size: 12,
                label: 'μ',
            });
            return;
        }

        if (o.kind !== 'centroid_target') return;

        const det = toCanvas(o.detected);
        const tgt = toCanvas(o.target);
        const absent = o.presenceOk === false;

        if (det && tgt) {
            ctx.strokeStyle = 'rgba(148, 163, 184, 0.75)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 3]);
            ctx.beginPath();
            ctx.moveTo(det.x, det.y);
            ctx.lineTo(tgt.x, tgt.y);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        if (tgt) {
            _drawCross(ctx, tgt.x, tgt.y, {
                color: '#38bdf8',
                size: 14,
                label: 'target',
            });
            _drawRing(ctx, tgt.x, tgt.y, 10, '#38bdf8');
        }
        if (det) {
            _drawCross(ctx, det.x, det.y, {
                color: absent ? '#f87171' : '#fbbf24',
                size: 16,
                label: absent ? 'absent' : 'detected',
            });
            _drawRing(ctx, det.x, det.y, 7, absent ? '#f87171' : '#fbbf24');
        }
    });
}

function _drawCross(ctx, x, y, { color, size, label }) {
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.75;
    ctx.beginPath();
    ctx.moveTo(x - size, y);
    ctx.lineTo(x + size, y);
    ctx.moveTo(x, y - size);
    ctx.lineTo(x, y + size);
    ctx.stroke();
    if (label) {
        ctx.font = '10px Inter, system-ui, sans-serif';
        ctx.textBaseline = 'bottom';
        ctx.fillText(label, x + 8, y - 8);
    }
}

function _drawRing(ctx, x, y, r, color) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();
}

/**
 * Bind overlay painting to a camera stage inside ``root``.
 * @param {ParentNode} root
 * @param {KernelOverlay[]} overlays
 */
export function bindKernelOverlayStage(root, overlays) {
    const stage = root.querySelector?.('.osd-camera-stage') || root.querySelector('.osd-camera-stage');
    if (!stage) return null;
    const img = stage.querySelector('img');
    const canvas = stage.querySelector('canvas.osd-kernel-overlay');
    if (!img || !canvas) return null;

    const list = Array.isArray(overlays) ? overlays : [];
    const redraw = () => paintKernelOverlays(canvas, img, list);

    if (img.complete && img.naturalWidth) {
        redraw();
    } else {
        img.addEventListener('load', redraw, { once: true });
    }

    let ro = null;
    if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(() => redraw());
        ro.observe(stage);
    }
    window.addEventListener('resize', redraw);

    return {
        redraw,
        destroy() {
            ro?.disconnect();
            window.removeEventListener('resize', redraw);
        },
    };
}

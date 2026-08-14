/**
 * Under each compatible measurable: list premade kernels, run EVAL_KERNEL,
 * show scalar/features, and (for centroid-ish results) overlay on ImageViewer.
 */
import {
    getEdgeKernelsCached,
    kernelsForMeasurableField,
    probeKernel,
} from '../api/kernels.js';
import {
    overlayLegendHtml,
    paintKernelOverlays,
} from '../optimize-session/kernel-overlay.js';

/**
 * Build UI overlays from a probe result (not OPTIMIZE stage debug).
 * @param {{
 *   kernelId: string,
 *   features?: number[],
 *   featureNames?: string[],
 *   frameHw?: [number, number]|null,
 *   natW?: number,
 *   natH?: number,
 * }} args
 */
export function overlaysFromProbeResult(args) {
    const kernelId = String(args.kernelId || '');
    const names = Array.isArray(args.featureNames) ? args.featureNames : [];
    const feats = Array.isArray(args.features)
        ? args.features.map((x) => Number(x))
        : [];
    const get = (name) => {
        const i = names.indexOf(name);
        if (i >= 0 && i < feats.length && Number.isFinite(feats[i])) return feats[i];
        return null;
    };

    const natW = Number(args.natW) || 0;
    const natH = Number(args.natH) || 0;
    let fh = args.frameHw?.[0];
    let fw = args.frameHw?.[1];
    if (!(Number.isFinite(fh) && Number.isFinite(fw) && fh > 0 && fw > 0)) {
        fh = natH || 0;
        fw = natW || 0;
    }
    if (!(fh > 0 && fw > 0)) return [];

    const frameHw = /** @type {[number, number]} */ ([fh, fw]);

    // Gaussian moments: center + σ ellipse (no fake target).
    const sx = get('sigma_x');
    const sy = get('sigma_y');
    const gcx = get('cx');
    const gcy = get('cy');
    if (
        (kernelId.includes('gaussian') || (Number.isFinite(sx) && Number.isFinite(sy))) &&
        Number.isFinite(gcx) &&
        Number.isFinite(gcy) &&
        Number.isFinite(sx) &&
        Number.isFinite(sy)
    ) {
        return [
            {
                kind: 'gaussian_moments',
                termId: 'probe',
                kernelId,
                metric: 'probe',
                detected: { x: gcx, y: gcy },
                sigmaX: sx,
                sigmaY: sy,
                amplitude: get('amplitude'),
                frameHw,
                presenceOk: null,
            },
        ];
    }

    const peak = get('peak');
    const cx = get('cx');
    const cy = get('cy');
    if (Number.isFinite(cx) && Number.isFinite(cy)) {
        // Centroid align: show FOV center as reference only for roi_centroid.
        const showTarget =
            kernelId.includes('roi_centroid') || kernelId.includes('centroid');
        return [
            {
                kind: 'centroid_target',
                termId: 'probe',
                kernelId,
                metric: 'probe',
                detected: { x: cx, y: cy },
                target: showTarget ? { x: fw / 2, y: fh / 2 } : null,
                peak,
                frameHw,
                presenceOk: null,
            },
        ];
    }

    const dx = get('dx');
    const dy = get('dy');
    if (Number.isFinite(dx) && Number.isFinite(dy)) {
        return [
            {
                kind: 'centroid_target',
                termId: 'probe',
                kernelId,
                metric: 'probe',
                detected: { x: fw / 2 + dx, y: fh / 2 + dy },
                target: { x: fw / 2, y: fh / 2 },
                peak: null,
                frameHw,
                presenceOk: null,
            },
        ];
    }

    // Fallback: first two floats as (cx, cy) for unnamed centroid kernels
    if (
        (kernelId.includes('centroid') || kernelId.includes('roi_centroid')) &&
        feats.length >= 2 &&
        Number.isFinite(feats[0]) &&
        Number.isFinite(feats[1])
    ) {
        return [
            {
                kind: 'centroid_target',
                termId: 'probe',
                kernelId,
                metric: 'probe',
                detected: { x: feats[0], y: feats[1] },
                target: { x: fw / 2, y: fh / 2 },
                peak: feats.length >= 3 && Number.isFinite(feats[2]) ? feats[2] : null,
                frameHw,
                presenceOk: null,
            },
        ];
    }

    return [];
}

function _fmtNum(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return '—';
    if (Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.001)) {
        return x.toExponential(3);
    }
    return Number.isInteger(x) ? String(x) : x.toFixed(4).replace(/\.?0+$/, '');
}

function _formatProbeResult(result, meta) {
    if (!result || typeof result !== 'object') return '—';
    const kind = String(result.kind || '');
    if (kind === 'features' || Array.isArray(result.features)) {
        const feats = Array.isArray(result.features) ? result.features : [];
        const names =
            (Array.isArray(meta?.feature_names) && meta.feature_names) ||
            (Array.isArray(result.feature_names) && result.feature_names) ||
            [];
        if (names.length && names.length === feats.length) {
            return names
                .map((n, i) => `${n}=${_fmtNum(feats[i])}`)
                .join(' · ');
        }
        return `[${feats.map(_fmtNum).join(', ')}]`;
    }
    if (result.scalar != null || kind === 'scalar') {
        return `scalar=${_fmtNum(result.scalar)}`;
    }
    return JSON.stringify(result);
}

function _stageFromCard(card) {
    if (!card) return null;
    const stage = card.querySelector('.meas-kernel-stage');
    if (!stage) return null;
    const img = stage.querySelector('img');
    const canvas = stage.querySelector('canvas.meas-kernel-overlay');
    if (!img || !canvas) return null;
    return { stage, img, canvas };
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

/**
 * Append a kernel probe strip under a measurable widget card.
 *
 * @param {{
 *   tagId: string,
 *   fieldName: string,
 *   descriptor?: object,
 *   value?: object|null,
 *   card: HTMLElement,
 * }} opts
 */
export function mountMeasurableKernelStrip(opts) {
    const { tagId, fieldName, descriptor, value, card } = opts;
    if (!card || !(card instanceof HTMLElement)) return;

    const strip = document.createElement('div');
    strip.className = 'meas-kernel-strip';
    strip.dataset.field = fieldName;

    const title = document.createElement('div');
    title.className = 'meas-kernel-strip__title';
    title.textContent = 'Kernels (probe)';
    strip.appendChild(title);

    const chips = document.createElement('div');
    chips.className = 'meas-kernel-strip__chips';
    strip.appendChild(chips);

    const status = document.createElement('div');
    status.className = 'meas-kernel-strip__status';
    status.textContent = 'Loading catalog…';
    strip.appendChild(status);

    const resultEl = document.createElement('div');
    resultEl.className = 'meas-kernel-strip__result';
    strip.appendChild(resultEl);

    const legendEl = document.createElement('div');
    legendEl.className = 'meas-kernel-strip__legend';
    strip.appendChild(legendEl);

    card.appendChild(strip);

    let busy = false;
    /** @type {ReturnType<typeof setTimeout>|null} */
    let redrawTimer = null;

    const paint = (overlays) => {
        const st = _stageFromCard(card);
        if (!st) {
            legendEl.innerHTML = '';
            return;
        }
        const list = Array.isArray(overlays) ? overlays : [];
        const redraw = () => paintKernelOverlays(st.canvas, st.img, list);
        if (st.img.complete && st.img.naturalWidth) redraw();
        else st.img.addEventListener('load', redraw, { once: true });
        if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(() => {
                if (redrawTimer) clearTimeout(redrawTimer);
                redrawTimer = setTimeout(redraw, 50);
            });
            ro.observe(st.stage);
        }
        legendEl.innerHTML = list.length
            ? overlayLegendHtml(list).replace(/osd-overlay-legend/g, 'meas-kernel-legend')
            : '';
        if (list.length) {
            const note = document.createElement('div');
            note.className = 'meas-kernel-strip__hint';
            note.textContent =
                'Overlay on displayed record; probe uses a fresh edge latch (may differ by a frame). Target = FOV center.';
            legendEl.appendChild(note);
        }
    };

    const runProbe = async (kernel) => {
        if (busy) return;
        busy = true;
        const kid = String(kernel.id || '');
        status.textContent = `Probing ${kid}…`;
        status.classList.remove('meas-kernel-strip__status--err');
        resultEl.textContent = '';
        legendEl.innerHTML = '';
        chips.querySelectorAll('button').forEach((b) => {
            b.disabled = true;
        });

        const out = await probeKernel(tagId, {
            kernel_id: kid,
            field: fieldName,
        });

        chips.querySelectorAll('button').forEach((b) => {
            b.disabled = false;
        });
        busy = false;

        if (!out.ok) {
            status.textContent = '';
            status.classList.add('meas-kernel-strip__status--err');
            status.textContent = out.error || 'probe failed';
            paint([]);
            return;
        }

        const result = out.result || {};
        status.textContent = `OK · ${kid}`;
        resultEl.textContent = _formatProbeResult(result, kernel);

        const feats = Array.isArray(result.features)
            ? result.features
            : result.scalar != null
              ? [Number(result.scalar)]
              : [];
        const st = _stageFromCard(card);
        const overlays = overlaysFromProbeResult({
            kernelId: kid,
            features: Array.isArray(result.features) ? result.features : feats,
            featureNames: kernel.feature_names || result.feature_names,
            frameHw: _shapeHw(value),
            natW: st?.img?.naturalWidth,
            natH: st?.img?.naturalHeight,
        });
        paint(overlays);
    };

    getEdgeKernelsCached().then((catalog) => {
        if (!catalog.ok) {
            status.classList.add('meas-kernel-strip__status--err');
            status.textContent = catalog.error || 'kernel catalog unavailable';
            return;
        }
        const list = kernelsForMeasurableField(
            catalog.kernels || [],
            fieldName,
            descriptor || {},
        );
        if (!list.length) {
            status.textContent = 'No premade kernels for this field';
            return;
        }
        status.textContent = 'Click a kernel to run EVAL_KERNEL (outside OPTIMIZE)';
        list.forEach((k) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'meas-kernel-chip';
            btn.title =
                (k.description && String(k.description)) ||
                `${k.id} · ${k.output_kind || 'kernel'}`;
            const label = document.createElement('span');
            label.className = 'meas-kernel-chip__label';
            label.textContent = k.label || k.id;
            btn.appendChild(label);
            const id = document.createElement('span');
            id.className = 'meas-kernel-chip__id';
            id.textContent = k.id;
            btn.appendChild(id);
            btn.addEventListener('click', (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                runProbe(k);
            });
            chips.appendChild(btn);
        });
    });
}

/**
 * Fit the Twin optical-table canvas into #canvas-wrapper while keeping aspect
 * and updating lab mm ↔ px scale (see config.setCanvasPixelSize).
 */
import {
    CANVAS_ASPECT_H,
    CANVAS_ASPECT_W,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    setCanvasPixelSize,
} from '../config.js';

let _canvas = null;
let _onFitted = null;
let _ro = null;
let _pending = false;

function measureBox() {
    const wrap = document.getElementById('canvas-wrapper');
    if (!wrap) return null;
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (w < 80 || h < 80) return null;
    return { w, h };
}

function chooseSize(boxW, boxH) {
    const aspect = CANVAS_ASPECT_W / CANVAS_ASPECT_H;
    let w = boxW;
    let h = w / aspect;
    if (h > boxH) {
        h = boxH;
        w = h * aspect;
    }
    w = Math.max(1, Math.floor(w));
    h = Math.max(1, Math.floor(w / aspect));
    if (h > boxH) {
        h = Math.max(1, Math.floor(boxH));
        w = Math.max(1, Math.floor(h * aspect));
    }
    // Leave a 1px safety so borders don't clip.
    if (w > boxW) w = Math.floor(boxW);
    if (h > boxH) h = Math.floor(boxH);
    return { w, h };
}

export function fitOpticalTableCanvas() {
    if (!_canvas) {
        _canvas = document.getElementById('optical-table');
    }
    if (!_canvas) return false;
    const box = measureBox();
    if (!box) return false;
    const { w, h } = chooseSize(box.w, box.h);
    const changed = setCanvasPixelSize(w, h);
    let attrsChanged = false;
    if (_canvas.width !== CANVAS_WIDTH || _canvas.height !== CANVAS_HEIGHT) {
        _canvas.width = CANVAS_WIDTH;
        _canvas.height = CANVAS_HEIGHT;
        attrsChanged = true;
    }
    if (changed || attrsChanged) {
        if (typeof _onFitted === 'function') _onFitted();
        return true;
    }
    return false;
}

function scheduleFit() {
    if (_pending) return;
    _pending = true;
    requestAnimationFrame(() => {
        _pending = false;
        fitOpticalTableCanvas();
    });
}

/**
 * @param {{ onFitted?: () => void }} [opts]
 */
export function initFitCanvas(opts = {}) {
    _onFitted = typeof opts.onFitted === 'function' ? opts.onFitted : null;
    _canvas = document.getElementById('optical-table');
    fitOpticalTableCanvas();
    window.addEventListener('resize', scheduleFit);
    window.addEventListener('cloudlabs:layout', scheduleFit);
    const wrap = document.getElementById('canvas-wrapper');
    if (wrap && typeof ResizeObserver !== 'undefined') {
        _ro = new ResizeObserver(() => scheduleFit());
        _ro.observe(wrap);
    }
}

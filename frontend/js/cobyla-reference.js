/**
 * Cobyla reference image management.
 *
 * The server holds one BGR ndarray used by `CobylaAlignmentStrategy.reference_image`. The UI
 * exposes four ways to update it:
 *   - "Set from Latest capture"  → uploads the currently-shown table cam PNG.
 *   - "Clear"                    → DELETE /api/cobyla-reference-image.
 *   - "Save"                     → download server's current reference as cobyla-reference.png.
 *   - "Load file"                → POST a user-selected PNG.
 *
 * Endpoints:
 *   GET    /api/cobyla-reference-image          PNG bytes (or 404 if unset)
 *   GET    /api/cobyla-reference-image/status   { set, width, height }
 *   POST   /api/cobyla-reference-image          (Content-Type: image/png)
 *   DELETE /api/cobyla-reference-image
 */
import { store } from './state/store.js';
import { log } from './ui/log.js';

let _tableCamImg = null;
let _refs = null; // resolved DOM refs

/**
 * Wire DOM nodes + the source `<img>` from the table-cam panel. Call once at boot.
 * @param {{ tableCamImg: HTMLImageElement | null }} deps
 */
export function initCobylaReference(deps) {
    _tableCamImg = (deps && deps.tableCamImg) || null;
    _refs = {
        setBtn: document.getElementById('table-cam-cobyla-ref-btn'),
        clearBtn: document.getElementById('table-cam-cobyla-clear-btn'),
        saveBtn: document.getElementById('cobyla-ref-save-btn'),
        loadBtn: document.getElementById('cobyla-ref-load-btn'),
        fileInput: document.getElementById('cobyla-ref-file-input'),
        statusEl: document.getElementById('cobyla-ref-status'),
        previewImg: document.getElementById('cobyla-ref-preview-img'),
        placeholderEl: document.getElementById('cobyla-ref-placeholder'),
    };
    bindHandlers();
    syncCobylaRefUi();
    // Periodically reconcile UI with server state — handles cases where another client cleared the reference.
    setInterval(async () => {
        const isSet = await refreshCobylaRefStatus();
        if (!isSet && _refs.previewImg) {
            revokeCobylaRefPreviewUrl();
            _refs.previewImg.src = '';
            setCobylaRefPreviewVisible(false);
        }
    }, 8000);
}

function revokeCobylaRefPreviewUrl() {
    if (store.cobylaRefPreviewObjectUrl) {
        URL.revokeObjectURL(store.cobylaRefPreviewObjectUrl);
        store.cobylaRefPreviewObjectUrl = null;
    }
}

function setCobylaRefPreviewVisible(hasImage) {
    if (_refs.previewImg && _refs.placeholderEl) {
        _refs.previewImg.style.display = hasImage ? 'block' : 'none';
        _refs.placeholderEl.style.display = hasImage ? 'none' : 'block';
    }
}

/** Load stored reference PNG into the red-bordered preview (Latest capture unchanged). */
async function refreshCobylaRefPreview() {
    if (!_refs.previewImg) return;
    revokeCobylaRefPreviewUrl();
    _refs.previewImg.src = '';
    try {
        const r = await fetch(`/api/cobyla-reference-image?t=${Date.now()}`);
        if (!r.ok) {
            setCobylaRefPreviewVisible(false);
            return;
        }
        const blob = await r.blob();
        store.cobylaRefPreviewObjectUrl = URL.createObjectURL(blob);
        _refs.previewImg.src = store.cobylaRefPreviewObjectUrl;
        setCobylaRefPreviewVisible(true);
    } catch {
        setCobylaRefPreviewVisible(false);
    }
}

/** @returns {Promise<boolean>} whether a reference is set on the server */
async function refreshCobylaRefStatus() {
    if (!_refs.statusEl) return false;
    try {
        const r = await fetch('/api/cobyla-reference-image/status');
        if (!r.ok) {
            _refs.statusEl.textContent = 'Cobyla ref: status unavailable';
            if (_refs.saveBtn) _refs.saveBtn.disabled = true;
            return false;
        }
        const d = await r.json();
        if (d.set && d.width && d.height) {
            _refs.statusEl.textContent = `Cobyla ref: set (${d.width}×${d.height})`;
            if (_refs.saveBtn) _refs.saveBtn.disabled = false;
            return true;
        }
        _refs.statusEl.textContent = 'Cobyla ref: not set';
        if (_refs.saveBtn) _refs.saveBtn.disabled = true;
        return false;
    } catch (e) {
        _refs.statusEl.textContent = 'Cobyla ref: status error';
        if (_refs.saveBtn) _refs.saveBtn.disabled = true;
        return false;
    }
}

async function syncCobylaRefUi() {
    await refreshCobylaRefStatus();
    await refreshCobylaRefPreview();
}

function bindHandlers() {
    if (_refs.setBtn) {
        _refs.setBtn.addEventListener('click', async () => {
            if (!_refs.statusEl || !_tableCamImg) return;
            if (_tableCamImg.style.display === 'none' || !_tableCamImg.src) {
                _refs.statusEl.textContent = 'Cobyla ref: capture an image first (Latest capture)';
                log('Set Cobyla reference: need an image in Latest capture.', 'warn');
                return;
            }
            _refs.statusEl.textContent = 'Cobyla ref: uploading…';
            try {
                const cap = await fetch(_tableCamImg.src);
                if (!cap.ok) throw new Error('Could not read Latest capture image');
                const blob = await cap.blob();
                const res = await fetch('/api/cobyla-reference-image', {
                    method: 'POST',
                    headers: { 'Content-Type': 'image/png' },
                    body: blob,
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const err = data.detail || res.statusText || 'Upload failed';
                    _refs.statusEl.textContent = `Cobyla ref: ${err}`;
                    log(err, 'warn');
                    return;
                }
                log(data.message || 'Cobyla reference stored from Latest capture', 'info');
                await syncCobylaRefUi();
            } catch (e) {
                _refs.statusEl.textContent = `Cobyla ref: ${e.message || 'failed'}`;
                log(e.message || 'Cobyla reference upload failed', 'error');
            }
        });
    }

    if (_refs.clearBtn) {
        _refs.clearBtn.addEventListener('click', async () => {
            try {
                const res = await fetch('/api/cobyla-reference-image', { method: 'DELETE' });
                const data = await res.json().catch(() => ({}));
                if (res.ok) log(data.message || 'Cobyla reference cleared', 'info');
                await syncCobylaRefUi();
            } catch (e) {
                log(e.message || 'Clear failed', 'error');
            }
        });
    }

    if (_refs.saveBtn) {
        _refs.saveBtn.addEventListener('click', async () => {
            try {
                const r = await fetch(`/api/cobyla-reference-image?t=${Date.now()}`);
                if (!r.ok) {
                    log('No Cobyla reference to save.', 'warn');
                    return;
                }
                const blob = await r.blob();
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = 'cobyla-reference.png';
                a.rel = 'noopener';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(a.href);
                log('Saved Cobyla reference as cobyla-reference.png', 'info');
            } catch (e) {
                log(e.message || 'Save failed', 'error');
            }
        });
    }

    if (_refs.loadBtn && _refs.fileInput) {
        _refs.loadBtn.addEventListener('click', () => _refs.fileInput.click());
        _refs.fileInput.addEventListener('change', async () => {
            const file = _refs.fileInput.files && _refs.fileInput.files[0];
            _refs.fileInput.value = '';
            if (!file || !_refs.statusEl) return;
            if (!file.type.includes('png') && !file.name.toLowerCase().endsWith('.png')) {
                log('Please choose a PNG file.', 'warn');
                return;
            }
            _refs.statusEl.textContent = 'Cobyla ref: uploading…';
            try {
                const buf = await file.arrayBuffer();
                const head = new Uint8Array(buf.slice(0, 8));
                const pngSig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
                if (head.length < 8 || !pngSig.every((b, i) => head[i] === b)) {
                    _refs.statusEl.textContent = 'Cobyla ref: not a valid PNG';
                    log('File is not a valid PNG.', 'warn');
                    return;
                }
                const res = await fetch('/api/cobyla-reference-image', {
                    method: 'POST',
                    headers: { 'Content-Type': 'image/png' },
                    body: buf,
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    _refs.statusEl.textContent = `Cobyla ref: ${data.detail || res.statusText}`;
                    log(data.detail || 'Upload failed', 'warn');
                    return;
                }
                log(data.message || 'Cobyla reference loaded from file', 'info');
                await syncCobylaRefUi();
            } catch (e) {
                _refs.statusEl.textContent = `Cobyla ref: ${e.message || 'failed'}`;
                log(e.message || 'Load failed', 'error');
            }
        });
    }
}

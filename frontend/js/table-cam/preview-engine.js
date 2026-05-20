/**
 * Low-latency table-cam live preview engine.
 *
 * Instead of an MJPEG `<img>` (which buffers frames), this polls
 * `GET /api/table-cam/preview?cam_id=N` returning a single JPEG. We pump up to
 * `tableCamPreviewMaxInflight` concurrent requests at no more than
 * `tableCamPreviewTargetFps` and only display the *most recent* sequence number
 * so out-of-order responses never flash an older frame.
 *
 * Backed by `store.tableCamLive[cid]` / `store.tableCamConnected[cid]` for "is preview alive?".
 */
import { store } from '../state/store.js';

// Configurable via /api/table-cam/status → preview_config (mirrors lab view `table_cam_preview.json`).
let tableCamPreviewTargetFps = 144;
let tableCamPreviewMaxInflight = 3;

let tableCamPreviewRafId = null;
let tableCamPreviewObjectUrl = null;
let tableCamPreviewInflight = 0;
let tableCamPreviewLastKickMs = 0;
let tableCamPreviewKickSeq = 0;
let tableCamPreviewDisplayedSeq = 0;

export function applyTableCamPreviewConfig(cfg) {
    if (!cfg || typeof cfg !== 'object') return;
    const fps = Number(cfg.target_fps);
    if (Number.isFinite(fps) && fps >= 8 && fps <= 240) {
        tableCamPreviewTargetFps = fps;
    }
    const inflight = Number(cfg.max_inflight_requests);
    if (Number.isFinite(inflight) && inflight >= 1 && inflight <= 8) {
        tableCamPreviewMaxInflight = inflight;
    }
}

function tableCamPreviewMinIntervalMs() {
    return 1000 / tableCamPreviewTargetFps;
}

export function isTableCamLivePreviewActive(camId) {
    return !!(
        store.tableCamLive[camId] &&
        store.tableCamConnected[camId] &&
        !store.tableCamCapturePending[camId]
    );
}

function revokeTableCamPreviewObjectUrl() {
    if (tableCamPreviewObjectUrl) {
        URL.revokeObjectURL(tableCamPreviewObjectUrl);
        tableCamPreviewObjectUrl = null;
    }
}

export function stopTableCamLivePreview(_camId) {
    if (tableCamPreviewRafId != null) {
        cancelAnimationFrame(tableCamPreviewRafId);
        tableCamPreviewRafId = null;
    }
    tableCamPreviewInflight = 0;
    tableCamPreviewLastKickMs = 0;
    const img = document.getElementById('table-cam-img');
    if (img && tableCamPreviewObjectUrl && img.src === tableCamPreviewObjectUrl) {
        img.src = '';
    }
    revokeTableCamPreviewObjectUrl();
}

/** Used by capture flow to fully reset both the live preview and any MJPEG `<img src>` URL. */
export function stopTableCamStreamImg(camId) {
    stopTableCamLivePreview(camId);
    const img = document.getElementById('table-cam-img');
    if (img && typeof img.src === 'string' && img.src.includes('/api/table-cam/stream')) {
        img.src = '';
    }
}

function applyTableCamPreviewBlob(camId, blob, seq) {
    if (seq <= tableCamPreviewDisplayedSeq || !isTableCamLivePreviewActive(camId)) {
        return;
    }
    tableCamPreviewDisplayedSeq = seq;
    const url = URL.createObjectURL(blob);
    const img = document.getElementById('table-cam-img');
    const placeholder = document.getElementById('table-cam-placeholder');
    if (!img) {
        URL.revokeObjectURL(url);
        return;
    }
    revokeTableCamPreviewObjectUrl();
    tableCamPreviewObjectUrl = url;
    img.src = url;
    img.style.display = 'block';
    if (placeholder) {
        placeholder.style.display = 'none';
        placeholder.textContent = '';
    }
    // Loading indicator (e.g. "Obtaining live feed…") goes away when the first frame paints.
    const loading = document.getElementById('table-cam-preview-loading');
    if (loading) {
        loading.classList.remove('table-cam-preview-loading--visible');
        loading.setAttribute('aria-hidden', 'true');
    }
}

function kickTableCamPreviewFetch(camId, seq) {
    tableCamPreviewInflight += 1;
    fetch(`/api/table-cam/preview?cam_id=${camId}`, { cache: 'no-store' })
        .then(async (res) => {
            if (!res.ok || !isTableCamLivePreviewActive(camId)) return;
            const blob = await res.blob();
            applyTableCamPreviewBlob(camId, blob, seq);
        })
        .catch(() => {
            /* transient network errors */
        })
        .finally(() => {
            tableCamPreviewInflight = Math.max(0, tableCamPreviewInflight - 1);
        });
}

function pumpTableCamPreview(camId) {
    tableCamPreviewRafId = null;
    if (!isTableCamLivePreviewActive(camId)) return;

    const now = performance.now();
    if (
        tableCamPreviewInflight < tableCamPreviewMaxInflight &&
        now - tableCamPreviewLastKickMs >= tableCamPreviewMinIntervalMs()
    ) {
        tableCamPreviewLastKickMs = now;
        kickTableCamPreviewFetch(camId, ++tableCamPreviewKickSeq);
    }
    tableCamPreviewRafId = requestAnimationFrame(() => pumpTableCamPreview(camId));
}

export function startTableCamPreviewPoll(camId) {
    stopTableCamLivePreview(camId);
    tableCamPreviewKickSeq = 0;
    tableCamPreviewDisplayedSeq = 0;
    tableCamPreviewRafId = requestAnimationFrame(() => pumpTableCamPreview(camId));
}

export function syncTableCamPreviewPoll(camId) {
    if (isTableCamLivePreviewActive(camId)) {
        if (tableCamPreviewRafId == null) startTableCamPreviewPoll(camId);
    } else {
        stopTableCamLivePreview(camId);
    }
}

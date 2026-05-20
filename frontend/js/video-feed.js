/**
 * Live video feed in the unified panel (right sidebar).
 *
 * Polls `/api/video-feed/status` every 5 s. While connected, the `<img id="live-video-img">`
 * streams from `data.source` (an MJPEG endpoint, e.g. `/api/video-feed/stream?fps=10`).
 * When the FPS input changes, we reload the stream with the new value.
 *
 * Pure DOM, no store — the video state is owned by the backend.
 */
import { log } from './ui/log.js';

let _videoImg = null;
let _videoPlaceholder = null;
let _videoStatus = null;

/**
 * @param {{
 *   videoImg: HTMLImageElement | null,
 *   videoPlaceholder: HTMLElement | null,
 *   videoStatus: HTMLElement | null,
 * }} deps
 */
export function initVideoFeed(deps) {
    _videoImg = deps.videoImg || null;
    _videoPlaceholder = deps.videoPlaceholder || null;
    _videoStatus = deps.videoStatus || null;

    const fpsInput = document.getElementById('fps-input');
    if (fpsInput) {
        fpsInput.addEventListener('change', () => {
            const fps = Math.max(1, Math.min(60, parseInt(fpsInput.value) || 10));
            fpsInput.value = fps;
            if (_videoImg) {
                const baseSrc = '/api/video-feed/stream';
                _videoImg.src = `${baseSrc}?fps=${fps}&t=${Date.now()}`;
                log(`Video stream FPS set to ${fps}`, 'info');
            }
        });
    }

    setInterval(checkVideoStatus, 5000);
    checkVideoStatus();
}

export async function checkVideoStatus() {
    if (!_videoImg || !_videoPlaceholder || !_videoStatus) return;
    try {
        console.log(`[${new Date().toLocaleTimeString()}] Checking Video Status...`);
        const res = await fetch('/api/video-feed/status');
        if (res.ok) {
            const data = await res.json();
            if (data.connected) {
                console.log(`[${new Date().toLocaleTimeString()}] Video Status: Connected`);
                _videoImg.style.display = 'block';
                _videoPlaceholder.style.display = 'none';
                _videoStatus.innerHTML = '● LIVE';
                _videoStatus.style.color = '#10b981';
                // Force refresh with cache-busting so we don't get stuck showing an older mock SVG response.
                // Reload only if the placeholder was visible (i.e. previous state was OFFLINE).
                if (_videoPlaceholder.style.display !== 'none') {
                    const fpsInput = document.getElementById('fps-input');
                    const fps = fpsInput ? fpsInput.value : 10;
                    _videoImg.src = `${data.source}?fps=${fps}&t=${Date.now()}`;
                } else if (_videoImg.src.indexOf('t=') === -1) {
                    // Also refresh once on connect if the src has no timestamp yet.
                    const fpsInput = document.getElementById('fps-input');
                    const fps = fpsInput ? fpsInput.value : 10;
                    _videoImg.src = `${data.source}?fps=${fps}&t=${Date.now()}`;
                }
            } else {
                console.warn(`[${new Date().toLocaleTimeString()}] Video Status: Disconnected`);
                throw new Error('Disconnected');
            }
        } else {
            console.error(`[${new Date().toLocaleTimeString()}] Video Status Check Failed: HTTP ${res.status}`);
            throw new Error('API Error');
        }
    } catch (e) {
        console.error(`[${new Date().toLocaleTimeString()}] Video Error: ${e.message}`);
        _videoImg.style.display = 'none';
        _videoPlaceholder.style.display = 'flex';
        _videoStatus.innerHTML = '● OFFLINE';
        _videoStatus.style.color = '#ef4444';
    }
}

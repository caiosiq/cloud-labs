/**
 * High-rate TeleOp live pose polling — HTTP fallback when WebSocket session
 * is unavailable (see ``api/teleop-session-ws.js``).
 */
import { store } from '../state/store.js';

const _pollers = new Map();

function defaultFps(descriptor) {
    const fps = descriptor && descriptor.default_fps;
    return Number.isFinite(Number(fps)) && Number(fps) > 0 ? Number(fps) : 20;
}

function pollUrl(tagId, descriptor) {
    const raw = (descriptor && descriptor.url) || '/api/components/{tag_id}/telemetry/live-pose';
    return raw.replace('{tag_id}', encodeURIComponent(tagId));
}

/**
 * @param {string} tagId
 * @param {{ descriptor?: object, onUpdate?: Function, fps?: number }} [opts]
 */
export function startTeleopLivePosePoll(tagId, opts = {}) {
    if (!tagId) return;
    stopTeleopLivePosePoll(tagId);
    const descriptor = opts.descriptor || {};
    const intervalMs = Math.max(30, Math.round(1000 / (opts.fps || defaultFps(descriptor))));
    const url = pollUrl(tagId, descriptor);

    const tick = async () => {
        try {
            const r = await fetch(`${url}?_=${Date.now()}`);
            if (!r.ok) return;
            const body = await r.json();
            const pose = body && body.pose;
            if (!pose || typeof pose !== 'object') return;
            const merged = { ...pose };
            if (body.iteration != null) merged.iteration = body.iteration;
            if (body.loss != null) merged.loss = body.loss;
            store.teleopLivePose[tagId] = merged;
            if (typeof opts.onUpdate === 'function') {
                opts.onUpdate(merged);
            }
            if (typeof store._teleopLivePoseRender === 'function') {
                store._teleopLivePoseRender(tagId);
            }
        } catch (_e) {
            /* next tick */
        }
    };

    tick();
    const timer = setInterval(tick, intervalMs);
    _pollers.set(tagId, { timer, url });
}

export function stopTeleopLivePosePoll(tagId) {
    const rec = _pollers.get(tagId);
    if (rec && rec.timer) clearInterval(rec.timer);
    _pollers.delete(tagId);
    if (tagId && store.teleopLivePose) delete store.teleopLivePose[tagId];
}

export function stopAllTeleopLivePosePolls() {
    Array.from(_pollers.keys()).forEach(stopTeleopLivePosePoll);
}

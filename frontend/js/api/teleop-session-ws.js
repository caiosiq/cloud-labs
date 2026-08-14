/**
 * Duplex TeleOp session over WebSocket (Phase 4).
 *
 *   WS /api/components/{tag_id}/teleop/session
 *
 * Server pushes ``pose`` @ ~50 Hz; client sends coalesced ``goto`` + ``ping``.
 * HTTP live-pose poll remains fallback when WS cannot connect.
 */
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import { withBackendQuery } from '../state/backend-selection.js';

const _sessions = new Map();
const GOTO_INTERVAL_MS = 50; // ~20 Hz coalesce cap

function wsUrl(tagId, descriptor) {
    const raw = (descriptor && descriptor.url) || '/api/components/{tag_id}/teleop/session';
    const path = raw.replace('{tag_id}', encodeURIComponent(tagId));
    if (path.startsWith('ws://') || path.startsWith('wss://')) return path;
    if (path.startsWith('http://')) return `ws://${path.slice(7)}`;
    if (path.startsWith('https://')) return `wss://${path.slice(8)}`;
    const proto = (typeof location !== 'undefined' && location.protocol === 'https:') ? 'wss:' : 'ws:';
    const host = typeof location !== 'undefined' ? location.host : 'localhost';
    const httpPath = path.startsWith('/') ? path : `/${path}`;
    // backend_id must be on the WS URL — HTTP middleware does not bind websockets.
    const withBackend = withBackendQuery(httpPath);
    return `${proto}//${host}${withBackend}`;
}

/**
 * @param {object|undefined} descriptor catalog ``live_pose`` descriptor
 */
export function shouldUseWebSocketTransport(descriptor) {
    if (!descriptor) return true;
    const transport = String(descriptor.transport || '').toLowerCase();
    if (transport === 'http' || transport === 'polling') return false;
    const url = String(descriptor.url || '');
    if (url.includes('telemetry/live-pose')) return false;
    return (
        transport === 'websocket'
        || transport === 'ws'
        || url.includes('teleop/session')
        || !url
    );
}

function applyPose(tagId, msg, onUpdate) {
    const pose = msg && msg.pose;
    if (!pose || typeof pose !== 'object') return;
    const merged = { ...pose };
    if (msg.ts_ms != null) merged.ts_ms = msg.ts_ms;
    if (msg.phase === 'executing') merged.executing = true;
    else if (msg.phase === 'idle') merged.executing = false;
    store.teleopLivePose[tagId] = merged;
    if (typeof onUpdate === 'function') onUpdate(merged);
    if (typeof store._teleopLivePoseRender === 'function') {
        store._teleopLivePoseRender(tagId);
    }
}

function flushGoto(tagId) {
    const rec = _sessions.get(tagId);
    if (!rec || !rec.pendingGoto) return;
    const body = rec.pendingGoto;
    rec.pendingGoto = null;
    rec.gotoTimer = null;
    if (!rec.ready || !rec.ws || rec.ws.readyState !== WebSocket.OPEN) return;
    rec.seq = (rec.seq || 0) + 1;
    try {
        rec.ws.send(JSON.stringify({
            type: 'goto',
            seq: rec.seq,
            target_pose: body.target_pose,
            target_motor_positions: body.target_motor_positions,
            speed: body.speed,
            frame_id: body.frame_id,
        }));
    } catch (e) {
        log(`TELEOP_WS goto ${tagId}: ${e.message || e}`, 'warn');
    }
}

/**
 * @param {string} tagId
 * @param {{ descriptor?: object, onUpdate?: Function, onFallback?: Function }} [opts]
 */
export function startTeleopWsSession(tagId, opts = {}) {
    if (!tagId || typeof WebSocket === 'undefined') {
        if (typeof opts.onFallback === 'function') opts.onFallback();
        return;
    }
    stopTeleopWsSession(tagId);

    const descriptor = opts.descriptor || {};
    const url = wsUrl(tagId, descriptor);
    let ws;
    try {
        ws = new WebSocket(url);
    } catch (e) {
        if (typeof opts.onFallback === 'function') opts.onFallback();
        return;
    }

    const rec = {
        ws,
        ready: false,
        descriptor,
        onUpdate: opts.onUpdate,
        onFallback: opts.onFallback,
        fallbackStarted: false,
        seq: 0,
        pendingGoto: null,
        gotoTimer: null,
    };
    _sessions.set(tagId, rec);

    const fallbackTimer = setTimeout(() => {
        if (!rec.ready && !rec.fallbackStarted) {
            rec.fallbackStarted = true;
            stopTeleopWsSession(tagId);
            if (typeof opts.onFallback === 'function') opts.onFallback();
        }
    }, 2000);

    ws.onopen = () => {
        /* wait for session.ready before marking connected */
    };

    ws.onmessage = (ev) => {
        let msg;
        try {
            msg = JSON.parse(ev.data);
        } catch (_e) {
            return;
        }
        if (!msg || typeof msg !== 'object') return;

        if (msg.type === 'session' && msg.phase === 'ready') {
            rec.ready = true;
            clearTimeout(fallbackTimer);
            return;
        }
        if (msg.type === 'pose') {
            applyPose(tagId, msg, rec.onUpdate);
            return;
        }
        if (msg.type === 'error' && msg.message) {
            log(`TELEOP_WS ${tagId}: ${msg.message}`, 'warn');
        }
    };

    const onDisconnect = () => {
        clearTimeout(fallbackTimer);
        const wasReady = rec.ready;
        if (rec.gotoTimer) clearTimeout(rec.gotoTimer);
        _sessions.delete(tagId);
        if (wasReady && !rec.fallbackStarted && typeof rec.onFallback === 'function') {
            rec.fallbackStarted = true;
            rec.onFallback();
        }
    };

    ws.onclose = onDisconnect;
    ws.onerror = onDisconnect;
}

export function stopTeleopWsSession(tagId) {
    const rec = _sessions.get(tagId);
    if (!rec) return;
    if (rec.gotoTimer) clearTimeout(rec.gotoTimer);
    rec.fallbackStarted = true;
    try {
        rec.ws.close();
    } catch (_e) {
        /* ignore */
    }
    _sessions.delete(tagId);
}

export function stopAllTeleopWsSessions() {
    Array.from(_sessions.keys()).forEach(stopTeleopWsSession);
}

export function isTeleopWsConnected(tagId) {
    const rec = _sessions.get(tagId);
    return !!(rec && rec.ready && rec.ws && rec.ws.readyState === WebSocket.OPEN);
}

/**
 * Queue a goto frame on the open WS (coalesced to ~20 Hz).
 *
 * @returns {Promise<{ok: boolean, error?: string}>}
 */
export function teleopGotoViaWs(tagId, body) {
    const rec = _sessions.get(tagId);
    if (!rec || !rec.ready || !rec.ws || rec.ws.readyState !== WebSocket.OPEN) {
        return Promise.resolve({ ok: false, error: 'ws not connected' });
    }
    rec.pendingGoto = body || {};
    if (!rec.gotoTimer) {
        rec.gotoTimer = setTimeout(() => flushGoto(tagId), GOTO_INTERVAL_MS);
    }
    return Promise.resolve({ ok: true });
}

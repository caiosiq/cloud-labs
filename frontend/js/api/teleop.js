/**
 * TeleOp v2 HTTP client.
 *
 *   POST /api/components/{tag_id}/teleop/start
 *   POST /api/components/{tag_id}/teleop/end
 *   POST /api/components/{tag_id}/telemetry/goto
 *
 * These routes are separate from ``/api/command`` so the goto path can be
 * optimized without touching recipe / dispatch machinery. Helpers return
 * parsed JSON; UI feedback (toasts, LIVE/TARGET layers, etc.) stays in callers.
 *
 * Error semantics: every function resolves with ``{ok: bool, ...}``
 * instead of throwing, so rate-limited canvas loops stay simple.
 */
import { log } from '../ui/log.js';
import { applyComponentTelemetryFromServer } from '../component-state.js';
import { syncTeleopTargetFromCurrent } from '../teleop-target.js';
import { isTeleopWsConnected, teleopGotoViaWs } from './teleop-session-ws.js';

async function _parseBody(response) {
    try {
        return await response.json();
    } catch (_e) {
        return null;
    }
}

/**
 * Acquire the per-component TELEOP lease.
 *
 * @param {string} tagId
 * @returns {Promise<{ok: boolean, tunables?: object, error?: string}>}
 */
export async function startTeleop(tagId) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    try {
        const r = await fetch(`/api/components/${encodeURIComponent(tagId)}/teleop/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const body = await _parseBody(r);
        if (!r.ok) {
            const detail = (body && body.detail) ? String(body.detail) : `HTTP ${r.status}`;
            log(`START_TELEOP ${tagId} refused: ${detail}`, 'warn');
            return { ok: false, error: detail };
        }
        if (body && body.telemetry) {
            applyComponentTelemetryFromServer(tagId, body.telemetry);
        }
        syncTeleopTargetFromCurrent(tagId);
        log(`START_TELEOP ${tagId} (target will sync from live pose)`, 'info');
        return { ok: true, tunables: body && body.tunables, telemetry: body && body.telemetry };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        log(`START_TELEOP ${tagId} failed: ${msg}`, 'error');
        return { ok: false, error: msg };
    }
}

/**
 * Release the per-component TELEOP lease. Idempotent.
 *
 * @param {string} tagId
 * @returns {Promise<{ok: boolean, tunables?: object, error?: string}>}
 */
export async function endTeleop(tagId) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    try {
        const r = await fetch(`/api/components/${encodeURIComponent(tagId)}/teleop/end`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        const body = await _parseBody(r);
        if (!r.ok) {
            const detail = (body && body.detail) ? String(body.detail) : `HTTP ${r.status}`;
            log(`END_TELEOP ${tagId} refused: ${detail}`, 'warn');
            return { ok: false, error: detail };
        }
        if (body && body.telemetry) {
            applyComponentTelemetryFromServer(tagId, body.telemetry);
        }
        log(`END_TELEOP ${tagId}`, 'info');
        return { ok: true, tunables: body && body.tunables, telemetry: body && body.telemetry };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        log(`END_TELEOP ${tagId} failed: ${msg}`, 'error');
        return { ok: false, error: msg };
    }
}

/**
 * Plan + execute one absolute TeleOp goto frame.
 *
 * Body must include ``target_pose`` and/or ``target_motor_positions``.
 * Verbose logging is off by default (``opts.silent``); canvas drag sets silent.
 *
 * @param {string} tagId
 * @param {{target_pose?: object, target_motor_positions?: object, speed?: object, frame_id?: number}} body
 * @param {{silent?: boolean}} [opts]
 * @returns {Promise<{ok: boolean, telemetry?: object, error?: string}>}
 */
export async function teleopGoto(tagId, body, opts) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    const silent = opts && opts.silent;
    if (isTeleopWsConnected(tagId)) {
        const wsResult = await teleopGotoViaWs(tagId, body || {});
        if (wsResult.ok) {
            if (!silent) log(`TELEOP_GOTO ${tagId} (ws)`, 'info');
            return wsResult;
        }
    }
    try {
        const r = await fetch(`/api/components/${encodeURIComponent(tagId)}/telemetry/goto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const parsed = await _parseBody(r);
        if (!r.ok) {
            const detail = (parsed && parsed.detail) ? String(parsed.detail) : `HTTP ${r.status}`;
            if (!silent) log(`TELEOP_GOTO ${tagId} refused: ${detail}`, 'warn');
            return { ok: false, error: detail };
        }
        if (!silent) log(`TELEOP_GOTO ${tagId}`, 'info');
        return { ok: true, telemetry: parsed && parsed.telemetry };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        if (!silent) log(`TELEOP_GOTO ${tagId} failed: ${msg}`, 'error');
        return { ok: false, error: msg };
    }
}

/**
 * Best-effort END_TELEOP for browser-unload paths (``beforeunload`` /
 * ``pagehide``). Uses ``navigator.sendBeacon`` so the request survives navigation.
 *
 * @param {string} tagId
 * @returns {boolean}
 */
export function endTeleopBeacon(tagId) {
    if (!tagId || typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') {
        return false;
    }
    try {
        const blob = new Blob(['{}'], { type: 'application/json' });
        return navigator.sendBeacon(`/api/components/${encodeURIComponent(tagId)}/teleop/end`, blob);
    } catch (_e) {
        return false;
    }
}

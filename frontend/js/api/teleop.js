/**
 * TeleOp v2 HTTP client — Twin wrapper over {@link labClient}.
 *
 * Primitive names match the Python SDK:
 *   lab.start_teleop / lab.end_teleop / lab.teleop_goto
 *
 * Hot path: when a Tier A WS session is open, ``teleopGoto`` prefers WS;
 * otherwise it uses the TELEOP_GOTO HTTP alias.
 *
 * Error semantics: every function resolves with ``{ok: bool, ...}``
 * instead of throwing, so rate-limited canvas loops stay simple.
 */
import { log } from '../ui/log.js';
import { applyComponentTelemetryFromServer } from '../component-state.js';
import { syncTeleopTargetFromCurrent } from '../teleop-target.js';
import { labClient } from '../cloudlabs/client.js';
import { isTeleopWsConnected, teleopGotoViaWs } from './teleop-session-ws.js';

/**
 * Acquire the per-component TELEOP lease.
 *
 * @param {string} tagId
 * @returns {Promise<{ok: boolean, tunables?: object, error?: string}>}
 */
export async function startTeleop(tagId) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    try {
        const body = await labClient.startTeleop(tagId);
        if (body && body.telemetry) {
            applyComponentTelemetryFromServer(tagId, body.telemetry);
        }
        syncTeleopTargetFromCurrent(tagId);
        log(`START_TELEOP ${tagId} (target will sync from live pose)`, 'info');
        return { ok: true, tunables: body && body.tunables, telemetry: body && body.telemetry };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        log(`START_TELEOP ${tagId} refused: ${msg}`, 'warn');
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
        const body = await labClient.endTeleop(tagId);
        if (body && body.telemetry) {
            applyComponentTelemetryFromServer(tagId, body.telemetry);
        }
        log(`END_TELEOP ${tagId}`, 'info');
        return { ok: true, tunables: body && body.tunables, telemetry: body && body.telemetry };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        log(`END_TELEOP ${tagId} refused: ${msg}`, 'warn');
        return { ok: false, error: msg };
    }
}

/**
 * Plan + execute one absolute TeleOp goto frame.
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
        const parsed = await labClient.teleopGoto(tagId, body || {});
        if (!silent) log(`TELEOP_GOTO ${tagId}`, 'info');
        return { ok: true, telemetry: parsed && parsed.telemetry };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        if (!silent) log(`TELEOP_GOTO ${tagId} refused: ${msg}`, 'warn');
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

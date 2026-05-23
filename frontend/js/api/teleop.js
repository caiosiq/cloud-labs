/**
 * Phase 8b — per-component TELEOP HTTP client.
 *
 * Thin wrapper around the three Phase 8a routes:
 *
 *   POST /api/components/{tag_id}/teleop/start
 *   POST /api/components/{tag_id}/teleop/end
 *   POST /api/components/{tag_id}/telemetry/jog
 *
 * These are deliberately NOT routed through ``/api/command``: teleop has
 * its own dedicated endpoints (per the architecture doc §16.5) so the
 * jog path can be optimized later without touching the recipe / dispatch
 * machinery. The helpers here just produce + return parsed JSON, leaving
 * UI feedback (toasts, ghost.source mutations, etc.) to the caller.
 *
 * Error semantics: every function resolves with ``{ok: bool, ...}``
 * instead of throwing, so callers can keep their fetch loops simple
 * (e.g. canvas drag rate-limiter doesn't want to wrap each frame in
 * try/catch). 4xx / 5xx responses surface ``error: <detail>`` lifted
 * from the FastAPI ``detail`` field when present.
 */
import { log } from '../ui/log.js';

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
 * Backend refusals (409): another component already teleoped, target is
 * STORED, lab status is BUSY/OPTIMIZING (the manifest ``require_lab_idle``
 * knob may add more cases). The error string is surfaced verbatim so the
 * widget can show it inline.
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
        log(`START_TELEOP ${tagId}`, 'info');
        return { ok: true, tunables: body && body.tunables };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        log(`START_TELEOP ${tagId} failed: ${msg}`, 'error');
        return { ok: false, error: msg };
    }
}

/**
 * Release the per-component TELEOP lease. Idempotent: ending a session
 * that's already inactive resolves ``{ok: true}``.
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
        log(`END_TELEOP ${tagId}`, 'info');
        return { ok: true, tunables: body && body.tunables };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        log(`END_TELEOP ${tagId} failed: ${msg}`, 'error');
        return { ok: false, error: msg };
    }
}

/**
 * Push one absolute jog frame to the component currently under TELEOP.
 *
 * Frames are absolute (not deltas) so the protocol is self-healing under
 * packet loss — dropping a frame just means the next one lands slightly
 * later. The body must include at least one of ``nominal_pose`` /
 * ``nominal_motor_positions``; the backend ``TeleopJogParameters``
 * validator rejects anything else with 422.
 *
 * Verbose mode is **off** by default: jogs are noisy (canvas drag fires
 * ~15/s) and we don't want to flood the log. Set ``opts.silent = false``
 * to log each frame (useful when debugging).
 *
 * @param {string} tagId
 * @param {{nominal_pose?: object, nominal_motor_positions?: object, frame_id?: number}} body
 * @param {{silent?: boolean}} [opts]
 * @returns {Promise<{ok: boolean, tunables?: object, error?: string}>}
 */
export async function teleopJog(tagId, body, opts) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    const silent = !opts || opts.silent !== false;
    try {
        const r = await fetch(`/api/components/${encodeURIComponent(tagId)}/telemetry/jog`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const parsed = await _parseBody(r);
        if (!r.ok) {
            const detail = (parsed && parsed.detail) ? String(parsed.detail) : `HTTP ${r.status}`;
            if (!silent) log(`TELEOP_JOG ${tagId} refused: ${detail}`, 'warn');
            return { ok: false, error: detail };
        }
        if (!silent) log(`TELEOP_JOG ${tagId} frame_id=${body && body.frame_id}`, 'debug');
        return { ok: true, tunables: parsed && parsed.tunables };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        if (!silent) log(`TELEOP_JOG ${tagId} failed: ${msg}`, 'error');
        return { ok: false, error: msg };
    }
}

/**
 * Best-effort END_TELEOP for browser-unload paths (``beforeunload`` /
 * ``pagehide``). Uses ``navigator.sendBeacon`` so the request reliably
 * survives navigation away from the page — a plain ``fetch`` is often
 * cancelled by the browser mid-flight during unload, which leaves the
 * server-side ``teleop_active`` flag dangling until the stale-lease
 * sweeper picks it up.
 *
 * Returns ``true`` if the beacon was queued by the browser, ``false``
 * otherwise (e.g. quota exceeded or no sendBeacon support, in which
 * case the caller may fall back to a synchronous XHR).
 *
 * @param {string} tagId
 * @returns {boolean}
 */
export function endTeleopBeacon(tagId) {
    if (!tagId || typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') {
        return false;
    }
    try {
        // Empty body — the route ignores the body but needs a valid blob.
        const blob = new Blob(['{}'], { type: 'application/json' });
        return navigator.sendBeacon(`/api/components/${encodeURIComponent(tagId)}/teleop/end`, blob);
    } catch (_e) {
        return false;
    }
}

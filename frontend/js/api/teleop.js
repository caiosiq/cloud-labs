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
import { store } from '../state/store.js';
import { confirmPrimitiveCommand } from './confirm-primitive.js';

/** Paint system-status badge immediately (avoid waiting for next poll). */
function paintSystemStatusOptimistic(status) {
    if (!store.labState || !status) return;
    store.labState.system_status = status;
    const statusBadge = document.getElementById('system-status-badge');
    if (!statusBadge) return;
    // Match updateUI / twin-viewer BUSY + TELEOP styling.
    let badgeClass = 'active';
    let badgeColor = 'placed';
    let badgeStyle = '';
    if (status === 'BUSY') {
        badgeClass = '';
        badgeColor = 'inventory';
        badgeStyle = 'background-color: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.4);';
    } else if (status === 'TELEOP') {
        badgeClass = '';
        badgeColor = '';
        badgeStyle = 'background-color: #0891b2; box-shadow: 0 0 8px rgba(34, 211, 238, 0.45);';
    }
    statusBadge.className = `system-status ${badgeClass}`;
    statusBadge.innerHTML = `<span class="status-dot ${badgeColor}" style="${badgeStyle}"></span> ${status}`;
}

/**
 * Acquire the per-component TELEOP lease.
 *
 * Optimistically marks ``active && !ready`` so Twin can show Loading while the
 * (possibly long) HTTP START runs on a real edge — same UX as mock's pending
 * commit before hardware prepare finishes.
 *
 * @param {string} tagId
 * @param {{ skipConfirm?: boolean }} [opts]
 * @returns {Promise<{ok: boolean, tunables?: object, error?: string}>}
 */
export async function startTeleop(tagId, opts = {}) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    if (!opts.skipConfirm && !store.isRecording) {
        const ok = await confirmPrimitiveCommand({
            action: 'START_TELEOP',
            target_id: tagId,
        });
        if (!ok) {
            log(`START_TELEOP ${tagId} cancelled by user.`, 'info');
            return { ok: false, error: 'cancelled' };
        }
    }
    // Pending session in the browser store immediately (Loading TeleOp).
    // Mirror acquiring on the system monitor as BUSY (not TELEOP until ready).
    applyComponentTelemetryFromServer(tagId, {
        teleop: { active: true, ready: false, last_error: null },
    });
    paintSystemStatusOptimistic('BUSY');
    try {
        const body = await labClient.startTeleop(tagId);
        if (body && body.telemetry) {
            applyComponentTelemetryFromServer(tagId, body.telemetry);
        } else {
            // HTTP edge often returns nested telemetry from the coordinator store;
            // if missing, assume arming finished (ready) so controls appear.
            applyComponentTelemetryFromServer(tagId, {
                teleop: { active: true, ready: true, last_error: null },
            });
        }
        const tel = body && body.telemetry && body.telemetry.teleop;
        const ready = tel ? !!tel.ready : true;
        paintSystemStatusOptimistic(ready ? 'TELEOP' : 'BUSY');
        syncTeleopTargetFromCurrent(tagId);
        log(`START_TELEOP ${tagId} (target will sync from live pose)`, 'info');
        return { ok: true, tunables: body && body.tunables, telemetry: body && body.telemetry };
    } catch (e) {
        applyComponentTelemetryFromServer(tagId, {
            teleop: { active: false, ready: false, last_error: (e && e.message) ? e.message : String(e) },
        });
        paintSystemStatusOptimistic('IDLE');
        const msg = (e && e.message) ? e.message : String(e);
        log(`START_TELEOP ${tagId} refused: ${msg}`, 'warn');
        return { ok: false, error: msg };
    }
}

/**
 * Release the per-component TELEOP lease. Idempotent.
 *
 * @param {string} tagId
 * @param {{ skipConfirm?: boolean }} [opts]
 * @returns {Promise<{ok: boolean, tunables?: object, error?: string}>}
 */
export async function endTeleop(tagId, opts = {}) {
    if (!tagId) return { ok: false, error: 'tagId required' };
    if (!opts.skipConfirm && !store.isRecording) {
        const ok = await confirmPrimitiveCommand({
            action: 'END_TELEOP',
            target_id: tagId,
        });
        if (!ok) {
            log(`END_TELEOP ${tagId} cancelled by user.`, 'info');
            return { ok: false, error: 'cancelled' };
        }
    }
    try {
        const body = await labClient.endTeleop(tagId);
        if (body && body.telemetry) {
            applyComponentTelemetryFromServer(tagId, body.telemetry);
        } else {
            applyComponentTelemetryFromServer(tagId, {
                teleop: { active: false, ready: false, last_error: null },
            });
        }
        // Poll will refine HOLDING vs IDLE; clear TELEOP immediately.
        if (store.labState && store.labState.system_status === 'TELEOP') {
            paintSystemStatusOptimistic('IDLE');
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

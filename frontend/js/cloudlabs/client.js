/**
 * CloudLabsClient — browser face of the same lab language as ``packages/cloudlabs``.
 *
 * Twin UI is not a second product: teleop / live feed / record / command use the
 * same primitive names the Python SDK uses. Dedicated HTTP routes remain
 * **aliases** (Phase 3); this module is the shared verb surface for Twin.
 *
 * Tier C: ``getLabState()`` is layout/lease overview only — not live science.
 * Tier B: JPEG/MJPEG URLs after ``startLiveFeed``.
 * Tier A: TeleOp WS stays in ``api/teleop-session-ws.js`` under ``teleopGoto``.
 *
 * One-line SDK equivalents (Python)::
 *
 *   lab.start_live_feed("tag_22")
 *   lab.end_live_feed("tag_22")
 *   lab.start_teleop("tag_20")
 *   lab.teleop_goto("tag_20", target_pose={...})
 *   lab.capture_measurable("tag_22", "camera_image")
 */
import { leaseHeaders } from '../api/session-lease.js';
import { withBackendQuery } from '../state/backend-selection.js';

function _debug(primitive, tagId, extra) {
    if (typeof console !== 'undefined' && typeof console.debug === 'function') {
        console.debug(`[cloudlabs] ${primitive}`, tagId ?? '', extra ?? '');
    }
}

async function _parseJson(response) {
    try {
        return await response.json();
    } catch (_e) {
        return null;
    }
}

function _detail(body, status) {
    if (body && body.detail !== undefined) {
        return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    }
    return `HTTP ${status}`;
}

export class CloudLabsClient {
    /**
     * POST /api/command — any primitive envelope.
     * @param {{ action: string, target_id?: string, parameters?: object, channel?: string }} command
     */
    async execute(command) {
        if (!command || !command.action) {
            throw new Error('execute requires command.action');
        }
        _debug(command.action, command.target_id, command.parameters);
        const r = await fetch(withBackendQuery('/api/command'), {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(command),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body || { status: 'ok' };
    }

    /**
     * Tier C overview only (layout, leases, badges). Not for laser-hunting.
     * Prefer armed Tier A/B channels or RECORD_MEASURABLES / EVAL_KERNEL for science.
     */
    async getLabState() {
        _debug('GET_LAB_STATE', null, 'tier_c');
        const r = await fetch(withBackendQuery('/api/lab-state'), {
            headers: leaseHeaders(),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body;
    }

    /** Primitive: START_LIVE_FEED (alias route). */
    async startLiveFeed(tagId, channel = 'stream') {
        _debug('START_LIVE_FEED', tagId, channel);
        const q = `channel=${encodeURIComponent(channel || 'stream')}`;
        const url = withBackendQuery(
            `/api/components/${encodeURIComponent(tagId)}/telemetry/live-feed/start?${q}`,
        );
        const r = await fetch(url, {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body || { status: 'ok' };
    }

    /** Primitive: END_LIVE_FEED (alias route). */
    async endLiveFeed(tagId, channel = 'all') {
        _debug('END_LIVE_FEED', tagId, channel);
        const q = `channel=${encodeURIComponent(channel || 'all')}`;
        const url = withBackendQuery(
            `/api/components/${encodeURIComponent(tagId)}/telemetry/live-feed/end?${q}`,
        );
        const r = await fetch(url, {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body || { status: 'ok' };
    }

    /** Primitive: START_TELEOP (alias route). */
    async startTeleop(tagId) {
        _debug('START_TELEOP', tagId);
        const url = withBackendQuery(
            `/api/components/${encodeURIComponent(tagId)}/teleop/start`,
        );
        const r = await fetch(url, {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body || { status: 'ok' };
    }

    /** Primitive: END_TELEOP (alias route). */
    async endTeleop(tagId) {
        _debug('END_TELEOP', tagId);
        const url = withBackendQuery(
            `/api/components/${encodeURIComponent(tagId)}/teleop/end`,
        );
        const r = await fetch(url, {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body || { status: 'ok' };
    }

    /**
     * Primitive: TELEOP_GOTO (alias route).
     * @param {string} tagId
     * @param {{ target_pose?: object, target_motor_positions?: object, speed?: object, frame_id?: number }} body
     */
    async teleopGoto(tagId, body) {
        _debug('TELEOP_GOTO', tagId, body);
        const url = withBackendQuery(
            `/api/components/${encodeURIComponent(tagId)}/telemetry/goto`,
        );
        const r = await fetch(url, {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body || {}),
        });
        const parsed = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(parsed, r.status));
        }
        return parsed || { status: 'ok' };
    }

    /**
     * Primitive: RECORD_MEASURABLES (alias route).
     * Mirrors Python ``capture_measurable`` network step (returns full body).
     */
    async recordMeasurables(tagId) {
        _debug('RECORD_MEASURABLES', tagId);
        const url = withBackendQuery(
            `/api/components/${encodeURIComponent(tagId)}/measurables/record`,
        );
        const r = await fetch(url, {
            method: 'POST',
            headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        });
        const body = await _parseJson(r);
        if (!r.ok) {
            throw new Error(_detail(body, r.status));
        }
        return body || { status: 'ok' };
    }

    /**
     * Primitive: EVAL_KERNEL via /api/command (preferred over /api/kernels/eval).
     * @param {string} tagId
     * @param {{ kernel_id: string, field?: string }} opts
     */
    async probeKernel(tagId, opts) {
        const kernelId = opts && opts.kernel_id;
        if (!kernelId) throw new Error('probeKernel requires opts.kernel_id');
        return this.execute({
            action: 'EVAL_KERNEL',
            target_id: tagId,
            parameters: {
                kernel_id: kernelId,
                field: (opts && opts.field) || 'camera_image',
            },
        });
    }
}

/** Twin-wide singleton — api/* modules delegate here. */
export const labClient = new CloudLabsClient();

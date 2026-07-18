/**
 * Twin session-lease lifecycle — Take control / heartbeat / release.
 *
 * Mutations attach ``X-CloudLabs-Lease`` via {@link leaseHeaders} once acquired.
 */
import { getSelectedBackendId, backendHeaders } from '../state/backend-selection.js';
import { getClientHolder } from '../state/client-id.js';
import { store } from '../state/store.js';
import { log } from '../ui/log.js';

/** @type {{ lease_id: string, backend_id: string, holder: string, expires_at?: string } | null} */
let _lease = null;
/** @type {ReturnType<typeof setInterval> | null} */
let _heartbeatTimer = null;

const HEARTBEAT_MS = 45_000;

export function getActiveLease() {
    return _lease;
}

export function weHoldSessionLease() {
    if (!_lease?.lease_id) return false;
    const remote = store.labState?.session_lease;
    if (!remote?.lease_id) {
        // Lab-state lag: trust local lease until poll catches up.
        return true;
    }
    return remote.lease_id === _lease.lease_id;
}

export function otherHoldsSessionLease() {
    const remote = store.labState?.session_lease;
    if (!remote?.holder) return false;
    return !weHoldSessionLease();
}

/** Headers for mutating Twin requests (backend + client + optional lease). */
export function leaseHeaders(extra = {}) {
    const headers = backendHeaders({ ...extra });
    if (_lease?.lease_id) {
        headers['X-CloudLabs-Lease'] = _lease.lease_id;
    }
    return headers;
}

function _clearHeartbeat() {
    if (_heartbeatTimer != null) {
        clearInterval(_heartbeatTimer);
        _heartbeatTimer = null;
    }
}

function _startHeartbeat() {
    _clearHeartbeat();
    _heartbeatTimer = setInterval(() => {
        void heartbeatSessionLease().catch((err) => {
            console.warn('session lease heartbeat failed', err);
        });
    }, HEARTBEAT_MS);
}

/**
 * Acquire exclusive control of the selected backend.
 * @returns {Promise<{ ok: true, lease: object } | { ok: false, error: string, holder?: string }>}
 */
export async function acquireSessionLease() {
    const backendId = getSelectedBackendId();
    if (!backendId) {
        return { ok: false, error: 'No backend selected' };
    }
    if (_lease?.lease_id && _lease.backend_id === backendId) {
        return { ok: true, lease: _lease };
    }

    const holder = getClientHolder('twin');
    try {
        const res = await fetch('/api/jobs/lease/acquire', {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                backend_id: backendId,
                holder,
                mode: 'imperative',
            }),
        });
        const body = await res.json().catch(() => ({}));
        if (res.status === 409) {
            const holderNow = body?.holder || body?.detail?.holder;
            return {
                ok: false,
                error: `Backend locked by ${holderNow || 'another client'}`,
                holder: holderNow,
            };
        }
        if (!res.ok) {
            const detail = body?.detail;
            const msg =
                typeof detail === 'string'
                    ? detail
                    : detail?.message || `HTTP ${res.status}`;
            return { ok: false, error: msg };
        }
        _lease = {
            lease_id: body.lease_id,
            backend_id: body.backend_id || backendId,
            holder: body.holder || holder,
            expires_at: body.expires_at,
        };
        _startHeartbeat();
        log(`Took control as ${holder}`, 'info');
        return { ok: true, lease: _lease };
    } catch (err) {
        return { ok: false, error: err.message || String(err) };
    }
}

export async function heartbeatSessionLease() {
    if (!_lease?.lease_id) return null;
    const res = await fetch('/api/jobs/lease/heartbeat', {
        method: 'POST',
        headers: backendHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ lease_id: _lease.lease_id }),
    });
    if (res.status === 404 || res.status === 410) {
        _lease = null;
        _clearHeartbeat();
        throw new Error('Session lease expired or released');
    }
    if (!res.ok) {
        throw new Error(`Heartbeat failed HTTP ${res.status}`);
    }
    const body = await res.json();
    _lease = {
        ..._lease,
        expires_at: body.expires_at || _lease.expires_at,
    };
    return body;
}

/**
 * Release our session lease (idempotent).
 * @param {{ beacon?: boolean }} [opts]
 */
export async function releaseSessionLease(opts = {}) {
    const leaseId = _lease?.lease_id;
    _clearHeartbeat();
    _lease = null;
    if (!leaseId) return { ok: true };

    const payload = JSON.stringify({ lease_id: leaseId });
    if (opts.beacon && typeof navigator !== 'undefined' && navigator.sendBeacon) {
        const blob = new Blob([payload], { type: 'application/json' });
        navigator.sendBeacon('/api/jobs/lease/release', blob);
        return { ok: true };
    }

    try {
        const res = await fetch('/api/jobs/lease/release', {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: payload,
        });
        if (!res.ok && res.status !== 404) {
            const body = await res.json().catch(() => ({}));
            return { ok: false, error: body?.detail || `HTTP ${res.status}` };
        }
        log('Released session control', 'info');
        return { ok: true };
    } catch (err) {
        return { ok: false, error: err.message || String(err) };
    }
}

/** Best-effort release on tab close. */
export function releaseSessionLeaseBeacon() {
    void releaseSessionLease({ beacon: true });
}

/**
 * Whether mutations require a lease for the selected backend (from coordinator policy).
 * @returns {boolean}
 */
export function commandLeaseRequired() {
    const policy = store.coordinatorPolicy;
    if (policy?.solo) return false;
    const backendId = getSelectedBackendId() || '';
    if (backendId.startsWith('mock.')) {
        return Boolean(policy?.strict_lease_mock);
    }
    // Real / other: require lease unless solo.
    return true;
}

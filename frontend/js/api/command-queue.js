/**
 * Command Matrix API helpers (Twin).
 */
import { withBackendQuery } from '../state/backend-selection.js';
import { leaseHeaders } from './session-lease.js';

/**
 * @returns {Promise<{
 *   enabled?: boolean,
 *   backend_id?: string,
 *   threads?: Array<Record<string, any>>,
 *   idle?: boolean,
 *   session_locks?: Record<string, any>,
 *   message?: string,
 * }>}
 */
export async function fetchCommandQueue() {
    const res = await fetch(withBackendQuery('/api/command-queue'), {
        headers: leaseHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data && data.detail;
        const msg =
            typeof detail === 'string'
                ? detail
                : (detail && detail.message) || res.statusText || `HTTP ${res.status}`;
        throw new Error(msg);
    }
    return data || {};
}

/**
 * Cancel every queued (not running) matrix command.
 * @returns {Promise<{ cancelled?: string[], command_matrix?: object }>}
 */
export async function cancelAllQueuedCommands() {
    const res = await fetch(withBackendQuery('/api/command-queue/cancel'), {
        method: 'POST',
        headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ all_queued: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data && data.detail;
        const msg =
            typeof detail === 'string'
                ? detail
                : (detail && detail.message) || res.statusText || `HTTP ${res.status}`;
        throw new Error(msg);
    }
    return data || {};
}

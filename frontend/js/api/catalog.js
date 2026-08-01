/**
 * Remote catalog API — approved pins and publish requests (Phase G.5).
 */
import { getClientHolder } from '../state/client-id.js';
import { backendHeaders, withBackendQuery } from '../state/backend-selection.js';


async function parseJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data.detail;
        let msg;
        if (typeof detail === 'string') msg = detail;
        else if (detail && typeof detail === 'object') msg = detail.message || JSON.stringify(detail);
        else msg = res.statusText || 'Request failed';
        throw new Error(msg);
    }
    return data;
}

export async function fetchCatalogPins() {
    const res = await fetch(withBackendQuery('/api/catalog/pins'), { headers: backendHeaders() });
    return parseJson(res);
}

export async function fetchPublishRequests(status = undefined) {
    let url = '/api/catalog/publish-requests';
    if (status) url += `?status=${encodeURIComponent(status)}`;
    const res = await fetch(withBackendQuery(url), { headers: backendHeaders() });
    return parseJson(res);
}

/**
 * @param {{
 *   repoId: string,
 *   configurationId: string,
 *   branch?: string,
 *   message?: string,
 *   pinId?: string,
 *   requestedBy?: string,
 *   backendId?: string,
 * }} opts
 */
export async function submitPublishRequest(opts) {
    const res = await fetch(withBackendQuery('/api/catalog/publish-requests'), {
        method: 'POST',
        headers: backendHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            repo_id: opts.repoId,
            configuration_id: opts.configurationId,
            branch: opts.branch || 'main',
            message: opts.message || '',
            pin_id: opts.pinId || null,
            requested_by: opts.requestedBy || getClientHolder('twin'),
            backend_id: opts.backendId || null,
        }),
    });
    return parseJson(res);
}

export async function approvePublishRequest(requestId, { approvedBy = 'owner', pinId } = {}) {
    const res = await fetch(
        withBackendQuery(`/api/catalog/publish-requests/${encodeURIComponent(requestId)}/approve`),
        {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                approved_by: approvedBy,
                pin_id: pinId || null,
            }),
        },
    );
    return parseJson(res);
}

export async function rejectPublishRequest(requestId, { rejectedBy = 'owner', reason = '' } = {}) {
    const res = await fetch(
        withBackendQuery(`/api/catalog/publish-requests/${encodeURIComponent(requestId)}/reject`),
        {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                rejected_by: rejectedBy,
                reason,
            }),
        },
    );
    return parseJson(res);
}

/**
 * Job Manager API — submit and monitor closed-loop / DAG jobs (Phase C).
 *
 * Ensemble optimization from the staged UI uses this path instead of raw
 * ``POST /api/command`` so runs acquire a lease and appear on /operations.
 */
import { store } from '../state/store.js';
import { getClientHolder } from '../state/client-id.js';
import { backendHeaders } from '../state/backend-selection.js';

/** @param {unknown} detail */
function formatApiDetail(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    }
    if (detail && typeof detail === 'object') {
        const msg = detail.message;
        if (msg && Array.isArray(detail.errors) && detail.errors.length) {
            const errs = detail.errors
                .map((e) => e.reason || e.msg || JSON.stringify(e))
                .join('; ');
            return `${msg}: ${errs}`;
        }
        return msg || JSON.stringify(detail);
    }
    return 'Request failed';
}

/**
 * Submit a closed-loop job (ensemble OPTIMIZE envelope).
 *
 * @param {{ action: string, target_id: string, parameters: object }} command
 * @param {{
 *   holder?: string,
 *   snapshot?: object,
 *   initializationPolicy?: string,
 *   onSuccess?: { commit_configuration: { repo_id: string, branch?: string, message?: string } },
 *   backendId?: string,
 * }} [opts]
 */
export async function submitClosedLoopJob(command, opts = {}) {
    const backendId = opts.backendId || store.labState?.active_backend_id;
    const body = {
        mode: 'closed_loop',
        holder: opts.holder || getClientHolder('optimization'),
        command: {
            action: command.action,
            target_id: command.target_id,
            parameters: command.parameters || {},
        },
    };
    if (backendId) body.backend_id = backendId;
    if (opts.snapshot) body.snapshot = opts.snapshot;
    body.initialization_policy = opts.initializationPolicy || 'force_reconcile';
    if (opts.onSuccess) body.on_success = opts.onSuccess;

    try {
        const response = await fetch('/api/jobs/submit', {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        const result = await response.json().catch(() => ({}));

        if (response.status === 409) {
            const detail = formatApiDetail(result?.detail) || 'Job rejected (409)';
            return { ok: false, error: detail };
        }
        if (!response.ok) {
            const detail = formatApiDetail(result?.detail) || `HTTP ${response.status}`;
            return { ok: false, error: detail };
        }

        return { ok: true, job: result };
    } catch (error) {
        return { ok: false, error: error.message || String(error) };
    }
}

/** @param {string} jobId */
export async function fetchJob(jobId) {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(formatApiDetail(result?.detail) || `HTTP ${response.status}`);
    }
    return response.json();
}

/**
 * Submit a compiled DAG job (reconcile plan steps).
 *
 * @param {{
 *   steps: object[],
 *   holder?: string,
 *   snapshot?: object,
 *   finalizeCheckout?: { repo_id: string, configuration_id: string, branch?: string },
 *   onSuccess?: { commit_configuration: { repo_id: string, branch?: string, message?: string } },
 *   backendId?: string,
 * }} opts
 */
export async function submitCompiledDagJob(opts) {
    const body = {
        mode: 'compiled_dag',
        holder: opts.holder || getClientHolder('reconcile'),
        steps: opts.steps,
    };
    if (opts.backendId) body.backend_id = opts.backendId;
    if (opts.snapshot) body.snapshot = opts.snapshot;
    body.initialization_policy = opts.initializationPolicy || 'force_reconcile';
    if (opts.finalizeCheckout) body.finalize_checkout = opts.finalizeCheckout;
    if (opts.onSuccess) body.on_success = opts.onSuccess;

    try {
        const response = await fetch('/api/jobs/submit', {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        const result = await response.json().catch(() => ({}));

        if (response.status === 409) {
            const detail = formatApiDetail(result?.detail) || 'Job rejected (409)';
            return { ok: false, error: detail };
        }
        if (!response.ok) {
            const detail = formatApiDetail(result?.detail) || `HTTP ${response.status}`;
            return { ok: false, error: detail };
        }

        return { ok: true, job: result };
    } catch (error) {
        return { ok: false, error: error.message || String(error) };
    }
}

/** Optimization compiler API (Phase E). */
import { backendHeaders, withBackendQuery } from '../state/backend-selection.js';

/**
 * Compile declarative objective graph → runtime ObjectiveSpec JSON.
 *
 * @param {{
 *   graph?: object,
 *   objective?: object,
 *   parameters?: object,
 *   preflight?: boolean,
 * }} body
 */
export async function compileObjective(body) {
    const response = await fetch(withBackendQuery('/api/optimization/compile'), {
        method: 'POST',
        headers: backendHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));

    const formatDetailErrors = (detail) => {
        if (!detail || typeof detail !== 'object') return null;
        const errors = detail.errors;
        if (!Array.isArray(errors) || !errors.length) return null;
        return errors.slice(0, 5).map((e) => {
            if (typeof e === 'string') return e;
            if (e?.msg) return `${(e.loc || []).join('.')}: ${e.msg}`;
            if (e?.reason) return `${e.term_id || e.path || '?'}: ${e.reason}`;
            return JSON.stringify(e);
        }).join('; ');
    };

    if (!response.ok) {
        const detail = result?.detail;
        const message =
            (typeof detail === 'object' && detail?.message) ||
            (typeof detail === 'string' && detail) ||
            `HTTP ${response.status}`;
        const bits = formatDetailErrors(typeof detail === 'object' ? detail : null);
        const error = bits ? `${message} — ${bits}` : message;
        console.warn('[optimize.compile] HTTP fail', { status: response.status, detail, graph: body?.graph });
        return { ok: false, error, detail };
    }

    if (result.ok === false) {
        const pf = result.preflight || result.detail || {};
        const message = pf.message || result.error || 'Objective compile/preflight failed';
        const bits = formatDetailErrors(pf);
        const error = bits ? `${message} — ${bits}` : message;
        console.warn('[optimize.compile] preflight fail', { preflight: pf, graph: body?.graph });
        return { ok: false, error, detail: pf, ...result };
    }

    return { ok: true, ...result };
}

/** @returns {Promise<{ ok: boolean, metrics?: string[], error?: string }>} */
export async function fetchOptimizationMetrics() {
    try {
        const response = await fetch(withBackendQuery('/api/optimization/metrics'), {
            headers: backendHeaders(),
        });
        if (!response.ok) {
            return { ok: false, error: `HTTP ${response.status}` };
        }
        const result = await response.json();
        return { ok: true, metrics: result.metrics || [] };
    } catch (error) {
        return { ok: false, error: error.message || String(error) };
    }
}

/**
 * Active-edge kernel catalog (proxied Twin ``GET /api/kernels``).
 * @returns {Promise<{ ok: boolean, kernels?: object[], error?: string }>}
 */
export async function fetchEdgeKernels() {
    try {
        const response = await fetch(withBackendQuery('/api/kernels'), {
            headers: backendHeaders(),
        });
        if (!response.ok) {
            return { ok: false, error: `HTTP ${response.status}`, kernels: [] };
        }
        const result = await response.json();
        const kernels = Array.isArray(result)
            ? result
            : Array.isArray(result?.kernels)
              ? result.kernels
              : [];
        return { ok: true, kernels };
    } catch (error) {
        return { ok: false, error: error.message || String(error), kernels: [] };
    }
}

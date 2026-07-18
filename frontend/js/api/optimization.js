/** Optimization compiler API (Phase E). */

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
    const response = await fetch('/api/optimization/compile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = result?.detail;
        const message =
            (typeof detail === 'object' && detail?.message) ||
            (typeof detail === 'string' && detail) ||
            `HTTP ${response.status}`;
        return { ok: false, error: message, detail };
    }
    return { ok: result.ok !== false, ...result };
}

/** @returns {Promise<{ ok: boolean, metrics?: string[], error?: string }>} */
export async function fetchOptimizationMetrics() {
    try {
        const response = await fetch('/api/optimization/metrics');
        if (!response.ok) {
            return { ok: false, error: `HTTP ${response.status}` };
        }
        const result = await response.json();
        return { ok: true, metrics: result.metrics || [] };
    } catch (error) {
        return { ok: false, error: error.message || String(error) };
    }
}

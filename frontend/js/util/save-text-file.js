/**
 * Prompt for a file name and save plain text via Twin backend (local Desktop).
 */
import { withBackendQuery, backendHeaders } from '../state/backend-selection.js';

function ensureTxtExtension(name) {
    const base = String(name || '').trim() || 'export';
    return /\.txt$/i.test(base) ? base : `${base}.txt`;
}

/**
 * @param {{ defaultName?: string, content: string, title?: string }} opts
 * @returns {Promise<{ ok: boolean, filename?: string, error?: string }>}
 */
export async function promptAndSaveTextFile(opts) {
    const content = String(opts?.content ?? '');
    if (!content.trim()) {
        return { ok: false, error: 'Nothing to save yet' };
    }
    const suggested = ensureTxtExtension(opts?.defaultName || 'export');
    const title = opts?.title || 'Save as';
    const typed = window.prompt(title, suggested);
    if (typed == null) {
        return { ok: false, error: 'cancelled' };
    }
    const filename = ensureTxtExtension(typed);
    try {
        const res = await fetch(withBackendQuery('/api/local/save-text'), {
            method: 'POST',
            headers: {
                ...backendHeaders({ 'Content-Type': 'application/json' }),
            },
            body: JSON.stringify({ filename, content }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            const detail = body?.detail || res.statusText || 'Save failed';
            return {
                ok: false,
                error: typeof detail === 'string' ? detail : JSON.stringify(detail),
            };
        }
        return { ok: true, filename: body.filename || filename };
    } catch (err) {
        return { ok: false, error: err?.message || String(err) };
    }
}

/**
 * Lab-level optimization reference (not stored on component tunables).
 */
import { store } from '../state/store.js';
import { executeSendCommand } from './commands.js';

/** Pin COBYLA reference via ``SET_COBYLA_REFERENCE`` (reads ``measurables.camera_image``). */
export async function pinLabOptimizationReference(tagId) {
    const result = await executeSendCommand({
        action: 'SET_COBYLA_REFERENCE',
        target_id: tagId,
        parameters: {},
    });
    if (!result?.ok) {
        const err = result.error;
        const msg = typeof err === 'string' ? err : JSON.stringify(err);
        throw new Error(msg || 'Failed to pin reference');
    }
    return result;
}

export async function clearLabOptimizationReference() {
    const r = await fetch('/api/lab/optimization-reference', { method: 'DELETE' });
    if (!r.ok && r.status !== 404) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `Failed to clear reference (${r.status})`);
    }
    if (store.labState) {
        delete store.labState.optimization_reference;
    }
}

export function labOptimizationReference() {
    const ref = store.labState?.optimization_reference;
    return ref && typeof ref === 'object' ? ref : null;
}

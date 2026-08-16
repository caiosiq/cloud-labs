/**
 * Twin helpers for edge kernel catalog + authoring probe (EVAL_KERNEL).
 */
import { labClient } from '../cloudlabs/client.js';
import { fetchEdgeKernels } from './optimization.js';

/** @type {{ at: number, kernels: object[]|null }} */
let _catalogCache = { at: 0, kernels: null };
const CATALOG_TTL_MS = 30_000;

/**
 * Cached active-edge kernel list (``GET /api/kernels``).
 * @returns {Promise<{ ok: boolean, kernels?: object[], error?: string }>}
 */
export async function getEdgeKernelsCached(force = false) {
    if (
        !force &&
        Array.isArray(_catalogCache.kernels) &&
        Date.now() - _catalogCache.at < CATALOG_TTL_MS
    ) {
        return { ok: true, kernels: _catalogCache.kernels };
    }
    const result = await fetchEdgeKernels();
    if (result.ok && Array.isArray(result.kernels)) {
        _catalogCache = { at: Date.now(), kernels: result.kernels };
    }
    return result;
}

/**
 * Premade TorchScript kernels that apply to an image measurable.
 * Catalog entries today are camera kernels (camera_image / ImageViewer).
 *
 * @param {object[]} kernels
 * @param {string} fieldName
 * @param {object} [descriptor]
 * @returns {object[]}
 */
export function isImageMeasurableField(fieldName, descriptor) {
    const widget = String((descriptor && descriptor.widget) || '');
    return (
        fieldName === 'camera_image' ||
        widget === 'ImageViewer' ||
        String(fieldName || '').endsWith('.camera_image')
    );
}

/**
 * @param {object[]} kernels
 * @param {string} fieldName
 * @param {object} [descriptor]
 * @returns {object[]}
 */
export function kernelsForMeasurableField(kernels, fieldName, descriptor) {
    if (!isImageMeasurableField(fieldName, descriptor)) return [];

    return (kernels || []).filter((k) => {
        if (!k || typeof k !== 'object') return false;
        const id = String(k.id || '').trim();
        if (!id || id.startsWith('ensemble.')) return false;
        if (k.artifact_present === false) return false;
        const runtime = String(k.runtime || 'torchscript').toLowerCase();
        if (runtime && runtime !== 'torchscript') return false;
        return true;
    });
}

/**
 * One-shot authoring probe via ``labClient.probeKernel`` → EVAL_KERNEL.
 *
 * @param {string} tagId
 * @param {{ kernel_id: string, field?: string, skipConfirm?: boolean }} opts
 * @returns {Promise<{ ok: boolean, result?: object, error?: string }>}
 */
export async function probeKernel(tagId, opts) {
    const kernelId = opts && opts.kernel_id;
    if (!kernelId) {
        return { ok: false, error: 'kernel_id required' };
    }
    if (!(opts && opts.skipConfirm)) {
        const { confirmPrimitiveCommand } = await import('./confirm-primitive.js');
        const ok = await confirmPrimitiveCommand({
            action: 'EVAL_KERNEL',
            target_id: tagId,
            parameters: {
                kernel_id: kernelId,
                field: (opts && opts.field) || 'camera_image',
            },
        });
        if (!ok) return { ok: false, error: 'cancelled' };
    }
    try {
        const result = await labClient.probeKernel(tagId, {
            kernel_id: kernelId,
            field: (opts && opts.field) || 'camera_image',
        });
        return { ok: true, result: result && typeof result === 'object' ? result : {} };
    } catch (error) {
        return { ok: false, error: (error && error.message) || String(error) };
    }
}

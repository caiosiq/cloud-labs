/**
 * Platform capability registry mirror (Phase 6–7).
 *
 * Prefer `loadPlatformRegistries()` (`GET /api/platform/registries`);
 * static fallbacks match backend `lab_model` plugins when offline.
 */

/** @type {Record<string, string>} */
export const TUNABLE_WRITE_PRIMITIVE_FALLBACK = {
    exposure_time_ms: 'SET_EXPOSURE',
    nominal_pose: 'MOVE_COMPONENT',
    nominal_motor_positions: 'SET_MOTOR_SETPOINT',
};

/** @type {Record<string, string>} */
export const MEASURABLE_WIDGET_FALLBACK = {
    camera_image: 'ImageViewer',
    last_optimization_score: 'NumberBadge',
    output_power_readback_mw: 'NumberBadge',
};

export const MEASURABLE_RECORD_PRIMITIVE_FALLBACK = 'RECORD_MEASURABLES';

import { backendHeaders, withBackendQuery } from './state/backend-selection.js';

/** @type {object|null} */
let _cached = null;

function _fallbackPayload() {
    const tunables = {};
    for (const [field, write_primitive] of Object.entries(TUNABLE_WRITE_PRIMITIVE_FALLBACK)) {
        tunables[field] = { widget: field, write_primitive };
    }
    const measurables = {};
    for (const [field, widget] of Object.entries(MEASURABLE_WIDGET_FALLBACK)) {
        measurables[field] = { widget };
    }
    return {
        tunables,
        measurables,
        primitives: {},
        record_measurables_primitive: MEASURABLE_RECORD_PRIMITIVE_FALLBACK,
    };
}

/**
 * Load registries from the backend (cached for the page session).
 * @returns {Promise<{ tunables: object, measurables: object, primitives: object }>}
 */
export async function loadPlatformRegistries() {
    if (_cached) return _cached;
    try {
        const res = await fetch(withBackendQuery('/api/platform/registries'), {
            headers: backendHeaders(),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        _cached = await res.json();
        return _cached;
    } catch (e) {
        console.warn('[lab-capabilities] registry fetch failed, using fallbacks:', e);
        _cached = _fallbackPayload();
        return _cached;
    }
}

/**
 * @param {string} fieldId
 * @returns {Promise<string|undefined>}
 */
export async function writePrimitiveForTunable(fieldId) {
    const reg = await loadPlatformRegistries();
    return reg.tunables?.[fieldId]?.write_primitive
        ?? TUNABLE_WRITE_PRIMITIVE_FALLBACK[fieldId];
}

/** Back-compat sync accessor (static fallback only). */
export function writePrimitiveForTunableSync(fieldId) {
    return TUNABLE_WRITE_PRIMITIVE_FALLBACK[fieldId];
}

/**
 * Default camera exposure (seconds) for COBYLA / NEWTON when the command
 * omits an explicit `exposure` parameter.
 *
 * Phase 9d removed the table-cam dock; prefer the selected component's
 * catalog tunable `exposure_time_ms` when present.
 */
import { store } from './state/store.js';

export function getTableCamExposureSeconds() {
    const fallback = store.defaultCameraExposureSec;
    const tagId = store.selectedComponent;
    if (!tagId || !store.labState || !store.labState.components) return fallback;
    const comp = store.labState.components[tagId];
    const ms = comp && comp.tunables && comp.tunables.exposure_time_ms;
    if (typeof ms === 'number' && Number.isFinite(ms) && ms > 0) {
        return ms / 1000;
    }
    return fallback;
}

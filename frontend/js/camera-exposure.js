/**
 * Default camera exposure (seconds) for COBYLA / NEWTON when the command
 * omits an explicit `exposure` parameter.
 */
import { store } from './state/store.js';
import { tunableValue } from './component-state.js';

export function getTableCamExposureSeconds() {
    const fallback = store.defaultCameraExposureSec;
    const tagId = store.selectedComponent;
    if (!tagId || !store.labState?.components) return fallback;
    const comp = store.labState.components[tagId];
    const ms = tunableValue(comp, 'exposure_time_ms');
    if (typeof ms === 'number' && Number.isFinite(ms) && ms > 0) {
        return ms / 1000;
    }
    return fallback;
}

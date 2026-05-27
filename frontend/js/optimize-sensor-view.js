/**
 * Open the optimization sensor camera panel and start its live feed when supported.
 */
import { openPanel } from './ui/context-panel.js';
import { startLiveFeed } from './api/live-feed.js';
import { getCatalogRow } from './component-model.js';
import { normalizeCapabilities } from './component-state.js';
import { supportsLiveVideo } from './catalog-support.js';
import { fetchLabState } from './state/lab-state.js';

function liveFeedChannelFor(tagId) {
    const row = getCatalogRow(tagId);
    const lf = normalizeCapabilities(row?.capabilities).telemetry?.live_feed || {};
    const keys = Object.keys(lf);
    return keys[0] || 'stream';
}

/**
 * @param {string} sensorId
 * @param {{ log?: Function, refreshPanel?: Function }} [hooks]
 */
export async function prepareOptimizeSensorView(sensorId, hooks = {}) {
    if (!sensorId) return;
    openPanel(sensorId, { add: true });
    if (!supportsLiveVideo(sensorId)) return;

    const channel = liveFeedChannelFor(sensorId);
    try {
        await startLiveFeed(sensorId, channel);
        try {
            await fetchLabState();
        } catch (_e) {
            /* poll will catch up */
        }
        if (typeof hooks.refreshPanel === 'function') {
            hooks.refreshPanel(sensorId);
        }
    } catch (e) {
        if (typeof hooks.log === 'function') {
            hooks.log(`Sensor live feed failed: ${e.message || e}`, 'error');
        }
    }
}

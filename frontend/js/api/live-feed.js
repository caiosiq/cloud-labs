/**
 * Live feed session controls (START_LIVE_FEED / END_LIVE_FEED).
 */
import { applyComponentTelemetryFromServer } from '../component-state.js';
import { stopJpegPollForTag } from '../widgets/jpeg-poll-registry.js';

function _applyTelemetryResponse(tagId, body) {
    if (body && body.telemetry) {
        applyComponentTelemetryFromServer(tagId, body.telemetry);
    }
}

export async function startLiveFeed(tagId, channel = 'stream') {
    const r = await fetch(
        `/api/components/${encodeURIComponent(tagId)}/telemetry/live-feed/start?channel=${encodeURIComponent(channel)}`,
        { method: 'POST' },
    );
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
        throw new Error(body.detail || `Live feed start failed (${r.status})`);
    }
    _applyTelemetryResponse(tagId, body);
    return body;
}

export async function endLiveFeed(tagId, channel = 'all') {
    stopJpegPollForTag(tagId);
    const r = await fetch(
        `/api/components/${encodeURIComponent(tagId)}/telemetry/live-feed/end?channel=${encodeURIComponent(channel)}`,
        { method: 'POST' },
    );
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
        throw new Error(body.detail || `Live feed end failed (${r.status})`);
    }
    _applyTelemetryResponse(tagId, body);
    return body;
}

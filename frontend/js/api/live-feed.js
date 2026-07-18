/**
 * Live feed session controls (START_LIVE_FEED / END_LIVE_FEED).
 *
 * Thin Twin wrapper over {@link labClient} — same verbs as Python
 * ``lab.start_live_feed`` / ``lab.end_live_feed``.
 */
import { applyComponentTelemetryFromServer } from '../component-state.js';
import { labClient } from '../cloudlabs/client.js';
import { stopJpegPollForTag } from '../widgets/jpeg-poll-registry.js';

function _applyTelemetryResponse(tagId, body) {
    if (body && body.telemetry) {
        applyComponentTelemetryFromServer(tagId, body.telemetry);
    }
}

export async function startLiveFeed(tagId, channel = 'stream') {
    const body = await labClient.startLiveFeed(tagId, channel);
    _applyTelemetryResponse(tagId, body);
    return body;
}

export async function endLiveFeed(tagId, channel = 'all') {
    stopJpegPollForTag(tagId);
    const body = await labClient.endLiveFeed(tagId, channel);
    _applyTelemetryResponse(tagId, body);
    return body;
}

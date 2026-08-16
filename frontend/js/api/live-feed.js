/**
 * Live feed session controls (START_LIVE_FEED / END_LIVE_FEED / SET_LIVE_EXPOSURE).
 *
 * Thin Twin wrapper over {@link labClient} — same verbs as Python
 * ``lab.start_live_feed`` / ``lab.end_live_feed``.
 */
import { applyComponentTelemetryFromServer } from '../component-state.js';
import { labClient } from '../cloudlabs/client.js';
import { stopJpegPollForTag } from '../widgets/jpeg-poll-registry.js';
import { closeLiveFeedPopout } from '../ui/live-feed-popout.js';
import { store } from '../state/store.js';
import { confirmPrimitiveCommand } from './confirm-primitive.js';
import { log } from '../ui/log.js';

function _applyTelemetryResponse(tagId, body) {
    if (body && body.telemetry) {
        applyComponentTelemetryFromServer(tagId, body.telemetry);
    }
}

/**
 * @param {string} tagId
 * @param {string} [channel]
 * @param {{ exposure_time_ms?: number, skipConfirm?: boolean }} [opts]
 */
export async function startLiveFeed(tagId, channel = 'stream', opts = {}) {
    if (!opts.skipConfirm && !store.isRecording) {
        const params = {};
        if (Number.isFinite(Number(opts.exposure_time_ms)) && Number(opts.exposure_time_ms) > 0) {
            params.exposure_time_ms = Number(opts.exposure_time_ms);
        }
        const ok = await confirmPrimitiveCommand({
            action: 'START_LIVE_FEED',
            target_id: tagId,
            channel,
            parameters: params,
        });
        if (!ok) {
            log(`START_LIVE_FEED ${tagId} cancelled by user.`, 'info');
            throw new Error('cancelled');
        }
    }
    const body = await labClient.startLiveFeed(tagId, channel, opts);
    _applyTelemetryResponse(tagId, body);
    return body;
}

/**
 * @param {string} tagId
 * @param {string} [channel]
 * @param {{ skipConfirm?: boolean }} [opts]
 */
export async function endLiveFeed(tagId, channel = 'all', opts = {}) {
    if (!opts.skipConfirm && !store.isRecording) {
        const ok = await confirmPrimitiveCommand({
            action: 'END_LIVE_FEED',
            target_id: tagId,
            channel,
        });
        if (!ok) {
            log(`END_LIVE_FEED ${tagId} cancelled by user.`, 'info');
            throw new Error('cancelled');
        }
    }
    stopJpegPollForTag(tagId);
    closeLiveFeedPopout(tagId);
    const body = await labClient.endLiveFeed(tagId, channel);
    _applyTelemetryResponse(tagId, body);
    return body;
}

/**
 * Preview (VEXP) only — does not change science SET_EXPOSURE.
 * @param {string} tagId
 * @param {number} exposureTimeMs
 * @param {{ skipConfirm?: boolean }} [opts]
 */
export async function setLiveExposure(tagId, exposureTimeMs, opts = {}) {
    if (!opts.skipConfirm && !store.isRecording) {
        const ok = await confirmPrimitiveCommand({
            action: 'SET_LIVE_EXPOSURE',
            target_id: tagId,
            parameters: { exposure_time_ms: Number(exposureTimeMs) },
        });
        if (!ok) {
            log(`SET_LIVE_EXPOSURE ${tagId} cancelled by user.`, 'info');
            throw new Error('cancelled');
        }
    }
    const body = await labClient.setLiveExposure(tagId, exposureTimeMs);
    _applyTelemetryResponse(tagId, body);
    return body;
}

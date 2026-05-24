/**
 * Start/stop live-pose polls when TeleOp sessions change.
 */
import { store } from './state/store.js';
import { maybeRepairTeleopTargetSeed } from './teleop-pose.js';
import { startTeleopLivePosePoll, stopTeleopLivePosePoll } from './api/teleop-live-pose.js';
import { isTeleopReady, normalizeCapabilities } from './component-state.js';
import { getCatalogRow } from './component-model.js';

const _activePollTags = new Set();

export function syncTeleopLivePosePolls() {
    const comps = store.labState?.components || {};
    const shouldPoll = new Set();

    Object.entries(comps).forEach(([tagId, comp]) => {
        if (!isTeleopReady(comp)) return;
        const row = getCatalogRow(tagId);
        const caps = normalizeCapabilities(row?.capabilities);
        const desc = caps.telemetry?.teleop?.live_pose;
        if (!desc) return;
        shouldPoll.add(tagId);
        if (!_activePollTags.has(tagId)) {
            startTeleopLivePosePoll(tagId, {
                descriptor: desc,
                onUpdate: () => {
                    maybeRepairTeleopTargetSeed(tagId);
                    if (typeof store._teleopLivePoseRender === 'function') {
                        store._teleopLivePoseRender(tagId);
                    }
                },
            });
            _activePollTags.add(tagId);
        }
    });

    Array.from(_activePollTags).forEach((tagId) => {
        if (!shouldPoll.has(tagId)) {
            stopTeleopLivePosePoll(tagId);
            _activePollTags.delete(tagId);
            delete store.teleopTarget[tagId];
        }
    });
}

export function stopTeleopPollForTag(tagId) {
    if (_activePollTags.has(tagId)) {
        stopTeleopLivePosePoll(tagId);
        _activePollTags.delete(tagId);
    }
    delete store.teleopTarget[tagId];
}

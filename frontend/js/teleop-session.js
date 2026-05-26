/**
 * Start/stop live-pose transport when TeleOp sessions change.
 *
 * Prefers WebSocket push (Phase 4); falls back to HTTP poll for debug/legacy.
 */
import { store } from './state/store.js';
import {
    clearTeleopTargetAwaitingLive,
    maybeRepairTeleopTargetSeed,
    maybeSyncTeleopTargetFromFirstLivePose,
} from './teleop-pose.js';
import { startTeleopLivePosePoll, stopTeleopLivePosePoll } from './api/teleop-live-pose.js';
import {
    shouldUseWebSocketTransport,
    startTeleopWsSession,
    stopTeleopWsSession,
} from './api/teleop-session-ws.js';
import { isTeleopReady, normalizeCapabilities } from './component-state.js';
import { getCatalogRow } from './component-model.js';

const _activeTags = new Set();

function onLivePoseUpdate(tagId) {
    maybeSyncTeleopTargetFromFirstLivePose(tagId);
    maybeRepairTeleopTargetSeed(tagId);
    if (typeof store._teleopLivePoseRender === 'function') {
        store._teleopLivePoseRender(tagId);
    }
}

function startLiveTransport(tagId, descriptor) {
    const opts = {
        descriptor,
        onUpdate: () => onLivePoseUpdate(tagId),
    };
    if (shouldUseWebSocketTransport(descriptor)) {
        opts.onFallback = () => {
            startTeleopLivePosePoll(tagId, {
                descriptor,
                onUpdate: () => onLivePoseUpdate(tagId),
            });
        };
        startTeleopWsSession(tagId, opts);
    } else {
        startTeleopLivePosePoll(tagId, opts);
    }
}

function stopLiveTransport(tagId) {
    stopTeleopWsSession(tagId);
    stopTeleopLivePosePoll(tagId);
}

export function syncTeleopLivePosePolls() {
    const comps = store.labState?.components || {};
    const shouldRun = new Set();

    Object.entries(comps).forEach(([tagId, comp]) => {
        if (!isTeleopReady(comp)) return;
        const row = getCatalogRow(tagId);
        const caps = normalizeCapabilities(row?.capabilities);
        const desc = caps.telemetry?.teleop?.live_pose;
        if (!desc) return;
        shouldRun.add(tagId);
        if (!_activeTags.has(tagId)) {
            startLiveTransport(tagId, desc);
            _activeTags.add(tagId);
        }
    });

    Array.from(_activeTags).forEach((tagId) => {
        if (!shouldRun.has(tagId)) {
            stopLiveTransport(tagId);
            _activeTags.delete(tagId);
            delete store.teleopTarget[tagId];
            clearTeleopTargetAwaitingLive(tagId);
        }
    });
}

export function stopTeleopPollForTag(tagId) {
    if (_activeTags.has(tagId)) {
        stopLiveTransport(tagId);
        _activeTags.delete(tagId);
    }
    delete store.teleopTarget[tagId];
    clearTeleopTargetAwaitingLive(tagId);
}

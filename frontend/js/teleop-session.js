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
    // Twin always owns `/teleop/session` (in-process push or HTTP-edge proxy).
    // Catalog often lists HTTP `…/live-pose` only — that disabled WS and left
    // CURRENT blank when polls lacked backend_id. Prefer WS; fall back to poll.
    const twinWsDesc = {
        ...(descriptor || {}),
        url: '/api/components/{tag_id}/teleop/session',
        transport: 'websocket',
    };
    const httpDesc = descriptor && String(descriptor.url || '').includes('live-pose')
        ? descriptor
        : { url: '/api/components/{tag_id}/telemetry/live-pose', default_fps: 20 };
    const opts = {
        descriptor: twinWsDesc,
        onUpdate: () => onLivePoseUpdate(tagId),
        onFallback: () => {
            startTeleopLivePosePoll(tagId, {
                descriptor: httpDesc,
                onUpdate: () => onLivePoseUpdate(tagId),
            });
        },
    };
    startTeleopWsSession(tagId, opts);
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
        // Start transport whenever TeleOp is ready — do not require a catalog
        // live_pose descriptor (Twin has a default session URL).
        const desc = caps.telemetry?.teleop?.live_pose || {
            url: '/api/components/{tag_id}/teleop/session',
            transport: 'websocket',
            default_fps: 20,
        };
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

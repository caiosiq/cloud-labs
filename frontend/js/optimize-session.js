/**
 * Autonomous OPTIMIZE telemetry — WebSocket (preferred) or HTTP live-pose fallback.
 */
import { store } from './state/store.js';
import { getCatalogRow } from './component-model.js';
import { supportsLiveVideo, supportsTeleop, defaultOptimizeSensorPool, buildOptimizeStrategies } from './catalog-support.js';
import { startTeleopLivePosePoll, stopTeleopLivePosePoll } from './api/teleop-live-pose.js';
import {
    drawOptimizationLossChart,
} from './ui/optimization-preview.js';
import {
    onOptimizationTick,
} from './ui/optimization-sidebar.js';
import { notifyOptimizeReadouts } from './optimize-live-readout.js';

const _activeTarget = { tagId: null, ws: null, mode: null };
/** Last chart ``iteration`` pushed per tag (step dedupe across motion polls). */
const _lastStepIteration = {};
let _watchTimer = null;

function resetStepTracking(tagId) {
    if (tagId) {
        delete _lastStepIteration[tagId];
    } else {
        Object.keys(_lastStepIteration).forEach((k) => {
            delete _lastStepIteration[k];
        });
    }
}

const LIVE_POSE_DESCRIPTOR = {
    url: '/api/components/{tag_id}/telemetry/live-pose',
    default_fps: 20,
};

function wsUrl(tagId) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/api/components/${encodeURIComponent(tagId)}/optimize/session`;
}

/** True while an OPTIMIZE run is active (or legacy BUSY during startup). */
export function isSystemOptimizing(status) {
    return status === 'OPTIMIZING' || status === 'BUSY';
}

function tickFromMsg(msg, pose) {
    const tick = {
        iteration: msg?.iteration ?? pose?.iteration,
        loss: msg?.loss ?? pose?.loss,
        pose: {
            x: pose.x,
            y: pose.y,
            z: pose.z,
            rotation: pose.rotation,
        },
    };
    if (pose.motor_positions && typeof pose.motor_positions === 'object') {
        tick.motor_positions = { ...pose.motor_positions };
    }
    return tick;
}

function messageKind(msg, pose, tagId) {
    const iter = msg?.iteration ?? pose?.iteration;
    if (iter != null && _lastStepIteration[tagId] !== iter) {
        return 'step';
    }
    if (msg?.type === 'step') return 'step';
    if (msg?.type === 'motion') return 'motion';
    if (msg?.iteration != null || msg?.loss != null) return 'step';
    if (pose?.iteration != null || pose?.loss != null) return 'step';
    return 'motion';
}

function applyTick(tagId, msg) {
    const pose = (msg && msg.pose) || msg;
    if (!pose || typeof pose !== 'object') return;
    store.teleopLivePose[tagId] = { ...pose };

    const kind = messageKind(msg, pose, tagId);
    if (kind === 'step') {
        const tick = tickFromMsg(msg, pose);
        if (tick.iteration != null) {
            _lastStepIteration[tagId] = tick.iteration;
        }
        store.optimizeTick = tick;
        onOptimizationTick(tick);
        notifyOptimizeReadouts(tagId);
    }

    if (typeof store._teleopLivePoseRender === 'function') {
        store._teleopLivePoseRender(tagId);
    }
}

function stopOptimizeTransport() {
    if (_activeTarget.ws) {
        try { _activeTarget.ws.close(); } catch (_) { /* noop */ }
        _activeTarget.ws = null;
    }
    if (_activeTarget.tagId) {
        stopTeleopLivePosePoll(_activeTarget.tagId);
        delete store.teleopLivePose[_activeTarget.tagId];
        resetStepTracking(_activeTarget.tagId);
    }
    _activeTarget.tagId = null;
    _activeTarget.mode = null;
}

function transportLiveFor(tagId) {
    if (_activeTarget.tagId !== tagId) return false;
    if (_activeTarget.mode === 'http') return true;
    if (_activeTarget.ws != null) {
        const rs = _activeTarget.ws.readyState;
        return rs === WebSocket.OPEN || rs === WebSocket.CONNECTING;
    }
    return false;
}

function startOptimizeHttpFallback(tagId) {
    if (_activeTarget.tagId === tagId && _activeTarget.mode === 'http') return;
    if (_activeTarget.ws) {
        try { _activeTarget.ws.close(); } catch (_) { /* noop */ }
        _activeTarget.ws = null;
    }
    _activeTarget.tagId = tagId;
    _activeTarget.mode = 'http';
    startTeleopLivePosePoll(tagId, {
        descriptor: LIVE_POSE_DESCRIPTOR,
        fps: 25,
        onUpdate: (pose) => {
            const kind = pose?.optimize_event
                || ((pose?.iteration != null || pose?.loss != null) ? 'step' : 'motion');
            applyTick(tagId, {
                type: kind,
                pose,
                iteration: pose?.iteration,
                loss: pose?.loss,
            });
        },
    });
}

function startOptimizeWs(tagId, { resetChart = false } = {}) {
    if (transportLiveFor(tagId)) return;

    if (_activeTarget.tagId !== tagId) {
        stopOptimizeTransport();
        _activeTarget.tagId = tagId;
    }

    if (resetChart) {
        store.optimizeLossSeries = [];
        resetStepTracking(tagId);
        drawOptimizationLossChart();
    }

    let ws;
    try {
        ws = new WebSocket(wsUrl(tagId));
    } catch (_) {
        startOptimizeHttpFallback(tagId);
        return;
    }
    _activeTarget.ws = ws;
    _activeTarget.mode = 'ws';

    ws.onmessage = (ev) => {
        try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'motion' || msg.type === 'step' || msg.type === 'tick') {
                applyTick(tagId, msg);
            }
        } catch (_) { /* ignore */ }
    };
    ws.onclose = () => {
        if (_activeTarget.tagId === tagId && isSystemOptimizing(store.labState?.system_status)) {
            _activeTarget.ws = null;
            _activeTarget.mode = null;
            startOptimizeHttpFallback(tagId);
        }
    };
    ws.onerror = () => {
        ws.close();
    };
}

export function stopOptimizeTelemetryWatch() {
    if (_watchTimer != null) {
        clearTimeout(_watchTimer);
        _watchTimer = null;
    }
}

async function optimizeTelemetryWatchTick() {
    const target = store.optimizeActiveTarget;
    if (!target) {
        stopOptimizeTelemetryWatch();
        return;
    }

    const status = store.labState?.system_status;
    const active = isSystemOptimizing(status);
    const pending = store.pendingCommands.has(target);

    if (!active) {
        try {
            const { fetchLabState } = await import('./state/lab-state.js');
            await fetchLabState();
        } catch (_) {
            /* retry next tick */
        }
    }

    syncOptimizeLivePosePolls();

    const stillActive = isSystemOptimizing(store.labState?.system_status);
    const stillPending = store.pendingCommands.has(target);
    if (stillActive || stillPending) {
        _watchTimer = setTimeout(() => { void optimizeTelemetryWatchTick(); }, 100);
    } else {
        stopOptimizeTelemetryWatch();
    }
}

export function beginOptimizeTelemetryWatch() {
    stopOptimizeTelemetryWatch();
    void optimizeTelemetryWatchTick();
}

export function resetOptimizeStepTracking(tagId) {
    resetStepTracking(tagId);
}

export function armOptimizeSession(tagId) {
    resetStepTracking(tagId);
    beginOptimizeTelemetryWatch();
}

export function isOptimizeLiveActive(tagId) {
    return (
        store.optimizeActiveTarget === tagId
        && isSystemOptimizing(store.labState?.system_status)
    );
}

export function isOptimizePendingOrActive(tagId) {
    return (
        store.optimizeActiveTarget === tagId
        && (
            isSystemOptimizing(store.labState?.system_status)
            || store.pendingCommands.has(tagId)
        )
    );
}

export function supportsOptimizeLiveCanvas(tagId) {
    return supportsTeleop(tagId);
}

export function syncOptimizeLivePosePolls() {
    const target = store.optimizeActiveTarget;
    const active = isSystemOptimizing(store.labState?.system_status);
    const shouldRun = active && target ? target : null;

    if (shouldRun) {
        startOptimizeWs(shouldRun, { resetChart: false });
    } else if (!shouldRun && _activeTarget.tagId) {
        stopOptimizeTransport();
    }
}

export function listOptimizeSensorCandidates(tagId) {
    const row = getCatalogRow(tagId);
    const opt = (row?.capabilities || {}).optimize || {};
    let list = Array.isArray(opt.sensor_candidates) ? opt.sensor_candidates.slice() : [];
    if (!list.length) list = defaultOptimizeSensorPool();
    return list.filter((id) => typeof id === 'string' && id);
}

export function getOptimizeStrategies(tagId) {
    return buildOptimizeStrategies(tagId);
}

export function defaultOptimizeSensor(tagId) {
    const row = getCatalogRow(tagId);
    const opt = (row?.capabilities || {}).optimize || {};
    if (typeof opt.default_sensor === 'string' && opt.default_sensor) {
        return opt.default_sensor;
    }
    const candidates = listOptimizeSensorCandidates(tagId);
    return candidates[0] || null;
}

export function cobylaReferenceReady() {
    const ref = store.labState?.optimization_reference;
    return !!(ref && typeof ref === 'object' && ref.path);
}

export function getOptimizeFormPrefs(tagId) {
    const prefs = store.optimizeFormPrefs?.[tagId];
    return prefs && typeof prefs === 'object' ? prefs : null;
}

export function saveOptimizeFormPrefs(tagId, prefs) {
    if (!tagId) return;
    store.optimizeFormPrefs[tagId] = { ...prefs };
}

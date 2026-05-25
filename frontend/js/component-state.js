/**
 * Client accessors for statecontrol + telemetry component shape.
 *
 * All UI reads of tunables / measurables / telemetry should go through
 * this module — never ``comp.tunables`` or ``comp.statecontrol`` directly.
 */
import { store } from './state/store.js';
import { stopJpegPollForTag } from './widgets/jpeg-poll-registry.js';

export function getStatecontrol(comp) {
    if (!comp || typeof comp !== 'object') {
        return { tunables: {}, measurables: {} };
    }
    if (comp.statecontrol && typeof comp.statecontrol === 'object') {
        return {
            tunables: comp.statecontrol.tunables || {},
            measurables: comp.statecontrol.measurables || {},
        };
    }
    return {
        tunables: comp.tunables || {},
        measurables: comp.measurables || {},
    };
}

/** @param {object | undefined} comp */
export function getTunables(comp) {
    return getStatecontrol(comp).tunables || {};
}

/** @param {object | undefined} comp */
export function getMeasurables(comp) {
    return getStatecontrol(comp).measurables || {};
}

/**
 * Resolve a tunable field from statecontrol (v1) or legacy flat shape.
 * @param {object | undefined} comp
 * @param {string} fieldName
 */
export function tunableValue(comp, fieldName) {
    return getTunables(comp)[fieldName];
}

/**
 * Resolve a measurable field. Handles nested pose fields declared at catalog top level.
 * @param {object | undefined} comp
 * @param {string} fieldName
 */
export function measurableValue(comp, fieldName) {
    const meas = getMeasurables(comp);
    if (fieldName === 'motor_rotations') {
        const nested = meas.pose?.motor_rotations;
        if (nested && typeof nested === 'object') return nested;
        return meas.motor_rotations;
    }
    if (fieldName === 'pose') {
        return meas.pose;
    }
    return meas[fieldName];
}

/**
 * JSON snapshot for context-panel refresh detection (tunables + measurables + telemetry).
 * @param {object | undefined} comp
 */
export function componentDataSnapshot(comp) {
    if (!comp || typeof comp !== 'object') return null;
    return JSON.stringify({
        tunables: getTunables(comp),
        measurables: getMeasurables(comp),
        telemetry: getTelemetry(comp),
    });
}

/**
 * Merge server telemetry into ``store.labState`` after a dedicated telemetry route
 * (live-feed start/end, teleop start/end) so the panel updates without waiting for poll.
 *
 * @param {string} tagId
 * @param {object | null | undefined} telemetry
 * @returns {boolean} whether state was patched
 */
export function applyComponentTelemetryFromServer(tagId, telemetry) {
    if (!tagId || !telemetry || typeof telemetry !== 'object') return false;
    if (!store.labState?.components?.[tagId]) return false;
    const entry = store.labState.components[tagId];
    const prev = getTelemetry(entry);
    const wasLive = isLiveFeedActive(entry, 'stream');
    entry.telemetry = {
        teleop: { ...prev.teleop, ...(telemetry.teleop || {}) },
        live_feed: { ...prev.live_feed, ...(telemetry.live_feed || {}) },
    };
    if (wasLive && !isLiveFeedActive(entry, 'stream')) {
        stopJpegPollForTag(tagId);
    }
    return true;
}

export function getTelemetry(comp) {
    if (!comp || typeof comp !== 'object') {
        return { teleop: { active: false, ready: false }, live_feed: {} };
    }
    if (comp.telemetry && typeof comp.telemetry === 'object') {
        return comp.telemetry;
    }
    const sc = getStatecontrol(comp);
    return {
        teleop: {
            active: !!sc.tunables.teleop_active,
            ready: !!sc.tunables.teleop_active,
            last_jog_ts: sc.tunables.teleop_last_jog_ts ?? null,
        },
        live_feed: {},
    };
}

export function isTeleopActive(comp) {
    return !!getTelemetry(comp).teleop?.active;
}

export function isTeleopReady(comp) {
    const teleop = getTelemetry(comp).teleop || {};
    return !!(teleop.active && teleop.ready);
}

export function isTeleopStarting(comp) {
    const teleop = getTelemetry(comp).teleop || {};
    return !!(teleop.active && !teleop.ready);
}

export function isLiveFeedActive(comp, channel = 'stream') {
    const ch = getTelemetry(comp).live_feed?.[channel];
    return !!(ch && ch.connected && ch.live);
}

export function normalizeCapabilities(caps) {
    if (!caps || typeof caps !== 'object') {
        return {
            statecontrol: { tunables: {}, measurables: {} },
            telemetry: { teleop: {}, live_feed: {} },
            primitives: [],
        };
    }
    if (caps.statecontrol) {
        const tel = { ...(caps.telemetry || {}) };
        tel.teleop = normalizeTeleopBlock(tel.teleop);
        tel.live_feed = tel.live_feed || {};
        return {
            statecontrol: caps.statecontrol || { tunables: {}, measurables: {} },
            telemetry: tel,
            primitives: caps.primitives || [],
        };
    }
    const tunables = { ...(caps.tunables || {}) };
    const teleop = normalizeTeleopBlock(tunables.teleop);
    delete tunables.teleop;
    return {
        statecontrol: {
            tunables,
            measurables: caps.measurables || {},
        },
        telemetry: {
            teleop,
            live_feed: caps.telemetry || {},
        },
        primitives: caps.primitives || [],
    };
}

function migrateLegacyPoseChannel(teleop) {
    if (!teleop || typeof teleop !== 'object' || !teleop.pose) return teleop;
    if (teleop.rz && teleop.pose3d) {
        const { pose: _drop, ...rest } = teleop;
        return rest;
    }
    const legacy = teleop.pose;
    const stepDeg = legacy.step_deg || [0.5, 2.0, 10.0];
    const stepMm = legacy.step_mm || [0.5, 2.0, 10.0];
    const { pose: _drop, ...rest } = teleop;
    return {
        ...rest,
        rz: { widget: 'TeleopRz', step_deg: stepDeg },
        pose3d: {
            widget: 'TeleopPose3d',
            step_mm: stepMm,
            step_deg: stepDeg,
            step_z_mm: legacy.step_z_mm || [1.0, 5.0, 20.0],
        },
    };
}

function normalizeTeleopBlock(teleop) {
    if (!teleop || typeof teleop !== 'object') return {};
    const keys = Object.keys(teleop);
    if (!keys.length) return {};
    if (keys.every((k) => teleop[k] && typeof teleop[k] === 'object' && teleop[k].widget)) {
        return migrateLegacyPoseChannel(teleop);
    }
    if (typeof teleop.widget === 'string') {
        return migrateLegacyPoseChannel({ pose: { ...teleop } });
    }
    return teleop;
}

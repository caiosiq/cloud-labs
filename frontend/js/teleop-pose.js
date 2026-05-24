/**
 * TeleOp pose quantities — mirrors backend naming.
 *
 *   current_pose  — high-rate hardware truth (`GET …/telemetry/live-pose`)
 *                   stored in ``store.teleopLivePose[tagId]``
 *   target_pose   — operator plan sent with ``TELEOP_GOTO``
 *                   stored in ``store.teleopTarget[tagId]``
 *
 * Pose fields: ``x``, ``y``, ``z`` (mm), ``rotation`` (deg).
 */
import { store } from './state/store.js';
import { drawPose, getHolding, isHeldTag, measPose } from './component-model.js';

/** @typedef {'x' | 'y' | 'z' | 'rotation'} TeleopPoseField */

export const DEFAULT_HOVER_Z_MM = 40.0;

/** @param {object | null | undefined} pose */
export function normalizeTeleopPose(pose) {
    return {
        x: Number(pose?.x) || 0,
        y: Number(pose?.y) || 0,
        z: Number(pose?.z ?? DEFAULT_HOVER_Z_MM),
        rotation: Number(pose?.rotation) || 0,
    };
}

/**
 * @param {object | null | undefined} pose
 * @param {TeleopPoseField} field
 * @returns {number | null}
 */
export function readTeleopPoseField(pose, field) {
    if (!pose || typeof pose !== 'object') return null;
    const n = Number(pose[field]);
    return Number.isFinite(n) ? n : null;
}

/** Hardware-reported pose (live_pose poll). */
export function getTeleopCurrentPose(tagId) {
    const pose = store.teleopLivePose[tagId];
    return pose && typeof pose === 'object' ? pose : null;
}

/** Client-side planned goto pose (target_pose). */
export function getTeleopTargetPose(tagId) {
    const pose = store.teleopTarget[tagId];
    return pose && typeof pose === 'object' ? pose : null;
}

function hasTablePosition(pose) {
    return Number.isFinite(Number(pose?.x)) && Number.isFinite(Number(pose?.y));
}

/**
 * Best pose to seed ``target_pose`` — mirrors backend ``_initial_live_pose``.
 * Prefers live poll when xy are known; otherwise nominal / measurable layout.
 * @param {string} tagId
 * @param {object | null | undefined} [labState]
 */
export function resolveTeleopPlanSeedPose(tagId, labState = store.labState) {
    const current = getTeleopCurrentPose(tagId);
    if (current && hasTablePosition(current)) {
        return normalizeTeleopPose(current);
    }

    let layout = {};
    if (isHeldTag(tagId, labState)) {
        layout = getHolding(labState).nominal_pose || {};
    } else {
        const comp = labState?.components?.[tagId];
        if (comp) {
            const mp = measPose(comp);
            if (mp.x != null || mp.y != null) {
                layout = mp;
            } else {
                layout = drawPose(comp);
            }
        } else if (store.ghostState[tagId]) {
            layout = store.ghostState[tagId];
        }
    }

    const merged = normalizeTeleopPose(layout);
    if (current) {
        if (Number.isFinite(Number(current.rotation))) {
            merged.rotation = Number(current.rotation);
        }
        if (Number.isFinite(Number(current.z))) {
            merged.z = Number(current.z);
        }
    }
    return merged;
}

function isAccidentalEmptySeed(pose) {
    const p = normalizeTeleopPose(pose);
    return p.x === 0 && p.y === 0 && p.rotation === 0 && p.z === DEFAULT_HOVER_Z_MM;
}

/**
 * Fix targets that were seeded with ``{ rotation: 0 }`` before live poll / nominal lookup.
 * Safe to call on every live-pose tick.
 */
export function maybeRepairTeleopTargetSeed(tagId, labState = store.labState) {
    const target = getTeleopTargetPose(tagId);
    if (!target || !isAccidentalEmptySeed(target)) return target;
    const seed = resolveTeleopPlanSeedPose(tagId, labState);
    if (isAccidentalEmptySeed(seed)) return target;
    return seedTeleopTargetPose(tagId, seed);
}

/** @param {string} tagId @param {object} pose */
export function seedTeleopTargetPose(tagId, pose) {
    if (!tagId || !pose) return null;
    store.teleopTarget[tagId] = normalizeTeleopPose(pose);
    notifyTeleopPoseLayers(tagId);
    return store.teleopTarget[tagId];
}

/**
 * Ensure ``target_pose`` exists; seed from live / nominal layout.
 * @param {string} tagId
 * @param {object | null | undefined} [labState]
 */
export function ensureTeleopTargetPose(tagId, labState = store.labState) {
    maybeRepairTeleopTargetSeed(tagId, labState);
    const existing = getTeleopTargetPose(tagId);
    if (existing) return existing;
    return seedTeleopTargetPose(tagId, resolveTeleopPlanSeedPose(tagId, labState));
}

/**
 * Nudge one ``target_pose`` field and refresh canvas TARGET layer.
 * @returns {number} updated field value
 */
export function nudgeTeleopTargetField(tagId, field, delta, labState = store.labState) {
    ensureTeleopTargetPose(tagId, labState);
    const target = store.teleopTarget[tagId];
    target[field] = Number(target[field] || 0) + Number(delta);
    notifyTeleopPoseLayers(tagId);
    return target[field];
}

/** Repaint canvas LIVE/TARGET overlays after target edits. */
export function notifyTeleopPoseLayers(tagId) {
    if (typeof store._teleopLivePoseRender === 'function') {
        store._teleopLivePoseRender(tagId);
    }
}

// Back-compat aliases (prefer current/target names in new code).
export const getTeleopLivePose = getTeleopCurrentPose;
export const getTeleopTarget = getTeleopTargetPose;
export const seedTeleopTarget = seedTeleopTargetPose;

/**
 * TeleOp goto command helpers (target_pose + speed for TELEOP_GOTO).
 *
 * Pose read/write accessors live in ``teleop-pose.js`` (current vs target).
 */
import { store } from './state/store.js';
import { isBreadboardIntent, isHeldTag } from './component-model.js';
import { getTelemetry } from './component-state.js';
import {
    clearTeleopTargetAwaitingLive,
    getTeleopCurrentPose,
    getTeleopTargetPose,
    markTeleopTargetAwaitingLive,
    seedTeleopTargetPose,
    canSeedTargetFromCurrent,
} from './teleop-pose.js';

export {
    getTeleopCurrentPose,
    getTeleopCurrentPose as getTeleopLivePose,
    getTeleopTargetPose,
    getTeleopTargetPose as getTeleopTarget,
    seedTeleopTargetPose,
    seedTeleopTargetPose as seedTeleopTarget,
    ensureTeleopTargetPose,
    nudgeTeleopTargetField,
    readTeleopPoseField,
    notifyTeleopPoseLayers,
    resolveTeleopPlanSeedPose,
    maybeRepairTeleopTargetSeed,
    maybeSyncTeleopTargetFromFirstLivePose,
    markTeleopTargetAwaitingLive,
    clearTeleopTargetAwaitingLive,
    canSeedTargetFromCurrent,
    normalizeTeleopPose,
    DEFAULT_HOVER_Z_MM,
} from './teleop-pose.js';

/** ~10% of legacy bench max for safer first TeleOp moves. */
export const DEFAULT_TELEOP_SPEED = { linear_mm_s: 2.5, angular_deg_s: 1.5 };

export function getTeleopSpeed(tagId) {
    return store.teleopSpeed[tagId] || { ...DEFAULT_TELEOP_SPEED };
}

export function setTeleopSpeed(tagId, speed) {
    store.teleopSpeed[tagId] = {
        linear_mm_s: Number(speed?.linear_mm_s) || DEFAULT_TELEOP_SPEED.linear_mm_s,
        angular_deg_s: Number(speed?.angular_deg_s) || DEFAULT_TELEOP_SPEED.angular_deg_s,
    };
}

export function buildTeleopGotoPayload(tagId, comp, labState, target) {
    const mode = getTelemetry(comp).teleop?.mode;
    const held = isHeldTag(tagId, labState);
    const speed = getTeleopSpeed(tagId);
    if (mode === 'rz' || (!held && comp && isBreadboardIntent(comp))) {
        return {
            target_pose: { rotation: Number(target.rotation) || 0 },
            speed,
        };
    }
    return {
        target_pose: {
            x: Number(target.x) || 0,
            y: Number(target.y) || 0,
            z: Number(target.z ?? 40),
            rotation: Number(target.rotation) || 0,
        },
        speed,
    };
}

/** Seed target from live current when starting to plan. */
export function syncTeleopTargetFromCurrent(tagId) {
    markTeleopTargetAwaitingLive(tagId);
    const current = getTeleopCurrentPose(tagId);
    if (current && canSeedTargetFromCurrent(current)) {
        clearTeleopTargetAwaitingLive(tagId);
        return seedTeleopTargetPose(tagId, current);
    }
    return getTeleopTargetPose(tagId);
}

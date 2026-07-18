/**
 * Primitive UI catalog — one renderer per primitive id (D5).
 *
 * Tunables/measurables/telemetry stay read-only in component-viewer;
 * every write path is a named primitive form here.
 */
import { renderSetExposure } from './set-exposure.js';
import { renderSetMotorSetpoint } from './set-motor-setpoint.js';
import { renderMoveMotorJog } from './move-motor-jog.js';
import { renderMotorSendHome, renderMotorSetZero } from './motor-home-zero.js';
import { renderRecordMeasurables } from './record-measurables.js';
import { renderMoveComponent } from './move-component.js';
import { renderStartTeleop, renderEndTeleop } from './teleop.js';
import { renderStartLiveFeed, renderEndLiveFeed } from './live-feed.js';
import {
    renderConfirmHolding,
    renderHover,
    renderPickComponent,
    renderPlaceFromHover,
    renderHoldingNotice,
} from './in-air.js';
import { renderStoreComponent, renderPlaceFromStorage } from './storage.js';
import { renderOptimize } from './optimize.js';
import { renderScanRotate } from './scan-rotate.js';
import { getWidget } from '../widgets/index.js';
import { isLiveFeedActive, isTeleopActive, normalizeCapabilities } from '../component-state.js';
import { isHeldTag, isStoredComponent } from '../component-model.js';
import { store } from '../state/store.js';
import { primitiveRegion, sessionHint } from './shared.js';

const PRIMITIVE_UI = {
    SET_EXPOSURE: renderSetExposure,
    SET_MOTOR_SETPOINT: renderSetMotorSetpoint,
    MOVE_MOTOR: renderMoveMotorJog,
    MOTOR_SEND_HOME: renderMotorSendHome,
    MOTOR_SET_ZERO: renderMotorSetZero,
    RECORD_MEASURABLES: renderRecordMeasurables,
    MOVE_COMPONENT: renderMoveComponent,
    START_TELEOP: renderStartTeleop,
    END_TELEOP: renderEndTeleop,
    START_LIVE_FEED: renderStartLiveFeed,
    END_LIVE_FEED: renderEndLiveFeed,
    PICK_COMPONENT: renderPickComponent,
    HOVER: renderHover,
    PLACE_FROM_HOVER: renderPlaceFromHover,
    CONFIRM_HOLDING_TAG: renderConfirmHolding,
    STORE_COMPONENT: renderStoreComponent,
    PLACE_FROM_STORAGE: renderPlaceFromStorage,
    OPTIMIZE: renderOptimize,
    SCAN_ROTATE_IN_PLACE: (ctx) => renderScanRotate(ctx, { contextHint: 'placed' }),
};

/** Primitives that commit tunable / layout intent — blocked while TeleOp is active. */
const TUNABLE_WRITER_PRIMITIVES = new Set([
    'MOVE_COMPONENT',
    'SET_EXPOSURE',
    'SET_MOTOR_SETPOINT',
    'MOVE_MOTOR',
    'MOTOR_SEND_HOME',
    'MOTOR_SET_ZERO',
    'STORE_COMPONENT',
    'PLACE_FROM_STORAGE',
    'PICK_COMPONENT',
    'HOVER',
    'PLACE_FROM_HOVER',
    'CONFIRM_HOLDING_TAG',
    'OPTIMIZE',
    'SCAN_ROTATE_IN_PLACE',
]);

/** Blocked while live feed is streaming (use the feed instead of one-shot capture). */
const MEASURABLE_WRITER_PRIMITIVES = new Set(['RECORD_MEASURABLES']);

/** Primitives with no dedicated form yet (read-only GET_* only). */
const SILENT_PRIMITIVES = new Set([
    'GET_TUNABLES',
    'GET_MEASURABLES',
    'APPLY_TUNABLES_PATCH',
    'AFFIRM_PLACED_AT_CURRENT',
    'REPACK_STORAGE',
    'RECENTER_IN_STORAGE',
    'SCAN',
    'REMOVE',
]);

/** Telemetry session controls render last. */
const TELEMETRY_BOTTOM_PRIMITIVES = [
    'START_TELEOP',
    'END_TELEOP',
    'TELEOP_GOTO',
    'START_LIVE_FEED',
    'END_LIVE_FEED',
];

/** UI display order — tunable writers sit directly under MOVE_COMPONENT. */
const PRIMITIVE_DISPLAY_ORDER = [
    'MOVE_COMPONENT',
    'SET_EXPOSURE',
    'SET_MOTOR_SETPOINT',
    'MOVE_MOTOR',
    'MOTOR_SEND_HOME',
    'MOTOR_SET_ZERO',
    'STORE_COMPONENT',
    'PLACE_FROM_STORAGE',
    'PICK_COMPONENT',
    'HOVER',
    'PLACE_FROM_HOVER',
    'CONFIRM_HOLDING_TAG',
    'RECORD_MEASURABLES',
    'OPTIMIZE',
    'SCAN_ROTATE_IN_PLACE',
    ...TELEMETRY_BOTTOM_PRIMITIVES,
];

/** When a part is in inventory Q3, only these primitive forms are shown. */
const STORED_COMPONENT_PRIMITIVES = new Set(['PLACE_FROM_STORAGE']);

function sortPrimitivesForDisplay(allowList) {
    const rank = new Map(PRIMITIVE_DISPLAY_ORDER.map((p, i) => [p, i]));
    const seen = new Set();
    const present = [];

    (Array.isArray(allowList) ? allowList : []).forEach((prim) => {
        if (!prim || seen.has(prim)) return;
        seen.add(prim);
        present.push(prim);
    });

    present.sort((a, b) => {
        const ra = rank.has(a) ? rank.get(a) : PRIMITIVE_DISPLAY_ORDER.length;
        const rb = rank.has(b) ? rank.get(b) : PRIMITIVE_DISPLAY_ORDER.length;
        return ra - rb;
    });

    return present;
}

/**
 * @param {string} tagId
 * @param {string[]} allowList
 * @param {object} ctx
 * @returns {HTMLElement}
 */
export function renderPrimitiveRegions(tagId, allowList, ctx) {
    const root = document.createElement('div');
    root.className = 'primitive-regions';
    root.style.display = 'flex';
    root.style.flexDirection = 'column';
    root.style.gap = '4px';

    const list = sortPrimitivesForDisplay(
        ctx.placementState === 'STORED' || isStoredComponent(ctx.comp)
            ? (Array.isArray(allowList) ? allowList : []).filter((p) =>
                  STORED_COMPONENT_PRIMITIVES.has(p),
              )
            : allowList,
    );
    const seen = new Set();
    let count = 0;

    const liveFeedChannel =
        Object.keys(normalizeCapabilities(ctx.catalogRow?.capabilities).telemetry?.live_feed || {})[0]
        || 'stream';
    ctx.liveFeedChannel = liveFeedChannel;

    const teleopActive = isTeleopActive(ctx.comp);
    const liveFeedActive = isLiveFeedActive(ctx.comp, liveFeedChannel);
    const storedOnly =
        ctx.placementState === 'STORED' || isStoredComponent(ctx.comp);

    if (!storedOnly && teleopActive) {
        root.appendChild(sessionHint('Tunable writes hidden while TeleOp is active.'));
    }
    if (!storedOnly && liveFeedActive) {
        root.appendChild(sessionHint('Record measurables hidden while live feed is on.'));
    }

    list.forEach((prim) => {
        if (!prim || seen.has(prim) || SILENT_PRIMITIVES.has(prim)) return;
        seen.add(prim);

        if (teleopActive && TUNABLE_WRITER_PRIMITIVES.has(prim)) return;
        if (liveFeedActive && MEASURABLE_WRITER_PRIMITIVES.has(prim)) return;

        if (prim === 'START_TELEOP' && isTeleopActive(ctx.comp)) return;
        if (prim === 'END_TELEOP' && !isTeleopActive(ctx.comp)) return;
        if (prim === 'START_LIVE_FEED' && isLiveFeedActive(ctx.comp, liveFeedChannel)) return;
        if (prim === 'END_LIVE_FEED' && !isLiveFeedActive(ctx.comp, liveFeedChannel)) return;

        if (prim === 'TELEOP_GOTO') {
            const comp = ctx.comp;
            const caps = normalizeCapabilities(ctx.catalogRow?.capabilities);
            const labState = ctx.labState || store.labState;
            const held = isHeldTag(tagId, labState);
            const teleopCaps = caps.telemetry?.teleop || {};
            const channel = held ? 'pose3d' : 'rz';
            let desc = teleopCaps[channel];
            let widgetName = desc && desc.widget;
            if (!desc && teleopCaps.pose) {
                desc = teleopCaps.pose;
                widgetName = held ? 'TeleopPose3d' : 'TeleopRz';
            }
            if (desc && isTeleopActive(comp)) {
                widgetName = widgetName || (held ? 'TeleopPose3d' : 'TeleopRz');
                const widget = getWidget(widgetName);
                try {
                    const { section, body } = primitiveRegion('TELEOP_GOTO', held ? 'TELEOP (held)' : 'TELEOP (Rz)', {
                        accent: 'teleop',
                        active: true,
                    });
                    const card = widget({
                        tagId,
                        descriptor: desc,
                        comp,
                        hooks: ctx.hooks,
                        labState,
                        showSessionToggle: false,
                        embedded: true,
                    });
                    if (card instanceof HTMLElement) {
                        body.appendChild(card);
                        root.appendChild(section);
                        count += 1;
                    }
                } catch (e) {
                    console.error(`[primitives] ${widgetName} widget failed:`, e);
                }
            }
            return;
        }

        const renderFn = PRIMITIVE_UI[prim];
        if (!renderFn) return;
        try {
            const el = renderFn(ctx);
            if (prim === 'PICK_COMPONENT' || prim === 'HOVER' || prim === 'PLACE_FROM_HOVER') {
                // holding notice once
            }
            if (el instanceof HTMLElement) {
                root.appendChild(el);
                count += 1;
            }
        } catch (e) {
            console.error(`[primitives] ${prim} UI failed:`, e);
        }
    });

    const notice = renderHoldingNotice(ctx);
    if (notice instanceof HTMLElement) root.prepend(notice);

    if (!count && !notice) {
        const empty = document.createElement('div');
        empty.style.fontStyle = 'italic';
        empty.style.color = '#475569';
        empty.style.fontSize = '10px';
        empty.textContent = '\u2014 no primitive actions for this component';
        root.appendChild(empty);
    }

    return root;
}


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
import { renderStartTeleop, renderEndTeleop, renderTeleopJog } from './teleop.js';
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
    TELEOP_JOG: renderTeleopJog,
    PICK_COMPONENT: renderPickComponent,
    HOVER: renderHover,
    PLACE_FROM_HOVER: renderPlaceFromHover,
    CONFIRM_HOLDING_TAG: renderConfirmHolding,
    STORE_COMPONENT: renderStoreComponent,
    PLACE_FROM_STORAGE: renderPlaceFromStorage,
    OPTIMIZE: renderOptimize,
    SCAN_ROTATE_IN_PLACE: (ctx) => renderScanRotate(ctx, { contextHint: 'placed' }),
};

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

    const list = Array.isArray(allowList) ? allowList : [];
    const seen = new Set();
    let count = 0;

    list.forEach((prim) => {
        if (!prim || seen.has(prim) || SILENT_PRIMITIVES.has(prim)) return;
        seen.add(prim);

        if (prim === 'TELEOP_JOG') {
            const comp = ctx.comp;
            const tun = comp?.tunables || {};
            const desc = ctx.catalogRow?.capabilities?.tunables?.teleop;
            if (desc && tun.teleop_active) {
                const widget = getWidget('TeleopJog');
                try {
                    const card = widget({
                        tagId,
                        fieldName: 'teleop',
                        descriptor: desc,
                        value: tun.teleop,
                        scope: 'tunables',
                        comp,
                        hooks: ctx.hooks,
                    });
                    if (card instanceof HTMLElement) {
                        const wrap = document.createElement('div');
                        wrap.className = 'prim-region';
                        wrap.dataset.primitive = 'TELEOP_JOG';
                        wrap.appendChild(card);
                        root.appendChild(wrap);
                        count += 1;
                    }
                } catch (e) {
                    console.error('[primitives] TeleopJog widget failed:', e);
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


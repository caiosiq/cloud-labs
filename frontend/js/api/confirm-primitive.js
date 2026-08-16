/**
 * Operator confirm gate for Twin primitive actions.
 *
 * Interactive UI paths should confirm before mutating the lab. Programmatic
 * paths (OPTIMIZE loops, layout resolve, unload beacon) call execute* APIs
 * directly or pass ``skipConfirm``.
 */
import { showConfirmationModal } from '../ui/modals.js';

/** High-frequency / continuous frames — never modal. */
export const PRIMITIVE_CONFIRM_SKIP = new Set([
    'TELEOP_JOG',
    'TELEOP_GOTO',
]);

const ACTION_TITLES = {
    MOVE_COMPONENT: 'Move component',
    STORE_COMPONENT: 'Store component',
    PLACE_FROM_STORAGE: 'Place from storage',
    PICK_COMPONENT: 'Pick component',
    HOVER: 'Hover component',
    PLACE_FROM_HOVER: 'Place from hover',
    CONFIRM_HOLDING_TAG: 'Confirm holding tag',
    SET_EXPOSURE: 'Set science exposure',
    SET_LIVE_EXPOSURE: 'Set live preview exposure',
    SET_LASER_OUTPUT: 'Set laser output',
    SET_MOTOR_SETPOINT: 'Set motor setpoint',
    MOVE_MOTOR: 'Move motor',
    MOTOR_SEND_HOME: 'Send motor home',
    MOTOR_SET_ZERO: 'Set motor zero',
    RECORD_MEASURABLES: 'Record measurables',
    EVAL_KERNEL: 'Eval kernel',
    OPTIMIZE: 'Start optimize',
    START_TELEOP: 'Start teleop',
    END_TELEOP: 'End teleop',
    START_LIVE_FEED: 'Start live feed',
    END_LIVE_FEED: 'End live feed',
    AFFIRM_PLACED_AT_CURRENT: 'Affirm placed',
    REPACK_STORAGE: 'Repack storage',
    RECENTER_IN_STORAGE: 'Recenter in storage',
    REMOVE: 'Remove component',
    RECORD_TUNABLES: 'Record tunables',
    SYNC_RUNTIME: 'Sync runtime',
    APPLY_TUNABLES_PATCH: 'Apply tunables patch',
};

function _titleForAction(action) {
    const key = String(action || '').trim();
    if (!key) return 'Run action';
    if (ACTION_TITLES[key]) return ACTION_TITLES[key];
    return key
        .split('_')
        .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
        .join(' ');
}

function _escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Build HTML body for a command confirm dialog.
 * @param {{ action?: string, target_id?: string, parameters?: object, channel?: string }} command
 */
export function formatPrimitiveConfirmMessage(command) {
    const action = String(command?.action || '').trim();
    const title = _titleForAction(action);
    const tag = command?.target_id ? String(command.target_id) : '';
    const params = command?.parameters && typeof command.parameters === 'object'
        ? command.parameters
        : {};

    let msg = `Run <strong>${_escapeHtml(title)}</strong>`;
    if (tag) msg += ` on <strong>${_escapeHtml(tag)}</strong>`;
    msg += '?';

    if (action === 'MOVE_COMPONENT' && params) {
        const x = Number(params.target_x);
        const y = Number(params.target_y);
        const r = Number(params.rotation);
        if (Number.isFinite(x) && Number.isFinite(y)) {
            msg += `<br><br>X: ${x.toFixed(1)} mm<br>Y: ${y.toFixed(1)} mm`;
            if (Number.isFinite(r)) msg += `<br>Rot: ${r.toFixed(1)}°`;
        }
    } else if (action === 'SET_EXPOSURE' || action === 'SET_LIVE_EXPOSURE') {
        const exp = Number(params.exposure_time_ms);
        if (Number.isFinite(exp)) msg += `<br><br>Exposure: <strong>${exp} ms</strong>`;
    } else if (action === 'SET_LASER_OUTPUT') {
        const p = Number(params.output_power_mw);
        if (Number.isFinite(p)) msg += `<br><br>Power: <strong>${p} mW</strong>`;
    } else if (action === 'SET_MOTOR_SETPOINT' || action === 'MOVE_MOTOR') {
        const mid = params.motor_id;
        const ang = Number(params.angle_deg);
        if (mid !== undefined && mid !== null) {
            msg += `<br><br>Motor: <strong>${_escapeHtml(mid)}</strong>`;
        }
        if (Number.isFinite(ang)) msg += `<br>Angle: <strong>${ang.toFixed(1)}°</strong>`;
    } else if (action === 'START_LIVE_FEED') {
        const ch = command.channel || params.channel || 'stream';
        const exp = Number(params.exposure_time_ms);
        msg += `<br><br>Channel: <strong>${_escapeHtml(ch)}</strong>`;
        if (Number.isFinite(exp) && exp > 0) {
            msg += `<br>Preview exposure: <strong>${exp} ms</strong>`;
        }
    }

    msg += `<br><br><span style="color:#64748b;font-size:12px">Confirm to send this command to the lab.</span>`;
    return msg;
}

/**
 * @param {string} messageHtml
 * @param {{ confirmLabel?: string, cancelLabel?: string }} [options]
 * @returns {Promise<boolean>} true if confirmed
 */
export function confirmPrimitiveMessage(messageHtml, options = {}) {
    return new Promise((resolve) => {
        showConfirmationModal(
            messageHtml,
            () => resolve(true),
            () => resolve(false),
            {
                confirmLabel: options.confirmLabel || 'Confirm',
                cancelLabel: options.cancelLabel || 'Cancel',
            },
        );
    });
}

/**
 * @param {{ action?: string, target_id?: string, parameters?: object, channel?: string }} command
 * @param {{ confirmLabel?: string }} [options]
 * @returns {Promise<boolean>}
 */
export function confirmPrimitiveCommand(command, options = {}) {
    return confirmPrimitiveMessage(formatPrimitiveConfirmMessage(command), options);
}

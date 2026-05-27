/**
 * Motor command feedback — spinner / checkmark for setpoint apply and relative jogs.
 *
 * Uses `dispatchPrimitive` with `refreshPanel: false` so optimistic UI survives
 * BUSY polls (see `primitives/shared.js`).
 */
import { dispatchPrimitive } from '../primitives/shared.js';
import { store } from '../state/store.js';
import { tunableValue } from '../component-state.js';

export const MOTOR_ACTION = {
    SETPOINT: 'setpoint',
    JOG: 'jog',
};

export function motorActionKey(kind, tagId, motorId) {
    return `${kind}:${tagId}:${motorId}`;
}

export function getMotorAction(kind, tagId, motorId) {
    return store.motorActionApply[motorActionKey(kind, tagId, motorId)] || null;
}

function ensureSpinStyle() {
    if (document.getElementById('motor-action-spin-style')) return;
    const style = document.createElement('style');
    style.id = 'motor-action-spin-style';
    style.textContent = `
        @keyframes motor-action-spin { to { transform: rotate(360deg); } }
        .motor-action-status {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            flex: 0 0 28px;
            margin-left: 2px;
            border-radius: 6px;
            background: #1e293b;
            border: 1px solid #475569;
            box-sizing: border-box;
        }
        .motor-action-status[data-status="idle"] {
            border-color: #334155;
            background: #0f1115;
        }
        .motor-action-status[data-status="loading"] {
            border-color: #fbbf24;
            background: #422006;
        }
        .motor-action-status[data-status="done"] {
            border-color: #22c55e;
            background: #052e16;
        }
        .motor-action-status[data-status="error"] {
            border-color: #f87171;
            background: #450a0a;
        }
        .motor-action-spinner {
            width: 18px;
            height: 18px;
            border: 3px solid #64748b;
            border-top-color: #fbbf24;
            border-radius: 50%;
            animation: motor-action-spin 0.7s linear infinite;
        }
        .motor-action-check {
            color: #4ade80;
            font-size: 20px;
            font-weight: 700;
            line-height: 1;
        }
        .motor-action-error {
            color: #fca5a5;
            font-size: 18px;
            font-weight: 700;
            line-height: 1;
        }
    `;
    document.head.appendChild(style);
}

function statusMarkup(status) {
    ensureSpinStyle();
    if (status === 'loading') {
        return '<div class="motor-action-spinner" role="status" aria-label="Moving"></div>';
    }
    if (status === 'done') {
        return '<span class="motor-action-check" aria-label="Complete" title="Complete">✓</span>';
    }
    if (status === 'error') {
        return '<span class="motor-action-error" aria-label="Failed" title="Failed">!</span>';
    }
    return '';
}

function doneTitle(kind) {
    return kind === MOTOR_ACTION.SETPOINT ? 'Setpoint applied' : 'Jog complete';
}

function loadingTitle(kind) {
    return kind === MOTOR_ACTION.SETPOINT ? 'Applying setpoint…' : 'Motor jog in progress…';
}

export function createMotorActionStatusEl(kind, tagId, motorId) {
    ensureSpinStyle();
    const el = document.createElement('span');
    el.className = 'motor-action-status';
    el.dataset.motorActionStatus = motorActionKey(kind, tagId, motorId);
    el.setAttribute('aria-live', 'polite');
    paintMotorActionStatus(kind, tagId, motorId);
    return el;
}

export function paintMotorActionStatus(kind, tagId, motorId) {
    ensureSpinStyle();
    const key = motorActionKey(kind, tagId, motorId);
    const rec = store.motorActionApply[key];
    const el = document.querySelector(`[data-motor-action-status="${key}"]`);
    if (!el) return;
    if (rec) {
        el.dataset.status = rec.status;
        el.innerHTML = statusMarkup(rec.status);
        el.title = rec.status === 'loading'
            ? loadingTitle(kind)
            : rec.status === 'done'
                ? doneTitle(kind)
                : rec.status === 'error'
                    ? 'Motor command failed'
                    : '';
    } else {
        el.dataset.status = 'idle';
        el.innerHTML = '';
        el.title = '';
    }
}

function beginMotorAction(kind, tagId, motorId, extra = {}) {
    const key = motorActionKey(kind, tagId, motorId);
    store.motorActionApply[key] = { status: 'loading', ...extra };
    paintMotorActionStatus(kind, tagId, motorId);
    requestAnimationFrame(() => paintMotorActionStatus(kind, tagId, motorId));
}

function failMotorAction(kind, tagId, motorId) {
    const key = motorActionKey(kind, tagId, motorId);
    const rec = store.motorActionApply[key];
    if (!rec) return;
    rec.status = 'error';
    paintMotorActionStatus(kind, tagId, motorId);
}

export function resolveMotorSetpointInputValue(tagId, motorId, nominalValue) {
    const rec = getMotorAction(MOTOR_ACTION.SETPOINT, tagId, motorId);
    if (rec && (rec.status === 'loading' || rec.status === 'done')) {
        return rec.value;
    }
    return Number.isFinite(Number(nominalValue)) ? Number(nominalValue) : 0;
}

function parseActionKey(key) {
    const parts = key.split(':');
    if (parts.length < 3) return null;
    const kind = parts[0];
    const motorId = parts[parts.length - 1];
    const tagId = parts.slice(1, -1).join(':');
    return { kind, tagId, motorId: Number(motorId) };
}

function isLabSettled() {
    const labStatus = store.labState?.system_status || 'IDLE';
    return labStatus !== 'BUSY' && labStatus !== 'TELEOP';
}

function scheduleDoneClear(key, kind, tagId, motorId) {
    window.setTimeout(() => {
        const cur = store.motorActionApply[key];
        if (cur?.status === 'done') {
            delete store.motorActionApply[key];
            paintMotorActionStatus(kind, tagId, motorId);
        }
    }, 2500);
}

/** Mark in-flight motor actions complete once the lab settles. */
export function syncMotorActionStatuses() {
    if (!store.labState?.components) return;

    let changed = false;
    Object.entries(store.motorActionApply).forEach(([key, rec]) => {
        if (rec.status !== 'loading') return;

        const parsed = parseActionKey(key);
        if (!parsed) return;
        const { kind, tagId, motorId } = parsed;

        if (store.pendingCommands.has(tagId)) return;
        if (!isLabSettled()) return;

        if (kind === MOTOR_ACTION.SETPOINT) {
            const comp = store.labState.components[tagId];
            if (!comp) return;
            const nom = tunableValue(comp, 'nominal_motor_positions')?.[String(motorId)];
            if (!Number.isFinite(Number(nom)) || Number(nom) !== Number(rec.value)) return;
        }

        rec.status = 'done';
        changed = true;
        scheduleDoneClear(key, kind, tagId, motorId);
    });

    if (changed) {
        Object.keys(store.motorActionApply).forEach((key) => {
            const parsed = parseActionKey(key);
            if (!parsed) return;
            paintMotorActionStatus(parsed.kind, parsed.tagId, parsed.motorId);
        });
    }
}

export async function dispatchMotorCommand(hooks, command, kind, tagId, motorId, beginExtra = {}) {
    beginMotorAction(kind, tagId, motorId, beginExtra);
    const result = await dispatchPrimitive(hooks, command, {
        refreshPanel: false,
        fetchLabState: true,
        onError: () => failMotorAction(kind, tagId, motorId),
    });
    if (!result?.ok && hooks.log) {
        hooks.log(`${command.action} failed: ${result?.error || 'unknown'}`, 'warn');
    }
    if (result?.ok) {
        if (store.pendingCommands.has(tagId)) {
            store.pendingCommands.delete(tagId);
            store.pendingActions.delete(tagId);
        }
        syncMotorActionStatuses();
    }
    return result;
}

export async function dispatchMotorJog(hooks, tagId, motorId, distance) {
    return dispatchMotorCommand(
        hooks,
        {
            action: 'MOVE_MOTOR',
            target_id: tagId,
            parameters: { motor_id: Number(motorId), distance },
        },
        MOTOR_ACTION.JOG,
        tagId,
        motorId,
    );
}

export function updateMotorActionStatuses(tagId) {
    if (!tagId) return;
    document.querySelectorAll(`[data-motor-action-status*="${tagId}:"]`).forEach((el) => {
        const key = el.dataset.motorActionStatus || '';
        const parsed = parseActionKey(key);
        if (!parsed || parsed.tagId !== tagId) return;
        paintMotorActionStatus(parsed.kind, parsed.tagId, parsed.motorId);
    });
}

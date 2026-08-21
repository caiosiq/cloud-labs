/**
 * `NudgeMotorGroup` — tunable widget for ``nominal_motor_positions`` (§14.1).
 *
 * Renders one row per motor declared in ``descriptor.motor_ids`` with
 * ± buttons that dispatch ``MOVE_MOTOR`` at a user-editable step (°).
 */
import { widgetCard, widgetTitle } from './common.js';
import {
    createMotorActionStatusEl,
    dispatchMotorJog,
    MOTOR_ACTION,
} from '../ui/motor-action-ui.js';

const DEFAULT_JOG_STEP_DEG = 2.5;
const MIN_JOG_STEP_DEG = 0.01;

/** @type {Map<string, number>} */
const _jogStepByKey = new Map();

function _clampStep(raw, fallback = DEFAULT_JOG_STEP_DEG) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return fallback;
    return Math.max(MIN_JOG_STEP_DEG, n);
}

export default function NudgeMotorGroup({ tagId, fieldName, descriptor, comp, hooks }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const motorIds = Array.isArray(descriptor && descriptor.motor_ids)
        ? descriptor.motor_ids
        : [];
    const catalogDefault = Number.isFinite(Number(descriptor && descriptor.step_deg))
        ? Number(descriptor.step_deg)
        : DEFAULT_JOG_STEP_DEG;
    const key = `${tagId}:${fieldName}`;
    let step = _clampStep(_jogStepByKey.get(key) ?? catalogDefault, catalogDefault);
    _jogStepByKey.set(key, step);

    if (!motorIds.length) {
        const el = document.createElement('div');
        el.style.fontStyle = 'italic';
        el.style.color = '#475569';
        el.style.fontSize = '11px';
        el.textContent = '\u2014 no motor_ids declared in catalog';
        card.appendChild(el);
        return card;
    }

    const stepRow = document.createElement('div');
    stepRow.style.display = 'flex';
    stepRow.style.alignItems = 'center';
    stepRow.style.gap = '6px';
    stepRow.style.marginTop = '4px';
    const stepLabel = document.createElement('span');
    stepLabel.style.color = '#64748b';
    stepLabel.style.fontSize = '10px';
    stepLabel.textContent = 'step (°)';
    const stepInp = document.createElement('input');
    stepInp.type = 'number';
    stepInp.className = 'input';
    stepInp.min = String(MIN_JOG_STEP_DEG);
    stepInp.step = '0.1';
    stepInp.value = String(step);
    stepInp.style.width = '72px';
    stepInp.style.fontSize = '11px';
    stepInp.title = 'Jog step size in degrees (e.g. 0.1)';
    stepRow.appendChild(stepLabel);
    stepRow.appendChild(stepInp);
    card.appendChild(stepRow);

    /** @type {Array<{minus: HTMLButtonElement, plus: HTMLButtonElement, mid: string|number}>} */
    const jogButtons = [];

    const syncButtons = () => {
        jogButtons.forEach(({ minus, plus, mid }) => {
            minus.title = `MOVE_MOTOR ${tagId} motor=${mid} \u0394=-${step}\u00b0`;
            plus.title = `MOVE_MOTOR ${tagId} motor=${mid} \u0394=+${step}\u00b0`;
            minus.textContent = `\u2212${step}\u00b0`;
            plus.textContent = `+${step}\u00b0`;
            minus.onclick = () => {
                if (hooks) void dispatchMotorJog(hooks, tagId, mid, -step);
            };
            plus.onclick = () => {
                if (hooks) void dispatchMotorJog(hooks, tagId, mid, step);
            };
        });
    };

    const applyStep = (raw) => {
        step = _clampStep(raw, catalogDefault);
        stepInp.value = String(step);
        _jogStepByKey.set(key, step);
        syncButtons();
    };
    stepInp.addEventListener('change', () => applyStep(stepInp.value));
    stepInp.addEventListener('input', () => {
        const next = Number(stepInp.value);
        if (!Number.isFinite(next) || next <= 0) return;
        applyStep(next);
    });

    motorIds.forEach((mid) => {
        const r = document.createElement('div');
        r.style.display = 'grid';
        r.style.gridTemplateColumns = 'auto auto auto auto';
        r.style.gap = '6px';
        r.style.alignItems = 'center';
        r.style.marginTop = '4px';

        const label = document.createElement('span');
        label.style.color = '#64748b';
        label.style.fontSize = '10px';
        label.textContent = `M${mid}`;
        r.appendChild(label);

        const minus = document.createElement('button');
        minus.type = 'button';
        minus.className = 'btn btn-secondary';
        minus.style.fontSize = '10px';
        minus.style.padding = '2px 6px';
        r.appendChild(minus);

        const plus = document.createElement('button');
        plus.type = 'button';
        plus.className = 'btn btn-secondary';
        plus.style.fontSize = '10px';
        plus.style.padding = '2px 6px';
        r.appendChild(plus);

        jogButtons.push({ minus, plus, mid });

        const statusEl = createMotorActionStatusEl(MOTOR_ACTION.JOG, tagId, mid);
        r.appendChild(statusEl);

        card.appendChild(r);
    });

    syncButtons();
    return card;
}

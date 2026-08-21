import { coordInput, primitiveRegion, secondaryButton } from './shared.js';
import {
    createMotorActionStatusEl,
    dispatchMotorJog,
    MOTOR_ACTION,
} from '../ui/motor-action-ui.js';

const DEFAULT_JOG_STEP_DEG = 2.5;
const MIN_JOG_STEP_DEG = 0.01;

/** @type {Map<string, number>} */
const _jogStepByTag = new Map();

function _clampStep(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return DEFAULT_JOG_STEP_DEG;
    return Math.max(MIN_JOG_STEP_DEG, n);
}

export function renderMoveMotorJog(ctx) {
    const { tagId, catalogRow, hooks } = ctx;
    const motorIds = catalogRow?.motor_ids || [];
    if (!motorIds.length) return null;

    let step = _clampStep(_jogStepByTag.get(tagId) ?? DEFAULT_JOG_STEP_DEG);
    _jogStepByTag.set(tagId, step);

    const { section, body } = primitiveRegion('MOVE_MOTOR', 'MOVE MOTOR (relative jog)');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 4px 0';
    const syncHint = () => {
        hint.textContent =
            `Relative jog in ±${step}° steps; updates nominal_motor_positions to match.`;
    };
    syncHint();
    body.appendChild(hint);

    const stepInp = coordInput('step (°)', step);
    stepInp.input.min = String(MIN_JOG_STEP_DEG);
    stepInp.input.step = '0.1';
    stepInp.input.title = 'Jog step size in degrees (e.g. 0.1)';
    body.appendChild(stepInp);

    /** @type {Array<{minus: HTMLButtonElement, plus: HTMLButtonElement}>} */
    const jogButtons = [];

    const syncButtons = () => {
        jogButtons.forEach(({ minus, plus }) => {
            minus.title = `Jog −${step}°`;
            plus.title = `Jog +${step}°`;
            minus.onclick = () => void dispatchMotorJog(hooks, tagId, minus.dataset.motorId, -step);
            plus.onclick = () => void dispatchMotorJog(hooks, tagId, plus.dataset.motorId, step);
        });
        syncHint();
    };

    stepInp.input.addEventListener('change', () => {
        step = _clampStep(stepInp.value);
        stepInp.value = String(step);
        _jogStepByTag.set(tagId, step);
        syncButtons();
    });
    stepInp.input.addEventListener('input', () => {
        const next = Number(stepInp.value);
        if (!Number.isFinite(next) || next <= 0) return;
        step = _clampStep(next);
        _jogStepByTag.set(tagId, step);
        syncButtons();
    });

    motorIds.forEach((mid) => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.gap = '6px';
        row.style.flexWrap = 'wrap';

        const label = document.createElement('span');
        label.textContent = `M${mid}`;
        label.style.fontSize = '11px';
        label.style.minWidth = '28px';

        const bRev = secondaryButton('', 'remove');
        bRev.style.width = 'auto';
        bRev.style.padding = '6px 10px';
        bRev.dataset.motorId = String(mid);
        bRev.title = `Jog −${step}°`;
        bRev.onclick = () => void dispatchMotorJog(hooks, tagId, mid, -step);

        const bFwd = secondaryButton('', 'add');
        bFwd.style.width = 'auto';
        bFwd.style.padding = '6px 10px';
        bFwd.dataset.motorId = String(mid);
        bFwd.title = `Jog +${step}°`;
        bFwd.onclick = () => void dispatchMotorJog(hooks, tagId, mid, step);

        jogButtons.push({ minus: bRev, plus: bFwd });

        const statusEl = createMotorActionStatusEl(MOTOR_ACTION.JOG, tagId, mid);

        row.appendChild(label);
        row.appendChild(bRev);
        row.appendChild(bFwd);
        row.appendChild(statusEl);
        body.appendChild(row);
    });

    return section;
}

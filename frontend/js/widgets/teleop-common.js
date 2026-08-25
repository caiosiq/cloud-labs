/**
 * Shared TeleOp widget UI (status badge, axis nudge panels, loading state).
 */
import { fmtNum } from './common.js';
import { DEFAULT_TELEOP_SPEED, getTeleopSpeed, setTeleopSpeed } from '../teleop-target.js';
import {
    getTeleopCurrentPose,
    getTeleopTargetPose,
    nudgeTeleopTargetField,
    readTeleopPoseField,
} from '../teleop-pose.js';
export const DEFAULT_STEP_MM = [0.5, 2.0, 10.0];
export const DEFAULT_STEP_DEG = [0.5, 2.0, 10.0];
export const DEFAULT_STEP_Z_MM = [1.0, 5.0, 20.0];

export function statusBadge(label, tone) {
    const el = document.createElement('span');
    el.style.fontSize = 'var(--text-xs)';
    el.style.fontWeight = '600';
    el.style.padding = '3px 8px';
    el.style.borderRadius = '999px';
    el.style.fontFamily = 'ui-monospace, monospace';
    el.style.letterSpacing = '0.06em';
    el.textContent = label;
    if (tone === 'ready') {
        el.style.background = '#0e7490';
        el.style.color = '#cffafe';
        el.style.border = '1px solid #06b6d4';
    } else if (tone === 'loading') {
        el.style.background = '#422006';
        el.style.color = '#fde68a';
        el.style.border = '1px solid #ca8a04';
    } else {
        el.style.background = '#1e293b';
        el.style.color = '#64748b';
        el.style.border = '1px solid #334155';
    }
    return el;
}

function stepButton(label, delta, onNudge) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-secondary';
    b.style.fontSize = 'var(--text-xs)';
    b.style.padding = '4px 0';
    b.style.minWidth = '0';
    b.style.flex = '1 1 0';
    b.textContent = label;
    b.onclick = () => onNudge(delta);
    return b;
}

const TELEOP_CURRENT_COLOR = '#22d3ee';
const TELEOP_TARGET_COLOR = '#fbbf24';

function poseReadoutRow(label, color) {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'baseline';
    row.style.gap = '8px';
    row.style.marginBottom = '4px';

    const name = document.createElement('span');
    name.style.fontSize = 'var(--text-xs)';
    name.style.fontWeight = '600';
    name.style.letterSpacing = '0.06em';
    name.style.color = color;
    name.textContent = label;
    row.appendChild(name);

    const val = document.createElement('span');
    val.style.fontFamily = 'ui-monospace, monospace';
    val.style.fontSize = 'var(--text-sm)';
    val.style.color = '#e2e8f0';
    val.textContent = '\u2014';
    row.appendChild(val);

    return { row, val };
}

/**
 * Axis planner with separate CURRENT (live_pose) and TARGET (target_pose) readouts.
 * TARGET updates immediately on nudge; CURRENT follows the live poll buffer.
 *
 * @param {{ tagId: string, axisName: string, field: string, unit: string, steps: number[] }} opts
 */
export function createTeleopPlanAxisPanel({ tagId, axisName, field, unit, steps }) {
    const panel = document.createElement('div');
    panel.style.border = '1px solid #2a2e36';
    panel.style.borderRadius = '6px';
    panel.style.padding = '8px';
    panel.style.background = '#12151a';

    const title = document.createElement('div');
    title.style.color = '#94a3b8';
    title.style.fontSize = 'var(--text-xs)';
    title.style.fontWeight = '600';
    title.style.letterSpacing = '0.05em';
    title.style.marginBottom = '8px';
    title.textContent = axisName;
    panel.appendChild(title);

    const current = poseReadoutRow('CURRENT', TELEOP_CURRENT_COLOR);
    const target = poseReadoutRow('TARGET', TELEOP_TARGET_COLOR);
    panel.appendChild(current.row);
    panel.appendChild(target.row);

    function repaintCurrent() {
        const n = readTeleopPoseField(getTeleopCurrentPose(tagId), field);
        current.val.textContent = n == null ? '\u2014' : `${fmtNum(n, 2)} ${unit}`;
    }

    function repaintTarget() {
        const n = readTeleopPoseField(getTeleopTargetPose(tagId), field);
        target.val.textContent = `${fmtNum(n ?? 0, 2)} ${unit}`;
    }

    const onNudge = (delta) => {
        nudgeTeleopTargetField(tagId, field, delta);
        repaintTarget();
    };

    const negRow = document.createElement('div');
    negRow.style.display = 'flex';
    negRow.style.gap = '4px';
    negRow.style.marginTop = '6px';
    negRow.style.marginBottom = '4px';
    steps.slice().reverse().forEach((s) => {
        negRow.appendChild(stepButton(`−${s}`, -s, onNudge));
    });
    panel.appendChild(negRow);

    const posRow = document.createElement('div');
    posRow.style.display = 'flex';
    posRow.style.gap = '4px';
    steps.forEach((s) => {
        posRow.appendChild(stepButton(`+${s}`, s, onNudge));
    });
    panel.appendChild(posRow);

    repaintCurrent();
    repaintTarget();

    const timer = setInterval(() => {
        repaintCurrent();
        repaintTarget();
    }, 100);
    const obs = new MutationObserver(() => {
        if (!panel.isConnected) {
            clearInterval(timer);
            obs.disconnect();
        }
    });
    setTimeout(() => {
        if (panel.parentNode) obs.observe(panel.parentNode, { childList: true, subtree: true });
    }, 0);

    panel._repaintTarget = repaintTarget;
    panel._repaintCurrent = repaintCurrent;
    return panel;
}

export function axisPanel(labelText, currentValue, unit, steps, onNudge) {
    const panel = document.createElement('div');
    panel.style.border = '1px solid #2a2e36';
    panel.style.borderRadius = '6px';
    panel.style.padding = '8px';
    panel.style.background = '#12151a';

    const head = document.createElement('div');
    head.style.display = 'flex';
    head.style.justifyContent = 'space-between';
    head.style.alignItems = 'baseline';
    head.style.marginBottom = '8px';
    head.style.gap = '8px';

    const label = document.createElement('span');
    label.style.color = '#94a3b8';
    label.style.fontSize = 'var(--text-xs)';
    label.style.fontWeight = '600';
    label.style.letterSpacing = '0.05em';
    label.textContent = labelText;
    head.appendChild(label);

    const val = document.createElement('span');
    val.style.color = '#e2e8f0';
    val.style.fontFamily = 'ui-monospace, monospace';
    val.style.fontSize = 'var(--text-sm)';
    val.textContent = `${fmtNum(currentValue, 2)} ${unit}`;
    head.appendChild(val);
    panel.appendChild(head);

    const negRow = document.createElement('div');
    negRow.style.display = 'flex';
    negRow.style.gap = '4px';
    negRow.style.marginBottom = '4px';
    steps.slice().reverse().forEach((s) => {
        negRow.appendChild(stepButton(`−${s}`, -s, onNudge));
    });
    panel.appendChild(negRow);

    const posRow = document.createElement('div');
    posRow.style.display = 'flex';
    posRow.style.gap = '4px';
    steps.forEach((s) => {
        posRow.appendChild(stepButton(`+${s}`, s, onNudge));
    });
    panel.appendChild(posRow);

    return panel;
}

export function loadingPanel(lastError) {
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.alignItems = 'center';
    wrap.style.gap = '8px';
    wrap.style.padding = '16px 8px';
    wrap.style.border = '1px dashed #334155';
    wrap.style.borderRadius = '6px';
    wrap.style.background = '#0f1115';

    const spinner = document.createElement('div');
    spinner.style.width = '18px';
    spinner.style.height = '18px';
    spinner.style.border = '2px solid #334155';
    spinner.style.borderTopColor = '#22d3ee';
    spinner.style.borderRadius = '50%';
    spinner.style.animation = 'teleop-spin 0.8s linear infinite';
    wrap.appendChild(spinner);

    const msg = document.createElement('div');
    msg.style.fontSize = 'var(--text-sm)';
    msg.style.color = '#cbd5e1';
    msg.style.fontWeight = '500';
    msg.textContent = 'Loading TeleOp…';
    wrap.appendChild(msg);

    const hint = document.createElement('div');
    hint.style.fontSize = 'var(--text-xs)';
    hint.style.color = '#64748b';
    hint.style.fontStyle = 'italic';
    hint.style.textAlign = 'center';
    hint.style.lineHeight = '1.4';
    hint.textContent = 'Waiting for the lab to confirm live control is ready.';
    wrap.appendChild(hint);

    if (lastError) {
        const err = document.createElement('div');
        err.style.fontSize = 'var(--text-xs)';
        err.style.color = '#fca5a5';
        err.style.textAlign = 'center';
        err.textContent = String(lastError);
        wrap.appendChild(err);
    }

    if (!document.getElementById('teleop-spin-keyframes')) {
        const style = document.createElement('style');
        style.id = 'teleop-spin-keyframes';
        style.textContent = '@keyframes teleop-spin { to { transform: rotate(360deg); } }';
        document.head.appendChild(style);
    }

    return wrap;
}

export function idleHint(showSessionToggle) {
    const hint = document.createElement('div');
    hint.style.fontSize = 'var(--text-xs)';
    hint.style.color = '#64748b';
    hint.style.fontStyle = 'italic';
    hint.style.lineHeight = '1.4';
    hint.textContent = showSessionToggle
        ? 'Start TELEOP to take direct control.'
        : 'Use START TELEOP in PRIMITIVES to take direct control.';
    return hint;
}

export function parseSteps(descriptor, key, fallback) {
    return Array.isArray(descriptor && descriptor[key]) && descriptor[key].length
        ? descriptor[key].map(Number).filter(Number.isFinite)
        : fallback;
}

function speedInput(label, value, unit, min, max, onChange) {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.gap = '8px';

    const lbl = document.createElement('span');
    lbl.style.color = '#94a3b8';
    lbl.style.fontSize = 'var(--text-xs)';
    lbl.style.fontWeight = '600';
    lbl.style.flex = '1 1 auto';
    lbl.textContent = label;
    row.appendChild(lbl);

    const inp = document.createElement('input');
    inp.type = 'number';
    inp.min = String(min);
    inp.max = String(max);
    inp.step = 'any';
    inp.value = String(value);
    inp.style.width = '72px';
    inp.style.padding = '4px 6px';
    inp.style.fontSize = 'var(--text-sm)';
    inp.style.fontFamily = 'ui-monospace, monospace';
    inp.style.borderRadius = '4px';
    inp.style.border = '1px solid #334155';
    inp.style.background = '#0f1115';
    inp.style.color = '#e2e8f0';
    inp.onchange = () => {
        const n = Number(inp.value);
        if (!Number.isFinite(n)) {
            inp.value = String(value);
            return;
        }
        const clamped = Math.min(max, Math.max(min, n));
        inp.value = String(clamped);
        onChange(clamped);
    };
    row.appendChild(inp);

    const unitEl = document.createElement('span');
    unitEl.style.color = '#64748b';
    unitEl.style.fontSize = 'var(--text-xs)';
    unitEl.style.fontFamily = 'ui-monospace, monospace';
    unitEl.textContent = unit;
    row.appendChild(unitEl);

    return row;
}

/**
 * Motion speed controls (used when Go commits a goto command).
 * @param {string} tagId
 * @param {{ angularOnly?: boolean, descriptor?: object }} [opts]
 */
export function speedPanel(tagId, opts = {}) {
    const { angularOnly = false, descriptor = null } = opts;
    const panel = document.createElement('div');
    panel.style.border = '1px solid #2a2e36';
    panel.style.borderRadius = '6px';
    panel.style.padding = '8px';
    panel.style.background = '#12151a';
    panel.style.marginTop = '8px';

    const head = document.createElement('div');
    head.style.color = '#94a3b8';
    head.style.fontSize = 'var(--text-xs)';
    head.style.fontWeight = '600';
    head.style.letterSpacing = '0.05em';
    head.style.marginBottom = '8px';
    head.textContent = 'Motion speed';
    panel.appendChild(head);

    const speed = getTeleopSpeed(tagId);
    const angMin = Number(descriptor?.angular_min) || 1;
    const angMax = Number(descriptor?.angular_max) || 120;
    const linMin = Number(descriptor?.linear_min) || 1;
    const linMax = Number(descriptor?.linear_max) || 70;

    panel.appendChild(
        speedInput(
            'Angular',
            speed.angular_deg_s ?? DEFAULT_TELEOP_SPEED.angular_deg_s,
            'deg/s',
            angMin,
            angMax,
            (v) => setTeleopSpeed(tagId, { ...getTeleopSpeed(tagId), angular_deg_s: v }),
        ),
    );

    if (!angularOnly) {
        panel.appendChild(
            speedInput(
                'Linear',
                speed.linear_mm_s ?? DEFAULT_TELEOP_SPEED.linear_mm_s,
                'mm/s',
                linMin,
                linMax,
                (v) => setTeleopSpeed(tagId, { ...getTeleopSpeed(tagId), linear_mm_s: v }),
            ),
        );
    }

    const hint = document.createElement('div');
    hint.style.fontSize = 'var(--text-xs)';
    hint.style.color = '#475569';
    hint.style.fontStyle = 'italic';
    hint.style.marginTop = '6px';
    hint.textContent = angularOnly
        ? 'Applied when you press Go (Rz-only motion).'
        : 'Applied when you press Go (XY, Z, and rotation).';
    panel.appendChild(hint);

    return panel;
}

export function goButton(label, onGo) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-primary';
    btn.style.fontSize = 'var(--text-xs)';
    btn.style.marginTop = '8px';
    btn.textContent = label || 'Go';
    btn.onclick = () => onGo(btn);
    return btn;
}

export function phaseHint(teleop) {
    const phase = teleop?.command?.phase;
    if (phase === 'executing') {
        const el = document.createElement('div');
        el.style.fontSize = 'var(--text-xs)';
        el.style.color = '#22d3ee';
        el.style.marginTop = '6px';
        el.textContent = 'Moving…';
        return el;
    }
    return null;
}

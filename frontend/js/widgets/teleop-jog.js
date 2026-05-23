/**
 * `TeleopJog` — tunable widget for per-component TELEOP (Phase 8b).
 *
 * Renders three sub-blocks:
 *
 *   1. Status badge + Start/End toggle button.
 *   2. Three nudge rows (X, Y, rotation) with ± coarse/medium/fine
 *      buttons. Each click POSTs a single absolute TELEOP_JOG frame
 *      using the *current* ``tunables.nominal_pose`` plus the delta.
 *   3. A short hint that the operator can also drag the component on
 *      the canvas while TELEOP is active.
 *
 * State source of truth: ``comp.tunables.teleop_active`` (server-side).
 * The widget never holds local "am I in teleop?" state — that's a
 * recipe for de-sync. After every start/end the widget asks for a
 * fresh ``fetchLabState()`` and the next render reflects the change.
 *
 * Step amounts come from the catalog descriptor::
 *
 *   {"widget": "TeleopJog", "step_mm": [0.5, 2.0, 10.0], "step_deg": [0.5, 2.0, 10.0]}
 *
 * Defaults match :func:`infer_default_capabilities` (``[0.5, 2.0, 10.0]``).
 */
import { widgetCard, widgetTitle, fmtNum, nullPlaceholder } from './common.js';
import { startTeleop, endTeleop, teleopJog } from '../api/teleop.js';

const DEFAULT_STEPS = [0.5, 2.0, 10.0];

function nudgeRow(labelText, currentValue, unit, steps, onNudge) {
    const r = document.createElement('div');
    r.style.display = 'grid';
    r.style.gridTemplateColumns = 'auto 1fr auto auto auto auto auto auto';
    r.style.gap = '4px';
    r.style.alignItems = 'center';
    r.style.marginTop = '4px';

    const label = document.createElement('span');
    label.style.color = '#64748b';
    label.style.fontSize = '10px';
    label.textContent = labelText;
    r.appendChild(label);

    const val = document.createElement('span');
    val.style.color = '#cbd5e1';
    val.style.fontFamily = 'ui-monospace, monospace';
    val.style.fontSize = '11px';
    val.style.textAlign = 'right';
    val.textContent = `${fmtNum(currentValue, 2)} ${unit}`;
    r.appendChild(val);

    // Order from most-negative to most-positive so the button row reads naturally.
    const negSteps = steps.slice().reverse();
    negSteps.forEach((s) => r.appendChild(_btn(`\u2212${s}`, () => onNudge(-s))));
    steps.forEach((s) => r.appendChild(_btn(`+${s}`, () => onNudge(s))));
    return r;
}

function _btn(text, fn) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-secondary';
    b.style.fontSize = '10px';
    b.style.padding = '2px 6px';
    b.style.minWidth = '36px';
    b.textContent = text;
    b.onclick = fn;
    return b;
}

function statusBadge(active) {
    const el = document.createElement('span');
    el.style.fontSize = '9px';
    el.style.fontWeight = '600';
    el.style.padding = '2px 6px';
    el.style.borderRadius = '3px';
    el.style.fontFamily = 'ui-monospace, monospace';
    el.style.letterSpacing = '0.05em';
    if (active) {
        el.style.background = '#0e7490';
        el.style.color = '#cffafe';
        el.style.border = '1px solid #06b6d4';
        el.textContent = 'TELEOP ACTIVE';
    } else {
        el.style.background = '#1e293b';
        el.style.color = '#64748b';
        el.style.border = '1px solid #334155';
        el.textContent = 'TELEOP IDLE';
    }
    return el;
}

export default function TeleopJog({ tagId, fieldName, descriptor, comp, hooks }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const tunables = (comp && comp.tunables) || {};
    const active = !!tunables.teleop_active;
    const nominalPose = (tunables.nominal_pose && typeof tunables.nominal_pose === 'object')
        ? tunables.nominal_pose
        : null;

    const stepMm = Array.isArray(descriptor && descriptor.step_mm) && descriptor.step_mm.length
        ? descriptor.step_mm.map(Number).filter(Number.isFinite)
        : DEFAULT_STEPS;
    const stepDeg = Array.isArray(descriptor && descriptor.step_deg) && descriptor.step_deg.length
        ? descriptor.step_deg.map(Number).filter(Number.isFinite)
        : DEFAULT_STEPS;

    // --- Top row: status badge + Start/End toggle ----------------------
    const topRow = document.createElement('div');
    topRow.style.display = 'flex';
    topRow.style.justifyContent = 'space-between';
    topRow.style.alignItems = 'center';
    topRow.style.gap = '8px';
    topRow.style.marginBottom = '6px';
    topRow.appendChild(statusBadge(active));

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'btn btn-secondary';
    toggle.style.fontSize = '10px';
    toggle.style.padding = '3px 10px';
    toggle.textContent = active ? 'End TELEOP' : 'Start TELEOP';
    toggle.title = active
        ? 'Release the per-component TELEOP lease (END_TELEOP).'
        : 'Acquire the per-component TELEOP lease (START_TELEOP). Nulls measurables (Golden Rule).';

    const refresh = async () => {
        if (hooks && typeof hooks.fetchLabState === 'function') {
            try { await hooks.fetchLabState(); } catch (_e) { /* swallow — UI will re-poll */ }
        }
    };

    toggle.onclick = async () => {
        toggle.disabled = true;
        const fn = active ? endTeleop : startTeleop;
        const result = await fn(tagId);
        toggle.disabled = false;
        if (!result.ok && hooks && hooks.log) {
            hooks.log(`TELEOP toggle failed: ${result.error}`, 'warn');
        }
        await refresh();
    };
    topRow.appendChild(toggle);
    card.appendChild(topRow);

    // --- Body: nudge rows when active, hint when idle ------------------
    if (!active) {
        const hint = document.createElement('div');
        hint.style.fontSize = '10px';
        hint.style.color = '#64748b';
        hint.style.fontStyle = 'italic';
        hint.style.lineHeight = '1.4';
        hint.textContent =
            'Start TELEOP to take direct control. While active, the part is ' +
            'jogged through these buttons or by dragging it on the canvas (' +
            'frames send at ~15 fps).';
        card.appendChild(hint);
        return card;
    }

    if (!nominalPose) {
        card.appendChild(nullPlaceholder('\u2014 nominal_pose missing; cannot jog'));
        return card;
    }

    const sendJog = async (deltaX, deltaY, deltaRot) => {
        // Always send absolute pose so frame loss is self-healing.
        const next = {
            x: Number(nominalPose.x || 0) + deltaX,
            y: Number(nominalPose.y || 0) + deltaY,
            rotation: Number(nominalPose.rotation || 0) + deltaRot,
        };
        const result = await teleopJog(tagId, { nominal_pose: next });
        if (!result.ok && hooks && hooks.log) {
            hooks.log(`jog refused: ${result.error}`, 'warn');
        }
        // Refresh after every nudge so the displayed value tracks the
        // server. At 3 button clicks/second this is negligible load.
        await refresh();
    };

    card.appendChild(
        nudgeRow('X', nominalPose.x, 'mm', stepMm, (d) => sendJog(d, 0, 0)),
    );
    card.appendChild(
        nudgeRow('Y', nominalPose.y, 'mm', stepMm, (d) => sendJog(0, d, 0)),
    );
    card.appendChild(
        nudgeRow('\u03b8', nominalPose.rotation, '\u00b0', stepDeg, (d) => sendJog(0, 0, d)),
    );

    const canvasHint = document.createElement('div');
    canvasHint.style.fontSize = '9px';
    canvasHint.style.color = '#475569';
    canvasHint.style.fontStyle = 'italic';
    canvasHint.style.marginTop = '6px';
    canvasHint.textContent =
        'Tip: you can also drag the component on the canvas while TELEOP is active.';
    card.appendChild(canvasHint);

    return card;
}

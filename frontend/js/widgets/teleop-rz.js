/**

 * `TeleopRz` — table TeleOp: in-place rotation only (release / Go to commit).

 */

import { widgetCard, nullPlaceholder } from './common.js';

import { endTeleop, startTeleop, teleopGoto } from '../api/teleop.js';

import { getTelemetry, isTeleopActive, isTeleopReady, isTeleopStarting, tunableValue } from '../component-state.js';

import { store } from '../state/store.js';

import {

    createTeleopPlanAxisPanel,

    DEFAULT_STEP_DEG,

    goButton,

    idleHint,

    loadingPanel,

    parseSteps,

    phaseHint,

    speedPanel,

    statusBadge,

} from './teleop-common.js';

import { afterCommandDispatch } from '../primitives/shared.js';

import { buildTeleopGotoPayload } from '../teleop-target.js';

import { ensureTeleopTargetPose, getTeleopTargetPose } from '../teleop-pose.js';



export default function TeleopRz({

    tagId,

    descriptor,

    comp,

    hooks,

    labState = null,

    showSessionToggle = true,

    embedded = false,

}) {

    const card = embedded ? document.createElement('div') : widgetCard();

    if (!embedded) {

        card.style.padding = '0';

        card.style.background = 'transparent';

        card.style.border = 'none';

    }



    const state = labState || store.labState;

    const teleop = getTelemetry(comp).teleop || {};

    const active = isTeleopActive(comp);

    const ready = isTeleopReady(comp);

    const starting = isTeleopStarting(comp);



    const refresh = async () => {

        if (hooks) await afterCommandDispatch(hooks, tagId);

    };



    const topRow = document.createElement('div');

    topRow.style.display = 'flex';

    topRow.style.justifyContent = 'space-between';

    topRow.style.alignItems = 'center';

    topRow.style.gap = '8px';

    topRow.style.marginBottom = '8px';



    if (starting) topRow.appendChild(statusBadge('LOADING', 'loading'));

    else if (ready) topRow.appendChild(statusBadge('READY', 'ready'));

    else topRow.appendChild(statusBadge('IDLE', 'idle'));



    if (showSessionToggle) {

        const toggle = document.createElement('button');

        toggle.type = 'button';

        toggle.className = 'btn btn-secondary';

        toggle.style.fontSize = 'var(--text-xs)';

        toggle.style.padding = '3px 10px';

        toggle.textContent = active ? 'End TELEOP' : 'Start TELEOP';

        toggle.disabled = starting;

        toggle.onclick = async () => {

            toggle.disabled = true;

            const fn = active ? endTeleop : startTeleop;

            const pending = fn(tagId);

            if (!active) {
                // Optimistic Loading UI while real-edge START may take seconds.
                await Promise.resolve();
                await refresh();
            }

            const result = await pending;

            toggle.disabled = false;

            if (!result.ok && result.error !== 'cancelled' && hooks && hooks.log) {

                hooks.log(`TELEOP toggle failed: ${result.error}`, 'warn');

            }

            await refresh();

        };

        topRow.appendChild(toggle);

    }

    card.appendChild(topRow);



    if (!active) {

        card.appendChild(idleHint(showSessionToggle));

        return card;

    }



    if (starting) {

        card.appendChild(loadingPanel(teleop.last_error));

        return card;

    }



    if (!tunableValue(comp, 'nominal_pose')) {

        card.appendChild(nullPlaceholder('\u2014 nominal_pose missing; cannot plan'));

        return card;

    }



    // Seed TARGET only if missing. Do NOT sync from CURRENT on every panel
    // rebuild — Go → refresh() would overwrite the planned target with the
    // still-moving live pose (e.g. target 40 jumps back to current 60).
    // START_TELEOP already calls syncTeleopTargetFromCurrent once.
    ensureTeleopTargetPose(tagId, state);



    const stepDeg = parseSteps(descriptor, 'step_deg', DEFAULT_STEP_DEG);

    const grid = document.createElement('div');

    grid.style.display = 'flex';

    grid.style.flexDirection = 'column';

    grid.style.gap = '8px';



    const sendGo = async (btn) => {

        btn.disabled = true;

        const target = getTeleopTargetPose(tagId);

        if (!target) {

            btn.disabled = false;

            return;

        }

        const payload = buildTeleopGotoPayload(tagId, comp, state, target);

        const result = await teleopGoto(tagId, payload);

        btn.disabled = false;

        if (!result.ok && hooks && hooks.log) {

            hooks.log(`goto refused: ${result.error}`, 'warn');

        }

        await refresh();

    };



    grid.appendChild(

        createTeleopPlanAxisPanel({

            tagId,

            axisName: 'Rz rotation',

            field: 'rotation',

            unit: '\u00b0',

            steps: stepDeg,

        }),

    );

    card.appendChild(grid);

    card.appendChild(speedPanel(tagId, { angularOnly: true, descriptor }));

    card.appendChild(goButton('Go to target', (btn) => sendGo(btn)));

    const ph = phaseHint(teleop);

    if (ph) card.appendChild(ph);



    const hint = document.createElement('div');

    hint.style.fontSize = 'var(--text-xs)';

    hint.style.color = '#475569';

    hint.style.fontStyle = 'italic';

    hint.style.marginTop = '8px';

    hint.textContent =

        'Scroll on canvas or nudge to plan TARGET rotation, then Go. CURRENT shows hardware pose.';

    card.appendChild(hint);



    return card;

}



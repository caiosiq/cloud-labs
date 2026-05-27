/**

 * `TeleopPose3d` — in-gripper TeleOp: XYZ + rotation (release / Go to commit).

 */

import { widgetCard, nullPlaceholder } from './common.js';

import { endTeleop, startTeleop, teleopGoto } from '../api/teleop.js';

import { getTelemetry, isTeleopActive, isTeleopReady, isTeleopStarting, tunableValue } from '../component-state.js';

import { isHeldTag } from '../component-model.js';

import { store } from '../state/store.js';

import {

    createTeleopPlanAxisPanel,

    DEFAULT_STEP_DEG,

    DEFAULT_STEP_MM,

    DEFAULT_STEP_Z_MM,

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

import {
    ensureTeleopTargetPose,
    getTeleopTargetPose,
    seedTeleopTargetPose,
} from '../teleop-pose.js';



export default function TeleopPose3d({

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

    const stepMm = parseSteps(descriptor, 'step_mm', DEFAULT_STEP_MM);

    const stepDeg = parseSteps(descriptor, 'step_deg', DEFAULT_STEP_DEG);

    const stepZ = parseSteps(descriptor, 'step_z_mm', DEFAULT_STEP_Z_MM);



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

        toggle.style.fontSize = '10px';

        toggle.style.padding = '3px 10px';

        toggle.textContent = active ? 'End TELEOP' : 'Start TELEOP';

        toggle.disabled = starting;

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



    if (!tunableValue(comp, 'nominal_pose') && !isHeldTag(tagId, state)) {

        card.appendChild(nullPlaceholder('\u2014 pose missing; cannot plan'));

        return card;

    }



    ensureTeleopTargetPose(tagId, state);



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

        const planned = { ...target };

        const payload = buildTeleopGotoPayload(tagId, comp, state, planned);

        const result = await teleopGoto(tagId, payload);

        if (result.ok) {

            seedTeleopTargetPose(tagId, planned);

        }

        btn.disabled = false;

        if (!result.ok && hooks && hooks.log) {

            hooks.log(`goto refused: ${result.error}`, 'warn');

        }

        await refresh();

    };



    grid.appendChild(

        createTeleopPlanAxisPanel({ tagId, axisName: 'X', field: 'x', unit: 'mm', steps: stepMm }),

    );

    grid.appendChild(

        createTeleopPlanAxisPanel({ tagId, axisName: 'Y', field: 'y', unit: 'mm', steps: stepMm }),

    );

    grid.appendChild(

        createTeleopPlanAxisPanel({ tagId, axisName: 'Z', field: 'z', unit: 'mm', steps: stepZ }),

    );

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

    card.appendChild(speedPanel(tagId, { descriptor }));

    card.appendChild(goButton('Go to target', (btn) => sendGo(btn)));

    const ph = phaseHint(teleop);

    if (ph) card.appendChild(ph);



    const canvasHint = document.createElement('div');

    canvasHint.style.fontSize = '9px';

    canvasHint.style.color = '#475569';

    canvasHint.style.fontStyle = 'italic';

    canvasHint.style.marginTop = '8px';

    canvasHint.textContent =

        'Drag or scroll on canvas to plan TARGET; press Go to move. CURRENT tracks hardware.';

    card.appendChild(canvasHint);



    return card;

}



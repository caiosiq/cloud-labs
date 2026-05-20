/**
 * Floating component context panel.
 *
 * Renders the right-hand-side panel that appears when the user selects a component on the canvas
 * (or in the inventory sidebar). The panel adapts its content based on:
 *   1. Placement state: PLACED | STORED | INVENTORY (`placementUiLabel`).
 *   2. System status:   IDLE | HOLDING (`holding` + `unconfirmed`).
 *
 * It owns the "in-air manipulation" sub-section, motor controls, scan-rotate, and the various
 * STORED affordances (drag-from-storage, place-from-storage, recenter).
 *
 * Dependencies that are still in `app-main.js` (the canvas `render()` plus the
 * `fetchLabState` call site after observing measurables) are injected at boot via
 * `initContextPanel`.
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { sendCommand } from '../api/commands.js';
import { showParameterModal } from './modals.js';
import { isStorageRegion } from '../storage-region.js';
import {
    getHolding,
    isHeldTag,
    isHoldingState,
    isHoldingUnconfirmed,
    isOffTableComponent,
    isOnTableComponent,
    isStoredComponent,
    measPose,
} from '../component-model.js';
import { fetchLabState } from '../state/lab-state.js';

const PRIMITIVE_DEV_HINTS =
    typeof window !== 'undefined' &&
    typeof window.location !== 'undefined' &&
    /(?:^|[?&])dev=1(?:&|$)/.test(window.location.search || '');

// DOM refs (resolved lazily so we don't need DI for elements that exist at boot anyway).
function refs() {
    return {
        contextPanel: document.getElementById('context-panel'),
        ctxX: document.getElementById('ctx-x'),
        ctxY: document.getElementById('ctx-y'),
        ctxRot: document.getElementById('ctx-rot'),
        ctxMoveBtn: document.getElementById('ctx-move-btn'),
        ctxObserveSlot: document.getElementById('ctx-observe-slot'),
        ctxStrategies: document.getElementById('ctx-strategies'),
        selectedCompName: document.getElementById('selected-comp-name'),
        selectedCompTag: document.getElementById('selected-comp-tag'),
        selectedCompProperties: document.getElementById('selected-comp-properties'),
    };
}

let _render = () => {};
let _checkCollision = () => ({ detected: false });

/**
 * @param {{
 *   render: () => void,
 *   checkCollision: (targetId: string, x: number, y: number, opts?: any) => { detected: boolean, other?: string },
 * }} deps
 */
export function initContextPanel(deps) {
    if (deps && typeof deps.render === 'function') _render = deps.render;
    if (deps && typeof deps.checkCollision === 'function') _checkCollision = deps.checkCollision;

    // Wire the Move button — calls MOVE_COMPONENT with a pre-flight collision check; on collision
    // we revert the x/y inputs to the current ghost state so the user sees the rejection.
    const { ctxMoveBtn, ctxX, ctxY, ctxRot } = refs();
    if (ctxMoveBtn) {
        ctxMoveBtn.addEventListener('click', async () => {
            if (!store.selectedComponent) return;

            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);

            const collision = _checkCollision(store.selectedComponent, tx, ty);
            if (collision.detected) {
                log(`Move cancelled: Collision with ${collision.other}`, 'error');
                if (store.ghostState[store.selectedComponent]) {
                    const old = store.ghostState[store.selectedComponent];
                    ctxX.value = old.x.toFixed(1);
                    ctxY.value = old.y.toFixed(1);
                }
                return;
            }

            // Bump the ghost immediately for visual feedback while the command is in flight.
            store.ghostState[store.selectedComponent].x = tx;
            store.ghostState[store.selectedComponent].y = ty;
            store.ghostState[store.selectedComponent].rotation = trot;

            await sendCommand({
                action: 'MOVE_COMPONENT',
                target_id: store.selectedComponent,
                parameters: { target_x: tx, target_y: ty, rotation: trot },
            });
            _render();
        });
    }
}

/** Legacy-style label for context panel / drag rules (PLACED | STORED | INVENTORY). */
export function placementUiLabel(comp) {
    if (!comp) return 'PLACED';
    if (isStoredComponent(comp)) return 'STORED';
    if (isOffTableComponent(comp)) return 'INVENTORY';
    return 'PLACED';
}

/** Same as clicking empty canvas: clear selection and hide the floating component panel. */
export function clearSelectionAndHideContextPanel() {
    store.selectedComponent = null;
    store.contextPanelStateSnapshot = null;
    store.contextPanelStatusSnapshot = null;
    store.dragFromStorageTag = null;
    store.dragFromStorageStartPose = null;
    const r = refs();
    if (r.contextPanel) r.contextPanel.style.display = 'none';
    _render();
}

// --- Holding banner ---

function ensureHoldingBannerEl() {
    let el = document.getElementById('holding-banner');
    if (el) return el;
    const anchor = document.getElementById('layout-warnings');
    el = document.createElement('div');
    el.id = 'holding-banner';
    el.style.cssText =
        'display: none; margin: 0 16px 10px; padding: 10px 12px; font-size: 11px; ' +
        'color: #f3e8ff; background: rgba(168, 85, 247, 0.12); ' +
        'border: 1px solid rgba(168, 85, 247, 0.45); border-radius: 6px; line-height: 1.4;';
    if (anchor && anchor.parentNode) {
        anchor.parentNode.insertBefore(el, anchor);
    } else {
        document.body.insertBefore(el, document.body.firstChild);
    }
    return el;
}

export function updateHoldingBanner() {
    const el = ensureHoldingBannerEl();
    if (!store.labState || !isHoldingState(store.labState)) {
        el.style.display = 'none';
        el.innerHTML = '';
        return;
    }
    const hld = getHolding(store.labState);
    el.style.display = 'block';
    if (hld.requires_operator_confirm) {
        // UNCONFIRMED: red banner — gripper closed on startup but the held tag is unknown.
        // All other commands are blocked until the operator runs `confirmhold <tag>`.
        el.style.background = 'rgba(239, 68, 68, 0.12)';
        el.style.borderColor = 'rgba(239, 68, 68, 0.5)';
        el.style.color = '#fee2e2';
        el.innerHTML =
            '<div style="display:flex; align-items:flex-start; gap:8px;">' +
            '<span class="material-icons-round" style="font-size:16px;color:#fecaca;">lock</span>' +
            '<div style="flex:1;">' +
            '<div style="font-weight:600; margin-bottom:2px;">HOLDING (unconfirmed)</div>' +
            'The gripper reports closed on startup but the held tag is unknown. ' +
            'Use <code>confirmhold &lt;tag&gt;</code> in the Command Console (or the confirm button in the part panel) to continue. ' +
            'All other commands are blocked until confirmed.' +
            '</div></div>';
    } else {
        el.style.background = 'rgba(168, 85, 247, 0.12)';
        el.style.borderColor = 'rgba(168, 85, 247, 0.45)';
        el.style.color = '#f3e8ff';
        const pose = hld.nominal_pose || {};
        const poseStr = [
            Number.isFinite(Number(pose.x)) ? `x=${Number(pose.x).toFixed(1)}` : null,
            Number.isFinite(Number(pose.y)) ? `y=${Number(pose.y).toFixed(1)}` : null,
            Number.isFinite(Number(pose.rotation)) ? `rot=${Number(pose.rotation).toFixed(1)}°` : null,
            Number.isFinite(Number(pose.z)) ? `z=${Number(pose.z).toFixed(1)}` : null,
        ].filter(Boolean).join(' ');
        el.innerHTML =
            '<div style="display:flex; align-items:flex-start; gap:8px;">' +
            '<span class="material-icons-round" style="font-size:16px;color:#d8b4fe;">pan_tool</span>' +
            '<div style="flex:1;">' +
            `<div style="font-weight:600; margin-bottom:2px;">HOLDING ${hld.tag_id || '<tag>'}</div>` +
            (poseStr
                ? `<div style="color:#c4b5fd; font-family: monospace; font-size: 10px;">${poseStr}</div>`
                : '') +
            '<div style="margin-top:4px;">' +
            'Only <strong>HOVER</strong>, <strong>PLACE_FROM_HOVER</strong>, <strong>SCAN_ROTATE_IN_PLACE</strong>, or ' +
            '<strong>CONFIRM_HOLDING_TAG</strong> are accepted for this tag until released.' +
            '</div>' +
            '</div></div>';
    }
}

// --- Motor commands ---

async function moveMotor(targetId, motorId, dist) {
    await sendCommand({
        action: 'MOVE_MOTOR',
        target_id: targetId,
        parameters: { motor_id: motorId, distance: dist },
    });
}

async function motorSendHome(targetId, motorId) {
    await sendCommand({
        action: 'MOTOR_SEND_HOME',
        target_id: targetId,
        parameters: { motor_id: motorId },
    });
}

async function motorSetZero(targetId, motorId) {
    await sendCommand({
        action: 'MOTOR_SET_ZERO',
        target_id: targetId,
        parameters: { motor_id: motorId },
    });
}

/** Refresh tracked motor angle labels from lab-state (pose.motor_rotations). */
export function updateMotorAngleLabels(tagId) {
    if (!tagId || !store.labState || !store.labState.components) return;
    const comp = store.labState.components[tagId];
    if (!comp) return;
    const mr = (measPose(comp).motor_rotations) || {};
    const mids = store.catalogMap[tagId] && store.catalogMap[tagId].motor_ids;
    if (!mids || !mids.length) return;
    mids.forEach((mid) => {
        const el = document.getElementById(`ctx-motor-angle-${mid}`);
        if (!el) return;
        const v = mr[String(mid)];
        const n = (v !== undefined && v !== null && Number.isFinite(Number(v))) ? Number(v) : 0;
        el.textContent = `θ ${n.toFixed(2)}`;
    });
}

// --- Scan-rotate sub-panel ---

/**
 * Build the Scan-Rotate In Place sub-panel (θ_min, θ_max, deg/s, Start).
 * The UI is identical regardless of whether the component is currently held or placed — the
 * backend decides which `lab_automation` function to call based on `system_status` at dispatch
 * time (see new_primitives.md §7 and labautomation_new_primitives.md §2.4).
 *
 * `contextHint` ("held" | "placed") only tweaks the helper text.
 */
function buildScanRotateSubPanel(tagId, { contextHint = 'held' } = {}) {
    const scan = document.createElement('div');
    scan.style.marginTop = '12px';
    scan.style.paddingTop = '10px';
    scan.style.borderTop = '1px dashed #2a2e36';
    const scanHdr = document.createElement('div');
    scanHdr.style.fontSize = '10px';
    scanHdr.style.color = '#94a3b8';
    scanHdr.style.fontWeight = '600';
    scanHdr.style.marginBottom = '6px';
    scanHdr.textContent = 'SCAN ROTATE IN PLACE';
    scan.appendChild(scanHdr);

    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.lineHeight = '1.4';
    hint.style.margin = '0 0 6px 0';
    hint.innerHTML = contextHint === 'placed'
        ? 'Briefly grips the part with the robot arm, sweeps \u03b8, then releases it back at the same XY at the new rotation. Works regardless of whether the component has a motor.'
        : 'Rotates the held part in-air while XY + Z stay locked at the hover pose.';
    scan.appendChild(hint);

    const scanGrid = document.createElement('div');
    scanGrid.style.display = 'grid';
    scanGrid.style.gridTemplateColumns = '1fr 1fr 1fr';
    scanGrid.style.gap = '6px';
    const scanTMin = document.createElement('input');
    scanTMin.type = 'number';
    scanTMin.className = 'coord-input';
    scanTMin.placeholder = 'θ min°';
    scanTMin.value = '-45';
    const scanTMax = document.createElement('input');
    scanTMax.type = 'number';
    scanTMax.className = 'coord-input';
    scanTMax.placeholder = 'θ max°';
    scanTMax.value = '45';
    const scanSpd = document.createElement('input');
    scanSpd.type = 'number';
    scanSpd.className = 'coord-input';
    scanSpd.placeholder = 'deg/s';
    scanSpd.value = '30';
    scanGrid.appendChild(scanTMin);
    scanGrid.appendChild(scanTMax);
    scanGrid.appendChild(scanSpd);
    scan.appendChild(scanGrid);

    const bScan = document.createElement('button');
    bScan.type = 'button';
    bScan.className = 'btn btn-secondary';
    bScan.style.marginTop = '6px';
    bScan.style.fontSize = '11px';
    bScan.style.width = '100%';
    bScan.innerHTML =
        '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">rotate_right</span> Start scan rotate';
    bScan.onclick = async () => {
        const tmin = parseFloat(scanTMin.value);
        const tmax = parseFloat(scanTMax.value);
        const spd = parseFloat(scanSpd.value);
        if (![tmin, tmax, spd].every(Number.isFinite) || !(spd > 0)) {
            log('Invalid scan params (need numbers; speed > 0).', 'error');
            return;
        }
        await sendCommand({
            action: 'SCAN_ROTATE_IN_PLACE',
            target_id: tagId,
            parameters: {
                theta_min: tmin,
                theta_max: tmax,
                speed_deg_per_s: spd,
                axis: 'z',
            },
        });
    };
    scan.appendChild(bScan);
    return scan;
}

// --- In-air manipulation sub-section ---

/**
 * Render the in-air manipulation section (Pick / Hover / Place-from-hover /
 * Scan-rotate / Confirm) in the context panel. Behavior depends on the
 * current HOLDING state (see new_primitives.md §6):
 *
 *  - IDLE & selected part is on-table:  show Pick button.
 *  - HOLDING_UNCONFIRMED:                show confirm button + lock message.
 *  - HOLDING & selected is held tag:     show Hover form + Place + Scan.
 *  - HOLDING & selected is NOT held:     disable ctx-move-btn with notice.
 */
function renderInAirControlsForContext(name, comp, placementState) {
    document.querySelectorAll('.ctx-in-air').forEach((el) => el.remove());

    const { ctxX, ctxY, ctxRot, ctxMoveBtn, ctxStrategies } = refs();

    const labState = store.labState || {};
    const holding = isHoldingState(labState);
    const unconfirmed = isHoldingUnconfirmed(labState);
    const held = getHolding(labState).tag_id;
    const selectedIsHeld = isHeldTag(name, labState);

    const section = document.createElement('div');
    section.className = 'ctx-in-air';
    section.style.marginTop = '14px';
    section.style.paddingTop = '10px';
    section.style.borderTop = '1px solid #2a2e36';

    const header = document.createElement('div');
    header.style.fontSize = '10px';
    header.style.color = '#94a3b8';
    header.style.fontWeight = '600';
    header.style.marginBottom = '6px';
    header.textContent = 'IN-AIR MANIPULATION';
    section.appendChild(header);

    if (unconfirmed) {
        const p = document.createElement('p');
        p.style.fontSize = '10px';
        p.style.color = '#fca5a5';
        p.style.lineHeight = '1.4';
        p.style.margin = '0 0 8px 0';
        p.innerHTML =
            'Gripper reports closed on startup. Select the tag physically in the gripper and click <strong>Confirm held tag</strong>. All other commands are blocked until confirmed.';
        section.appendChild(p);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-primary';
        btn.style.width = '100%';
        btn.style.fontSize = '11px';
        btn.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">verified</span> Confirm held tag: ' +
            name;
        btn.onclick = () =>
            sendCommand({ action: 'CONFIRM_HOLDING_TAG', target_id: name, parameters: {} });
        section.appendChild(btn);
        if (ctxStrategies && ctxStrategies.parentNode) ctxStrategies.parentNode.appendChild(section);
        // Block regular move while unconfirmed.
        if (ctxMoveBtn) {
            ctxMoveBtn.disabled = true;
            ctxMoveBtn.title = 'Disabled while HOLDING is unconfirmed.';
        }
        return;
    }

    if (holding && !selectedIsHeld) {
        const p = document.createElement('p');
        p.style.fontSize = '10px';
        p.style.color = '#c4b5fd';
        p.style.lineHeight = '1.4';
        p.style.margin = '0 0 4px 0';
        p.innerHTML =
            `Robot is currently holding <strong>${held || '<tag>'}</strong>. ` +
            'Release it (Place from hover) before interacting with another part.';
        section.appendChild(p);
        if (ctxStrategies && ctxStrategies.parentNode) ctxStrategies.parentNode.appendChild(section);
        if (ctxMoveBtn) {
            ctxMoveBtn.disabled = true;
            ctxMoveBtn.title = `Disabled: robot is holding ${held || 'another part'}.`;
        }
        return;
    }

    // From here on, the regular move button is re-enabled.
    if (ctxMoveBtn) {
        ctxMoveBtn.disabled = false;
        ctxMoveBtn.title = '';
    }

    if (holding && selectedIsHeld) {
        // Hide the generic Move button — use Hover / Place-from-hover instead.
        if (ctxMoveBtn) ctxMoveBtn.style.display = 'none';

        const hld = getHolding(labState);
        // Default safe hover height is 40 mm above the breadboard surface; reuse the held part's
        // current z when known so the user doesn't have to re-enter it every action.
        const currentZ =
            (hld.nominal_pose && Number.isFinite(Number(hld.nominal_pose.z)))
                ? Number(hld.nominal_pose.z)
                : 40.0;

        const zRow = document.createElement('div');
        zRow.style.marginBottom = '8px';
        const zLabel = document.createElement('label');
        zLabel.style.fontSize = '10px';
        zLabel.style.color = '#94a3b8';
        zLabel.style.display = 'block';
        zLabel.style.marginBottom = '4px';
        zLabel.textContent = 'Z CLEARANCE (mm above table)';
        zLabel.title =
            'Height of the component\'s base above the breadboard surface. ' +
            '0 = on the table, 40 = default safe hover height.';
        zRow.appendChild(zLabel);
        const zInp = document.createElement('input');
        zInp.type = 'number';
        zInp.id = 'ctx-z';
        zInp.step = '0.5';
        zInp.className = 'coord-input';
        zInp.style.width = '100%';
        zInp.value = currentZ.toFixed(1);
        zRow.appendChild(zInp);
        section.appendChild(zRow);

        const btnRow = document.createElement('div');
        btnRow.style.display = 'flex';
        btnRow.style.flexDirection = 'column';
        btnRow.style.gap = '6px';

        const bHover = document.createElement('button');
        bHover.type = 'button';
        bHover.className = 'btn btn-secondary';
        bHover.style.fontSize = '11px';
        bHover.style.width = '100%';
        bHover.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">open_with</span> Hover to X/Y/Rot/Z';
        bHover.onclick = async () => {
            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);
            const tz = parseFloat(zInp.value);
            if (![tx, ty, trot, tz].every(Number.isFinite)) {
                log('Invalid coordinates for HOVER (need x, y, rotation, z).', 'error');
                return;
            }
            await sendCommand({
                action: 'HOVER',
                target_id: name,
                parameters: { target_x: tx, target_y: ty, rotation: trot, z: tz },
            });
        };
        btnRow.appendChild(bHover);

        const bPlace = document.createElement('button');
        bPlace.type = 'button';
        bPlace.className = 'btn btn-primary';
        bPlace.style.fontSize = '11px';
        bPlace.style.width = '100%';
        bPlace.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">south_east</span> Place from hover';
        bPlace.onclick = async () => {
            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);
            if (![tx, ty, trot].every(Number.isFinite)) {
                log('Invalid coordinates.', 'error');
                return;
            }
            if (isStorageRegion(tx, ty)) {
                log('Place target must be outside the storage quadrant.', 'error');
                return;
            }
            await sendCommand({
                action: 'PLACE_FROM_HOVER',
                target_id: name,
                parameters: { target_x: tx, target_y: ty, rotation: trot },
            });
        };
        btnRow.appendChild(bPlace);

        section.appendChild(btnRow);

        section.appendChild(buildScanRotateSubPanel(name, { contextHint: 'held' }));

        if (ctxStrategies && ctxStrategies.parentNode) ctxStrategies.parentNode.appendChild(section);
        return;
    }

    // IDLE path: expose PICK for on-table parts + Scan Rotate (placed-mode).
    if (comp && isOnTableComponent(comp) && placementState !== 'STORED') {
        const p = document.createElement('p');
        p.style.fontSize = '10px';
        p.style.color = '#94a3b8';
        p.style.lineHeight = '1.4';
        p.style.margin = '0 0 6px 0';
        p.innerHTML =
            '<strong>Pick</strong> closes the gripper on this part and lifts to a safe Z (→ HOLDING). ' +
            'Then use <strong>Hover</strong> to re-pose mid-air, <strong>Place from hover</strong> to set down. ' +
            '<strong>Scan rotate</strong> sweeps θ in place — the robot decides whether to rotate it in-air or on the table.';
        section.appendChild(p);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary';
        btn.style.fontSize = '11px';
        btn.style.width = '100%';
        btn.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">pan_tool</span> Pick up (start HOLDING)';
        btn.onclick = () =>
            sendCommand({ action: 'PICK_COMPONENT', target_id: name, parameters: {} });
        section.appendChild(btn);

        // Placed-mode scan-rotate: same user intent (sweep θ at constant rate), but the backend
        // dispatches to `scan_rotate_placed_cloudlab` in lab_automation instead of the held-mode
        // function.
        section.appendChild(buildScanRotateSubPanel(name, { contextHint: 'placed' }));

        if (ctxStrategies && ctxStrategies.parentNode) ctxStrategies.parentNode.appendChild(section);
    }
}

// --- Main: updateContextPanel ---

export function updateContextPanel(name) {
    const {
        contextPanel,
        ctxX,
        ctxY,
        ctxRot,
        ctxMoveBtn,
        ctxObserveSlot,
        ctxStrategies,
        selectedCompName,
        selectedCompTag,
        selectedCompProperties,
    } = refs();
    if (!contextPanel || !ctxX || !ctxY || !ctxRot) return;

    const comp = store.labState.components[name];
    const pose = store.ghostState[name];

    let displayName = name;
    let properties = {};

    if (store.catalogMap[name]) {
        displayName = store.catalogMap[name].name;
        if (store.catalogMap[name].properties) {
            properties = store.catalogMap[name].properties;
        }
    }

    if (selectedCompName) selectedCompName.textContent = displayName;
    if (selectedCompTag) selectedCompTag.textContent = name;

    if (selectedCompProperties) {
        selectedCompProperties.innerHTML = '';
        if (Object.keys(properties).length > 0) {
            const propsHtml = Object.entries(properties).map(([key, val]) => {
                // Format key: radius_of_curvature → "Radius of curvature".
                const cleanKey = key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
                return `<div style="margin-bottom: 2px;">${cleanKey}: <span style="color: #e2e8f0;">${val}</span></div>`;
            }).join('');
            selectedCompProperties.innerHTML = propsHtml;
        }
    }

    contextPanel.style.display = 'block';

    document.querySelectorAll('.ctx-dynamic-storage').forEach((el) => el.remove());

    const placementState = placementUiLabel(comp);
    ctxX.value = pose.x.toFixed(1);
    ctxY.value = pose.y.toFixed(1);
    ctxRot.value = (pose.rotation || 0).toFixed(1);

    if (ctxObserveSlot) {
        ctxObserveSlot.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.style.borderTop = '1px solid #2a2e36';
        wrap.style.paddingTop = '10px';
        wrap.style.marginTop = '4px';
        const h = document.createElement('div');
        h.style.fontSize = '10px';
        h.style.color = '#94a3b8';
        h.style.fontWeight = '600';
        h.style.marginBottom = '6px';
        h.textContent = 'MEASURABLES: SAVED VS OBSERVE';
        wrap.appendChild(h);
        const p = document.createElement('p');
        p.style.fontSize = '9px';
        p.style.color = '#64748b';
        p.style.lineHeight = '1.35';
        p.style.margin = '0 0 8px 0';
        p.innerHTML =
            'Coordinates above are <strong>intent</strong> (ghost). The UI polls <strong>saved</strong> measurables via lab state. <strong>Observe</strong> asks the lab to refresh this tag’s measurables (e.g. camera → <code style="color:#94a3b8;">camera_image</code>).';
        wrap.appendChild(p);
        if (PRIMITIVE_DEV_HINTS) {
            const dev = document.createElement('div');
            dev.style.fontSize = '9px';
            dev.style.color = '#475569';
            dev.style.marginBottom = '6px';
            dev.innerHTML =
                'Dev: <code>OBSERVE_MEASURABLES</code> · <code>POST /api/components/{tag}/measurables/observe</code>';
            wrap.appendChild(dev);
        }
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.flexWrap = 'wrap';
        row.style.gap = '8px';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary';
        btn.style.fontSize = '11px';
        btn.style.padding = '6px 10px';
        btn.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">photo_camera</span> Observe measurables';
        const status = document.createElement('span');
        status.style.fontSize = '10px';
        status.style.color = '#94a3b8';
        btn.onclick = async () => {
            status.textContent = '…';
            btn.disabled = true;
            try {
                const r = await fetch(
                    `/api/components/${encodeURIComponent(name)}/measurables/observe`,
                    { method: 'POST' },
                );
                const data = await r.json().catch(() => ({}));
                if (!r.ok) {
                    const det = data.detail !== undefined ? data.detail : r.status;
                    const msg = typeof det === 'string' ? det : JSON.stringify(det);
                    log(`Observe failed: ${msg}`, 'error');
                    status.textContent = 'Failed';
                    return;
                }
                status.textContent = 'OK';
                const ci = data.measurables && data.measurables.camera_image;
                if (ci && typeof ci === 'object' && ci.path) {
                    const base = String(ci.path).replace(/^.*[/\\\\]/, '');
                    status.textContent = `OK · ${base}`;
                }
                await fetchLabState();
                updateContextPanel(name);
            } catch (e) {
                log(`Observe error: ${e && e.message ? e.message : e}`, 'error');
                status.textContent = 'Error';
            } finally {
                btn.disabled = false;
            }
        };
        row.appendChild(btn);
        row.appendChild(status);
        wrap.appendChild(row);
        const lu = store.labState && store.labState.last_updated;
        if (lu) {
            const luEl = document.createElement('div');
            luEl.style.fontSize = '9px';
            luEl.style.color = '#64748b';
            luEl.style.marginTop = '6px';
            luEl.textContent = `Lab state last_updated: ${lu}`;
            wrap.appendChild(luEl);
        }
        ctxObserveSlot.appendChild(wrap);
    }

    if (placementState === 'STORED') {
        if (ctxMoveBtn) ctxMoveBtn.style.display = 'none';
        const hint = document.createElement('div');
        hint.className = 'ctx-dynamic-storage';
        hint.style.marginTop = '8px';
        hint.style.fontSize = '10px';
        hint.style.color = '#94a3b8';
        hint.style.lineHeight = '1.35';
        hint.innerHTML =
            'Stored in Q3 at <strong>cell center</strong> and <strong>0°</strong> by default. Use <strong>Drag from storage</strong> or set X/Y/Rot and <strong>Place from storage</strong>.';
        if (selectedCompProperties) selectedCompProperties.appendChild(hint);
    } else {
        if (ctxMoveBtn) ctxMoveBtn.style.display = 'flex';
    }

    if (ctxStrategies) {
        ctxStrategies.innerHTML = '';
        if (placementState !== 'STORED' && store.availableStrategies) {
            Object.entries(store.availableStrategies).forEach(([stratKey, strat]) => {
                const btn = document.createElement('button');
                btn.className = 'btn btn-secondary';
                btn.style.width = '100%';
                btn.style.marginBottom = '4px';
                btn.style.fontSize = '10px';
                btn.style.padding = '6px';
                btn.style.textAlign = 'left';
                btn.innerHTML = `<span class="material-icons-round" style="font-size: 12px; vertical-align: middle;">settings_suggest</span> ${strat.name}`;
                btn.onclick = () => showParameterModal(stratKey, strat);
                ctxStrategies.appendChild(btn);
            });
        }
    }

    if (placementState === 'PLACED' && ctxMoveBtn && ctxMoveBtn.parentNode) {
        const row = document.createElement('div');
        row.className = 'ctx-dynamic-storage';
        row.style.marginTop = '10px';
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-secondary';
        b.style.fontSize = '11px';
        b.style.width = '100%';
        b.innerHTML = '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">inventory_2</span> Move to storage (auto pack)';
        b.onclick = () =>
            sendCommand({ action: 'STORE_COMPONENT', target_id: name, parameters: {} });
        row.appendChild(b);
        ctxMoveBtn.parentNode.insertBefore(row, ctxMoveBtn.nextSibling);
    }

    if (placementState === 'STORED' && ctxMoveBtn && ctxMoveBtn.parentNode) {
        const rowPlace = document.createElement('div');
        rowPlace.className = 'ctx-dynamic-storage';
        rowPlace.style.marginTop = '10px';
        rowPlace.style.display = 'flex';
        rowPlace.style.flexDirection = 'column';
        rowPlace.style.gap = '6px';

        const bPlace = document.createElement('button');
        bPlace.type = 'button';
        bPlace.className = 'btn btn-primary';
        bPlace.style.fontSize = '11px';
        bPlace.style.width = '100%';
        bPlace.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">north_east</span> Place from storage';
        bPlace.onclick = async () => {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            const tx = parseFloat(ctxX.value);
            const ty = parseFloat(ctxY.value);
            const trot = parseFloat(ctxRot.value);
            if (!Number.isFinite(tx) || !Number.isFinite(ty) || !Number.isFinite(trot)) {
                log('Invalid coordinates.', 'error');
                return;
            }
            if (isStorageRegion(tx, ty)) {
                log('Target must be outside the configured storage (inventory) rectangle.', 'error');
                return;
            }
            await sendCommand({
                action: 'PLACE_FROM_STORAGE',
                target_id: name,
                parameters: { target_x: tx, target_y: ty, rotation: trot },
            });
        };
        rowPlace.appendChild(bPlace);

        const bRecenter = document.createElement('button');
        bRecenter.type = 'button';
        bRecenter.className = 'btn btn-secondary';
        bRecenter.style.fontSize = '11px';
        bRecenter.style.width = '100%';
        bRecenter.title = 'Robot moves part to cell center at 0° (standard storage pose).';
        bRecenter.innerHTML =
            '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">center_focus_strong</span> Re-center in cell (0°)';
        bRecenter.onclick = () => {
            store.dragFromStorageTag = null;
            store.dragFromStorageStartPose = null;
            void sendCommand({ action: 'RECENTER_IN_STORAGE', target_id: name, parameters: {} });
        };
        rowPlace.appendChild(bRecenter);

        const bDragFs = document.createElement('button');
        bDragFs.type = 'button';
        bDragFs.className = 'btn btn-secondary';
        bDragFs.style.fontSize = '11px';
        bDragFs.style.width = '100%';
        bDragFs.title =
            'Only this part can be dragged until you place or cancel. Release on the breadboard to confirm placement.';
        if (store.dragFromStorageTag === name) {
            bDragFs.disabled = true;
            bDragFs.style.opacity = '0.95';
            bDragFs.innerHTML =
                '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">pan_tool</span> Drag mode — pull on canvas';
            rowPlace.appendChild(bDragFs);
            const bCancelDrag = document.createElement('button');
            bCancelDrag.type = 'button';
            bCancelDrag.className = 'btn btn-secondary';
            bCancelDrag.style.fontSize = '10px';
            bCancelDrag.style.width = '100%';
            bCancelDrag.textContent = 'Cancel drag-from-storage mode';
            bCancelDrag.onclick = () => {
                store.dragFromStorageTag = null;
                store.dragFromStorageStartPose = null;
                log('Drag from storage mode cancelled.', 'info');
                _render();
                updateContextPanel(name);
            };
            rowPlace.appendChild(bCancelDrag);
        } else {
            bDragFs.innerHTML =
                '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">touch_app</span> Drag from storage';
            bDragFs.onclick = () => {
                store.dragFromStorageTag = name;
                log('Drag mode: only this part can be dragged. Pull it onto the breadboard, release, then confirm.', 'info');
                _render();
                updateContextPanel(name);
            };
            rowPlace.appendChild(bDragFs);
        }

        ctxMoveBtn.parentNode.insertBefore(rowPlace, ctxMoveBtn.nextSibling);
    }

    // --- Motor Controls ---
    const existingMotor = document.getElementById('ctx-motor-controls');
    if (existingMotor) existingMotor.remove();

    if (
        placementState !== 'STORED' &&
        store.catalogMap[name] &&
        store.catalogMap[name].motor_ids &&
        store.catalogMap[name].motor_ids.length > 0 &&
        ctxStrategies && ctxStrategies.parentNode
    ) {
        const motorSection = document.createElement('div');
        motorSection.id = 'ctx-motor-controls';
        motorSection.style.marginTop = '12px';
        motorSection.style.paddingTop = '12px';
        motorSection.style.borderTop = '1px solid #2a2e36';

        motorSection.innerHTML = '<div style="font-size:11px; color:#94a3b8; margin-bottom:8px; font-weight:600;">MOTOR CONTROL (Relative) — θ = server-tracked cumulative angle</div>';

        store.catalogMap[name].motor_ids.forEach((mid) => {
            const block = document.createElement('div');
            block.style.marginBottom = '10px';

            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.flexWrap = 'wrap';
            row.style.gap = '8px';

            const label = document.createElement('span');
            label.textContent = `M${mid}`;
            label.style.fontSize = '12px';
            label.style.color = '#cbd5e1';
            label.style.minWidth = '28px';

            const angleSpan = document.createElement('span');
            angleSpan.id = `ctx-motor-angle-${mid}`;
            angleSpan.style.fontSize = '11px';
            angleSpan.style.color = '#94a3b8';
            angleSpan.style.fontFamily = 'ui-monospace, monospace';
            angleSpan.textContent = 'θ —';

            const input = document.createElement('input');
            input.type = 'number';
            input.value = '100';
            input.style.width = '56px';
            input.style.fontSize = '12px';
            input.style.padding = '6px 8px';
            input.style.background = '#0f1115';
            input.style.border = '1px solid #2a2e36';
            input.style.color = '#fff';
            input.style.borderRadius = '4px';
            input.title = 'Step Size';

            const btnRev = document.createElement('button');
            btnRev.className = 'btn btn-secondary';
            btnRev.style.padding = '6px 10px';
            btnRev.style.fontSize = '12px';
            btnRev.style.width = 'auto';
            btnRev.innerHTML = '<span class="material-icons-round" style="font-size:14px">remove</span>';
            btnRev.title = 'Jog Backward';
            btnRev.onclick = () => moveMotor(name, mid, -parseFloat(input.value));

            const btnFwd = document.createElement('button');
            btnFwd.className = 'btn btn-secondary';
            btnFwd.style.padding = '6px 10px';
            btnFwd.style.fontSize = '12px';
            btnFwd.style.width = 'auto';
            btnFwd.innerHTML = '<span class="material-icons-round" style="font-size:14px">add</span>';
            btnFwd.title = 'Jog Forward';
            btnFwd.onclick = () => moveMotor(name, mid, parseFloat(input.value));

            row.appendChild(label);
            row.appendChild(angleSpan);
            row.appendChild(btnRev);
            row.appendChild(input);
            row.appendChild(btnFwd);
            block.appendChild(row);

            const row2 = document.createElement('div');
            row2.style.display = 'flex';
            row2.style.gap = '8px';
            row2.style.marginTop = '4px';
            row2.style.paddingLeft = '36px';

            const btnHome = document.createElement('button');
            btnHome.type = 'button';
            btnHome.className = 'btn btn-secondary';
            btnHome.style.padding = '4px 10px';
            btnHome.style.fontSize = '10px';
            btnHome.style.width = 'auto';
            btnHome.textContent = 'Send to home';
            btnHome.title = 'Move motor by −θ so tracked angle becomes 0';
            btnHome.onclick = () => motorSendHome(name, mid);

            const btnZero = document.createElement('button');
            btnZero.type = 'button';
            btnZero.className = 'btn btn-secondary';
            btnZero.style.padding = '4px 10px';
            btnZero.style.fontSize = '10px';
            btnZero.style.width = 'auto';
            btnZero.textContent = 'Set 0';
            btnZero.title = 'Define current position as θ = 0 (no move)';
            btnZero.onclick = () => motorSetZero(name, mid);

            row2.appendChild(btnHome);
            row2.appendChild(btnZero);
            block.appendChild(row2);

            motorSection.appendChild(block);
        });

        ctxStrategies.parentNode.appendChild(motorSection);
    }

    renderInAirControlsForContext(name, comp, placementState);

    store.contextPanelStateSnapshot = placementState;
    const hld = getHolding(store.labState);
    store.contextPanelStatusSnapshot = `${(store.labState && store.labState.system_status) || 'IDLE'}|${hld.tag_id || ''}|${hld.requires_operator_confirm ? '1' : '0'}`;
}

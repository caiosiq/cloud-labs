/**
 * Floating component context panel.
 *
 * **Pose surface #2 (X/Y/Rot):** `#ctx-x`, `#ctx-y`, `#ctx-rot` mirror
 * `store.ghostState[selected]`; Move sends MOVE_COMPONENT. Surface #3
 * (TablePose read-only receipt) lives in the capability popup below.
 * See `component-model.js` for the full three-surface map.
 *
 * Renders the right-hand-side panel when the user selects a component on the canvas
 * (or in the inventory sidebar). The panel adapts its content based on:
 *   1. Placement state: PLACED | STORED | INVENTORY (`placementUiLabel`).
 *   2. System status:   IDLE | HOLDING (`holding` + `unconfirmed`).
 *
 * Read-only capability panels and per-primitive forms live in ``component-popup.js`` /
 * ``frontend/js/primitives/``.
 *
 * Dependencies that are still in `app-main.js` (the canvas `render()` plus the
 * `fetchLabState` call site after observing measurables) are injected at boot via
 * `initContextPanel`.
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { sendCommand } from '../api/commands.js';
import { showParameterModal } from './modals.js';
import {
    getHolding,
    isHoldingState,
    isHoldingUnconfirmed,
    isChromeComponent,
    isOffTableComponent,
    isStoredComponent,
    measPose,
} from '../component-model.js';
import { componentDataSnapshot } from '../component-state.js';
import { renderComponentPopup } from './component-popup.js';

const PRIMITIVE_DEV_HINTS =
    typeof window !== 'undefined' &&
    typeof window.location !== 'undefined' &&
    /(?:^|[?&])dev=1(?:&|$)/.test(window.location.search || '');

// Capability panels (TablePose, measurables, primitives) render via component-popup.js.

// DOM refs (resolved lazily so we don't need DI for elements that exist at boot anyway).
function refs() {
    return {
        contextPanel: document.getElementById('context-panel'),
        ctxX: document.getElementById('ctx-x'),
        ctxY: document.getElementById('ctx-y'),
        ctxRot: document.getElementById('ctx-rot'),
        ctxMoveBtn: document.getElementById('ctx-move-btn'),
        ctxRecordSlot: document.getElementById('ctx-record-slot'),
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
    store.contextPanelDataSnapshot = null;
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

/** Refresh tracked motor angle labels in primitive UI regions. */
export function updateMotorAngleLabels(tagId) {
    if (!tagId || !store.labState || !store.labState.components) return;
    const comp = store.labState.components[tagId];
    if (!comp) return;
    const mr = (measPose(comp).motor_rotations) || {};
    document.querySelectorAll(`[data-motor-angle^="${tagId}:"]`).forEach((el) => {
        const mid = (el.dataset.motorAngle || '').split(':')[1];
        if (!mid) return;
        const v = mr[String(mid)];
        const n = (v !== undefined && v !== null && Number.isFinite(Number(v))) ? Number(v) : 0;
        el.textContent = `θ ${n.toFixed(2)}°`;
    });
}

// --- Main: updateContextPanel ---

export function updateContextPanel(name) {
    const {
        contextPanel,
        ctxX,
        ctxY,
        ctxRot,
        ctxMoveBtn,
        ctxRecordSlot,
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

    const chromeOnly = isChromeComponent(name);
    const tablePoseSection = document.getElementById('ctx-table-pose-section');
    const tableMotionSection = document.getElementById('ctx-table-motion-section');
    if (tablePoseSection) tablePoseSection.style.display = 'none';
    if (tableMotionSection) tableMotionSection.style.display = 'none';

    document.querySelectorAll('.ctx-dynamic-storage').forEach((el) => el.remove());

    const placementState = placementUiLabel(comp);
    if (pose && Number.isFinite(pose.x)) {
        ctxX.value = pose.x.toFixed(1);
        ctxY.value = pose.y.toFixed(1);
        ctxRot.value = (pose.rotation || 0).toFixed(1);
    } else {
        ctxX.value = '';
        ctxY.value = '';
        ctxRot.value = '';
    }

    if (ctxRecordSlot) {
        ctxRecordSlot.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.style.marginTop = chromeOnly ? '0' : '8px';
        if (chromeOnly) {
            const hint = document.createElement('p');
            hint.style.fontSize = '9px';
            hint.style.color = '#64748b';
            hint.style.lineHeight = '1.35';
            hint.style.margin = '0 0 8px 0';
            hint.textContent =
                'Fixed bench component — not placed on the table. Use the chrome bar above the canvas for quick access.';
            wrap.appendChild(hint);
        }
        wrap.appendChild(
            renderComponentPopup(name, {
                placementState,
                checkCollision: _checkCollision,
                render: _render,
                updateContextPanel,
                showParameterModal,
            }),
        );
        if (PRIMITIVE_DEV_HINTS) {
            const dev = document.createElement('div');
            dev.style.fontSize = '9px';
            dev.style.color = '#475569';
            dev.style.marginTop = '6px';
            dev.innerHTML =
                'Dev: <code>POST /api/components/{tag}/measurables/record</code>';
            wrap.appendChild(dev);
        }
        ctxRecordSlot.appendChild(wrap);
    }

    if (!chromeOnly && placementState === 'STORED') {
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
    } else if (!chromeOnly) {
        if (ctxMoveBtn) ctxMoveBtn.style.display = 'flex';
    }

    if (ctxStrategies) {
        ctxStrategies.innerHTML = '';
        ctxStrategies.style.display = 'none';
    }

    store.contextPanelStateSnapshot = placementState;
    const hld = getHolding(store.labState);
    store.contextPanelStatusSnapshot = `${(store.labState && store.labState.system_status) || 'IDLE'}|${hld.tag_id || ''}|${hld.requires_operator_confirm ? '1' : '0'}`;
    if (comp) {
        store.contextPanelDataSnapshot = componentDataSnapshot(comp);
    }
}

/**
 * Multi-panel dock manager.
 *
 * Owns ``#panel-dock`` — the floating row of ``.component-panel`` elements
 * over the canvas. The dock supports:
 *   - **Replace semantics** (default): plain click on a component swaps
 *     every open panel with just that one (matches the historical
 *     single-panel behaviour).
 *   - **Add semantics**: ``openPanel(tag, { add: true })`` (driven by
 *     Ctrl/Cmd+click) appends another panel to the dock without closing
 *     the existing ones. With the dock's ``flex-direction: row-reverse``
 *     CSS, the newest panel appears visually to the LEFT of older ones.
 *   - **Focus**: only the focused panel captures interactive clicks
 *     (CSS gates the others with ``pointer-events: none`` on controls).
 *     Unfocused panels remain visible and scrollable so live camera
 *     feeds keep streaming.
 *
 * Public API:
 *   - ``openPanel(tagId, { add })`` — open or focus.
 *   - ``focusPanel(tagId)`` — change focus only (panel must be open).
 *   - ``closePanel(tagId)`` — remove a single panel.
 *   - ``closeAllPanels()`` — wipe the dock.
 *   - ``updateContextPanel(tagId)`` — refresh-if-open. **Does not** open
 *     a new panel; legacy callers paired this with
 *     ``store.selectedComponent = tag`` (which the compat shim in
 *     ``state/store.js`` maps to ``openPanels = [tag]``), so the pair
 *     still effectively means "open and refresh".
 *   - ``clearSelectionAndHideContextPanel()`` — empty-canvas click;
 *     keeps panels open and only clears focus (less destructive than the
 *     single-panel behaviour, matching the operator-requested default).
 *
 * Pose editing lives in the canvas ghost + in-air / TeleOp primitives at
 * the bottom of each panel (see ``ui/component-popup.js``).
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { showParameterModal } from './modals.js';
import {
    getHolding,
    isHoldingState,
    isOffTableComponent,
    isStoredComponent,
} from '../component-model.js';
import { componentDataSnapshot, getTunables } from '../component-state.js';
import { renderComponentPanel } from './component-popup.js';
import { syncComponentSidebarHighlights } from './updateUI.js';
import { hideSelectedPartTab, showSelectedPartTab } from './workspace-tabs.js';

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
    _installDockClickDelegate();
}

/** Legacy-style label for context panel / drag rules (PLACED | STORED | INVENTORY). */
export function placementUiLabel(comp) {
    if (!comp) return 'PLACED';
    if (isStoredComponent(comp)) return 'STORED';
    if (isOffTableComponent(comp)) return 'INVENTORY';
    return 'PLACED';
}

// ---------- panel-dock API ----------

/**
 * Open a panel for ``tagId``.
 *
 * - ``add=false`` (default): closes every other open panel first, then
 *   mounts ``tagId`` and focuses it. Matches the historical single-panel
 *   "click replaces" behaviour.
 * - ``add=true``: keeps existing panels open, appends ``tagId`` (or focuses
 *   it if already open). Driven by Ctrl/Cmd+click. With the dock's
 *   ``row-reverse`` styling the new panel appears to the LEFT of the
 *   existing ones.
 *
 * Always focuses ``tagId`` after the mount so the user can immediately
 * interact with the newly-opened panel.
 *
 * @param {string} tagId
 * @param {{ add?: boolean }} [opts]
 */
export function openPanel(tagId, _opts = {}) {
    if (!tagId) return;
    const toClose = store.openPanels.filter((t) => t !== tagId);
    toClose.forEach((t) => _removePanelDom(t));
    store.openPanels = store.openPanels.filter((t) => t === tagId);
    const stale = [...store.contextPanelSnapshots.keys()].filter((k) => k !== tagId);
    stale.forEach((k) => store.contextPanelSnapshots.delete(k));
    if (!store.openPanels.includes(tagId)) {
        store.openPanels.push(tagId);
    }
    _mountOrRebuildPanel(tagId);
    focusPanel(tagId);
    showSelectedPartTab();
    _render();
}

/**
 * Change which open panel is focused. No-op if ``tagId`` is not open.
 * @param {string | null} tagId
 */
export function focusPanel(tagId) {
    if (tagId == null) {
        if (store.focusedPanel == null) return;
        store.focusedPanel = null;
        _applyFocusStyling();
        _render();
        return;
    }
    if (!store.openPanels.includes(tagId)) return;
    if (store.focusedPanel === tagId) {
        _applyFocusStyling();
        return;
    }
    store.focusedPanel = tagId;
    _applyFocusStyling();
    _render();
}

/**
 * Close a single panel. If it was the focused one, focus falls back to the
 * next-most-recently-opened panel (or ``null`` if none remain).
 * @param {string} tagId
 */
export function closePanel(tagId) {
    if (!tagId || !store.openPanels.includes(tagId)) return;
    _removePanelDom(tagId);
    store.openPanels = store.openPanels.filter((t) => t !== tagId);
    store.contextPanelSnapshots.delete(tagId);
    if (store.focusedPanel === tagId) {
        store.focusedPanel = store.openPanels[store.openPanels.length - 1] || null;
        _applyFocusStyling();
    }
    if (store.openPanels.length === 0) hideSelectedPartTab();
    _render();
}

/** Close every open panel and clear all per-tag snapshot state. */
export function closeAllPanels() {
    store.openPanels.slice().forEach((t) => _removePanelDom(t));
    store.openPanels = [];
    store.focusedPanel = null;
    store.contextPanelSnapshots.clear();
    _panelScrollMemory.clear();
    store.dragFromStorageTag = null;
    store.dragFromStorageStartPose = null;
    hideSelectedPartTab();
    _render();
}

/**
 * Legacy "empty-canvas click" handler. Multi-panel default: keep panels
 * open, only clear focus. The operator can explicitly close panels via
 * the X button on each one.
 */
export function clearSelectionAndHideContextPanel() {
    // Empty-canvas clicks intentionally leave the latest Selected Part open.
}

/**
 * Refresh the panel for ``tagId`` if it is currently open. Does NOT open a
 * panel — legacy call sites that wanted "open and show" set
 * ``store.selectedComponent = tag`` first (which the compat shim maps to
 * ``openPanels = [tag]; focusedPanel = tag``) and then called this.
 *
 * Also writes the latest snapshot for ``tagId`` so the lab-state poller's
 * change-detection compares against fresh values.
 *
 * @param {string} tagId
 */
export function updateContextPanel(tagId) {
    if (!tagId) return;
    if (!store.openPanels.includes(tagId)) return;
    _mountOrRebuildPanel(tagId);
    _applyFocusStyling();
}

/** Refresh tracked motor angle labels in primitive UI regions. */
export function updateMotorAngleLabels(tagId) {
    if (!tagId || !store.labState || !store.labState.components) return;
    const comp = store.labState.components[tagId];
    if (!comp) return;
    const mr = getTunables(comp).nominal_motor_positions || {};
    document.querySelectorAll(`[data-motor-angle^="${tagId}:"]`).forEach((el) => {
        const mid = (el.dataset.motorAngle || '').split(':')[1];
        if (!mid) return;
        const v = mr[String(mid)];
        const n = (v !== undefined && v !== null && Number.isFinite(Number(v))) ? Number(v) : 0;
        el.textContent = `θ ${n.toFixed(2)}°`;
    });
}

// ---------- holding banner (unchanged behaviour) ----------

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

// Per-tag scroll position preserved across panel DOM rebuilds (full
// ``replaceWith`` would otherwise reset ``overflow-y: auto`` to the top).
const _panelScrollMemory = new Map();

// ---------- internal: DOM plumbing ----------

function _getDock() {
    return document.getElementById('panel-dock');
}

/**
 * Mount (or rebuild) the panel DOM for ``tagId``, then refresh its snapshot
 * entry. With the dock's ``row-reverse`` styling, ``appendChild`` places the
 * newly-mounted panel visually to the LEFT of any existing panels — which
 * matches the user-requested "Ctrl+click adds a window to the left".
 */
function _mountOrRebuildPanel(tagId) {
    const dock = _getDock();
    if (!dock) return;

    const comp =
        (store.labState && store.labState.components && store.labState.components[tagId]) || null;
    const placement = placementUiLabel(comp);

    const fresh = renderComponentPanel(tagId, {
        placementState: placement,
        checkCollision: _checkCollision,
        render: _render,
        updateContextPanel,
        showParameterModal,
        closePanel,
    });

    const existing = dock.querySelector(
        `.component-panel[data-tag-id="${_cssEscape(tagId)}"]`,
    );
    let scrollState = _panelScrollMemory.get(tagId) || { top: 0, left: 0 };
    if (existing) {
        scrollState = { top: existing.scrollTop, left: existing.scrollLeft };
        _panelScrollMemory.set(tagId, scrollState);
    }
    if (existing) {
        existing.replaceWith(fresh);
    } else {
        dock.appendChild(fresh);
    }
    _attachPanelScrollMemory(fresh);
    _restorePanelScroll(fresh, scrollState);

    const hld = getHolding(store.labState);
    const statusKey = `${(store.labState && store.labState.system_status) || 'IDLE'}|${hld.tag_id || ''}|${hld.requires_operator_confirm ? '1' : '0'}`;
    const dataKey = comp ? componentDataSnapshot(comp) : null;
    store.contextPanelSnapshots.set(tagId, {
        state: placement,
        status: statusKey,
        data: dataKey,
    });
}

function _removePanelDom(tagId) {
    const dock = _getDock();
    if (!dock) return;
    const el = dock.querySelector(
        `.component-panel[data-tag-id="${_cssEscape(tagId)}"]`,
    );
    if (el) el.remove();
    _panelScrollMemory.delete(tagId);
}

/** Remember scroll position while the operator reads long primitive lists. */
function _attachPanelScrollMemory(panel) {
    const tag = panel && panel.dataset && panel.dataset.tagId;
    if (!tag || panel.__scrollMemoryAttached) return;
    panel.__scrollMemoryAttached = true;
    panel.addEventListener(
        'scroll',
        () => {
            _panelScrollMemory.set(tag, {
                top: panel.scrollTop,
                left: panel.scrollLeft,
            });
        },
        { passive: true },
    );
}

function _restorePanelScroll(panel, scrollState) {
    if (!panel || !scrollState) return;
    const apply = () => {
        panel.scrollTop = scrollState.top;
        panel.scrollLeft = scrollState.left;
    };
    apply();
    requestAnimationFrame(apply);
}

function _applyFocusStyling() {
    const dock = _getDock();
    if (!dock) return;
    const focused = store.focusedPanel;
    dock.querySelectorAll('.component-panel').forEach((el) => {
        const tag = el.dataset.tagId;
        el.classList.toggle('component-panel--focused', tag != null && tag === focused);
    });
    syncComponentSidebarHighlights();
}

/**
 * Install a single delegated click handler on ``#panel-dock``. Any click
 * that bubbles up from inside a ``.component-panel`` shifts focus to that
 * panel. The close button on each panel calls ``stopPropagation`` so it
 * dismisses without first focusing.
 *
 * On the focused panel, inner controls (buttons, inputs) work normally —
 * their click bubbles up here too, but the ``tag === focused`` early-return
 * makes it a cheap no-op.
 */
function _installDockClickDelegate() {
    const dock = _getDock();
    if (!dock || dock.__dockDelegateInstalled) return;
    dock.__dockDelegateInstalled = true;
    dock.addEventListener('click', (ev) => {
        const panel = ev.target.closest('.component-panel');
        if (!panel) return;
        const tag = panel.dataset.tagId;
        if (!tag || tag === store.focusedPanel) return;
        focusPanel(tag);
    });
}

/**
 * CSS.escape polyfill — we use the tag id inside an attribute selector and
 * legitimate ids (e.g. ``tag_22``) contain underscores; modern browsers
 * support ``CSS.escape`` natively but fall back to a conservative literal
 * for ASCII letters / digits / underscore / hyphen which covers every tag
 * id our backend generates today.
 */
function _cssEscape(s) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
        return CSS.escape(s);
    }
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
}

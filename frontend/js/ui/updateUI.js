/**
 * `updateUI()` — the "I just refetched lab state, now repaint everything" routine.
 *
 * This is the single entry-point that every poll tick calls after mirroring `store.labState`. It
 * is intentionally side-effect-heavy: it updates the system status badge, rebuilds the inventory
 * sidebar from scratch, refreshes the table-cam chrome and motor labels, runs the layout-warning
 * banner sync, and finally requests a canvas redraw.
 *
 * Callbacks for the still-in-app-main pieces (context panel, holding banner, motor labels, the
 * canvas `render()` itself) are injected via `initUpdateUI` so this file stays decoupled.
 */
import { store } from '../state/store.js';
import {
    getHolding,
    isHeldTag,
    isOnTableComponent,
    isOptimizedPlacement,
    isStoredComponent,
} from '../component-model.js';
import { getComponentIcon } from './icons.js';
import { updateLayoutWarningBanner } from './layout-conflicts.js';
import { syncTableCamMockHint, updateTableCamMockPreviewChrome } from '../table-cam/panel.js';

let _deps = {
    placementUiLabel: () => 'PLACED',
    updateContextPanel: () => {},
    updateMotorAngleLabels: () => {},
    updateHoldingBanner: () => {},
    render: () => {},
};

/**
 * @param {{
 *   placementUiLabel: (comp: any) => string,
 *   updateContextPanel: (tagId: string) => void,
 *   updateMotorAngleLabels: (tagId: string) => void,
 *   updateHoldingBanner: () => void,
 *   render: () => void,
 * }} deps
 */
export function initUpdateUI(deps) {
    _deps = { ..._deps, ...deps };
}

export function updateUI() {
    if (!store.labState) return;

    const statusBadge = document.getElementById('system-status-badge');
    const componentList = document.getElementById('component-list');

    const status = store.labState.system_status;
    let badgeClass = 'active';
    let badgeColor = 'placed';
    let badgeStyle = '';
    let badgeSuffix = '';

    if (status === 'BUSY') {
        badgeClass = '';
        badgeColor = 'inventory';
        badgeStyle = 'background-color: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.4);';
    } else if (status === 'OPTIMIZING') {
        badgeClass = '';
        badgeColor = 'placed';
        badgeStyle = 'background-color: #10b981; box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);';
    } else if (status === 'HOLDING') {
        badgeClass = '';
        badgeColor = '';
        const hld = getHolding(store.labState);
        // UNCONFIRMED HOLDING is a special red state: the gripper closed but the operator needs
        // to acknowledge before the next motion proceeds.
        if (hld.requires_operator_confirm) {
            badgeStyle = 'background-color: #ef4444; box-shadow: 0 0 8px rgba(239, 68, 68, 0.5);';
            badgeSuffix = ' · UNCONFIRMED';
        } else {
            badgeStyle = 'background-color: #a855f7; box-shadow: 0 0 8px rgba(168, 85, 247, 0.45);';
            badgeSuffix = hld.tag_id ? ` · ${hld.tag_id}` : '';
        }
    }

    if (statusBadge) {
        statusBadge.className = `system-status ${badgeClass}`;
        statusBadge.innerHTML = `<span class="status-dot ${badgeColor}" style="${badgeStyle}"></span> ${status}${badgeSuffix}`;
    }

    _deps.updateHoldingBanner();

    if (!componentList) return;
    componentList.innerHTML = '';

    const components = store.labState.components || {};
    const placedCount = Object.keys(components).length;
    if (placedCount === 0) {
        componentList.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b; font-size: 11px;">No components placed.</div>';
    }

    Object.entries(components).forEach(([name, comp]) => {
        const card = document.createElement('div');
        card.className = 'component-card';
        if (name === store.selectedComponent) card.style.borderColor = '#3b82f6';

        const isPlaced = isOnTableComponent(comp);

        // Resolve display name from catalog using Tag ID; fall back to "Unknown (id)" with a
        // yellow accent so the operator notices catalog mismatches.
        let displayName = name;
        let displayType = comp.type;
        let unknownTag = false;

        if (store.catalogMap[comp.id]) {
            displayName = store.catalogMap[comp.id].name;
        } else {
            displayName = `Unknown (${comp.id})`;
            unknownTag = true;
        }

        const icon = getComponentIcon(comp.type);

        // Sidebar status-dot priority ladder (highest wins):
        //   1. HOLDING (this tag is in the gripper right now)   — purple
        //   2. OPTIMIZED (current placement came from a strategy)— green + gold halo
        //   3. STORED (Q3 storage region)                       — indigo
        //   4. PLACED on breadboard                             — green
        //   5. OFF_TABLE inventory                              — blue
        // Note: `hasOptimizationOutcome` alone is NOT enough for "optimized" styling —
        // `isOptimizedPlacement` also requires the current `placement.mode` to be a strategy name
        // (i.e. the part hasn't been manually re-moved since the optimizer ran). Hovering tooltip
        // shows the raw placement label (MANUAL / COBYLA / HOVER / etc.) for detail.
        let statusDot;
        if (isHeldTag(name, store.labState)) {
            statusDot = `<div class="status-dot holding" title="Held by gripper"></div>`;
        } else if (isOptimizedPlacement(comp)) {
            statusDot = `<div class="status-dot optimized" title="Optimized (${_deps.placementUiLabel(comp)})"></div>`;
        } else if (isStoredComponent(comp)) {
            statusDot = `<div class="status-dot stored" title="Stored (Q3)"></div>`;
        } else {
            statusDot = `<div class="status-dot ${isPlaced ? 'placed' : 'inventory'}" title="${_deps.placementUiLabel(comp)}"></div>`;
        }

        let motorBadge = '';
        if (
            store.catalogMap[comp.id] &&
            store.catalogMap[comp.id].motor_ids &&
            store.catalogMap[comp.id].motor_ids.length > 0
        ) {
            motorBadge = `<span class="material-icons-round" style="font-size: 12px; color: #f59e0b; margin-right: 4px;" title="Motorized">settings_input_component</span>`;
        }

        card.innerHTML = `
            <div class="comp-icon material-icons-round">${icon}</div>
            <div class="comp-info">
                <span class="comp-name" style="${unknownTag ? 'color: #f59e0b;' : ''}">${displayName}</span>
                <span class="comp-meta">${motorBadge}${displayType.replace('OPTICAL_', '')} • ${comp.id}</span>
            </div>
            ${statusDot}
        `;

        card.addEventListener('click', () => {
            store.selectedComponent = name;
            _deps.updateContextPanel(name);
            _deps.render();
        });

        componentList.appendChild(card);
    });

    syncTableCamMockHint();
    updateTableCamMockPreviewChrome();
    _deps.updateMotorAngleLabels(store.selectedComponent);
    updateLayoutWarningBanner();
    _deps.render();
}

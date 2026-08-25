/**
 * `updateUI()` — refresh chrome after lab state / catalog changes.
 *
 * The component sidebar is rebuilt only when its snapshot changes (not every
 * 500 ms poll). While the pointer is over the list, rebuilds are deferred so
 * hover and clicks stay stable.
 */
import { store } from '../state/store.js';
import { isLabInitReady } from '../state/lab-state.js';
import {
    getCatalogRow,
    getHolding,
    isComponentControlled,
    isHeldTag,
    isOnTableComponent,
    isOptimizedPlacement,
    formatOptimizationLabel,
    getOptimizationDisplayInfo,
    isStoredComponent,
    physicalMountLabel,
} from '../component-model.js';
import { maybeRefreshBenchChromeBar } from './bench-chrome-bar.js';
import {
    reconcileLiveFeedPopouts,
    syncLiveFeedSessionChrome,
} from './live-feed-popout.js';
import { getComponentIcon } from './icons.js';
import { updateLayoutWarningBanner } from './layout-conflicts.js';
import { createTrackToggleButton, listLibraryOnlyTags } from './inventory-add.js';
import {
    getOptimizationHighlightForTag,
    optimizationHighlightColor,
    isOptimizationPlanningActive,
} from '../state/optimization-builder.js';

let _deps = {
    placementUiLabel: () => 'PLACED',
    updateContextPanel: () => {},
    updateMotorAngleLabels: () => {},
    updateHoldingBanner: () => {},
    render: () => {},
    openPanel: () => {},
    fetchLabState: null,
    log: () => {},
};

let _lastSidebarSnapshot = '';
let _sidebarPointerInside = false;
let _sidebarPendingRebuild = false;
let _sidebarInteractionInit = false;

/**
 * @param {{
 *   placementUiLabel: (comp: any) => string,
 *   updateContextPanel: (tagId: string) => void,
 *   updateMotorAngleLabels: (tagId: string) => void,
 *   updateHoldingBanner: () => void,
 *   render: () => void,
 *   openPanel: (tagId: string, opts?: { add?: boolean }) => void,
 * }} deps
 */
export function initUpdateUI(deps) {
    _deps = { ..._deps, ...deps };
}

/** Defer sidebar DOM rebuilds while the operator hovers the inventory list. */
export function initComponentSidebarInteraction() {
    if (_sidebarInteractionInit) return;
    const list = document.getElementById('component-list');
    if (!list) return;
    _sidebarInteractionInit = true;
    list.addEventListener('pointerenter', () => {
        _sidebarPointerInside = true;
    });
    list.addEventListener('pointerleave', () => {
        _sidebarPointerInside = false;
        if (_sidebarPendingRebuild) {
            _sidebarPendingRebuild = false;
            rebuildComponentSidebar();
            syncComponentSidebarHighlights();
        }
    });
}

function sidebarCardContentKey(tagId, comp, { libraryOnlyCard = false } = {}) {
    const controlled = isComponentControlled(tagId, { libraryOnly: libraryOnlyCard });
    const catalogRow = getCatalogRow(tagId);
    const displayName =
        catalogRow && catalogRow.name ? catalogRow.name : `Unknown (${comp?.id || tagId})`;
    const parts = [
        controlled ? '1' : '0',
        isHeldTag(tagId, store.labState) ? '1' : '0',
        comp && isOptimizedPlacement(comp, tagId) ? '1' : '0',
        (() => {
            const info = getOptimizationDisplayInfo(comp, tagId);
            return info ? formatOptimizationLabel(info) : '';
        })(),
        comp && isStoredComponent(comp) ? '1' : '0',
        comp && isOnTableComponent(comp) ? '1' : '0',
        comp ? _deps.placementUiLabel(comp) : '',
        physicalMountLabel(tagId, comp),
        displayName,
        comp?.type || catalogRow?.type || 'UNKNOWN',
        catalogRow?.motor_ids?.length ? '1' : '0',
    ];
    return parts.join(':');
}

function computeSidebarSnapshot() {
    const components = store.labState?.components || {};
    const libraryOnly = listLibraryOnlyTags();
    const runtimeTags = Object.keys(components).sort();
    const hld = getHolding(store.labState);
    const parts = [
        runtimeTags.join(','),
        libraryOnly.join(','),
        (store.activeCatalogTags || []).join(','),
        store.openPanels.join(','),
        store.focusedPanel || '',
        `${hld.tag_id || ''}|${hld.requires_operator_confirm ? '1' : '0'}`,
    ];
    runtimeTags.forEach((tagId) => {
        parts.push(`${tagId}=${sidebarCardContentKey(tagId, components[tagId])}`);
    });
    libraryOnly.forEach((tagId) => {
        parts.push(
            `${tagId}=lib:${sidebarCardContentKey(tagId, null, { libraryOnlyCard: true })}`,
        );
    });
    return parts.join('|');
}

/** Cheap pass: panel open/focus borders without tearing down card nodes. */
export function syncComponentSidebarHighlights() {
    const list = document.getElementById('component-list');
    if (!list) return;
    list.querySelectorAll('.component-card[data-tag-id]').forEach((card) => {
        const tagId = card.dataset.tagId;
        card.classList.remove(
            'opt-plan-scope',
            'opt-plan-objective',
            'opt-plan-variable',
            'opt-plan-both',
        );
        if (isOptimizationPlanningActive()) {
            const role = getOptimizationHighlightForTag(tagId);
            if (role) {
                card.classList.add(`opt-plan-${role}`);
                card.style.borderColor = optimizationHighlightColor(role);
                card.style.boxShadow = `0 0 10px ${optimizationHighlightColor(role)}55`;
            } else {
                card.style.borderColor = '';
                card.style.boxShadow = '';
            }
        } else if (store.openPanels.includes(tagId)) {
            card.style.borderColor = 'rgba(59, 130, 246, 0.45)';
            card.style.boxShadow = '';
        } else {
            card.style.borderColor = '';
            card.style.boxShadow = '';
        }
        if (tagId === store.focusedPanel) {
            card.style.borderColor = '#3b82f6';
        }
    });
}

function rebuildComponentSidebar() {
    const componentList = document.getElementById('component-list');
    if (!componentList) return;

    componentList.innerHTML = '';

    const components = store.labState.components || {};
    const libraryOnly = listLibraryOnlyTags();
    const placedCount = Object.keys(components).length;
    if (placedCount === 0 && libraryOnly.length === 0) {
        componentList.innerHTML =
            '<div style="padding: 20px; text-align: center; color: #64748b; font-size: var(--text-sm);">No components placed.</div>';
        _lastSidebarSnapshot = computeSidebarSnapshot();
        return;
    }

    const appendComponentCard = (name, comp, { libraryOnlyCard = false } = {}) => {
        const card = document.createElement('div');
        card.className = 'component-card';
        card.dataset.tagId = name;

        if (store.openPanels.includes(name)) {
            card.style.borderColor = 'rgba(59, 130, 246, 0.45)';
        }
        if (name === store.focusedPanel) {
            card.style.borderColor = '#3b82f6';
        }

        const isPlaced = comp ? isOnTableComponent(comp) : false;
        const controlled = isComponentControlled(name, { libraryOnly: libraryOnlyCard });

        let displayName = name;
        let displayType = comp?.type || getCatalogRow(name)?.type || 'UNKNOWN';
        let unknownTag = false;

        const catalogRow = getCatalogRow(name) || (comp && getCatalogRow(comp.id));
        if (catalogRow && catalogRow.name) {
            displayName = catalogRow.name;
        } else {
            displayName = `Unknown (${comp?.id || name})`;
            unknownTag = true;
        }

        const icon = getComponentIcon(displayType);
        const mount = physicalMountLabel(name, comp);

        let statusDot;
        if (isHeldTag(name, store.labState)) {
            statusDot = `<div class="status-dot holding" title="Held by gripper"></div>`;
        } else if (isOptimizedPlacement(comp, name)) {
            const optLabel = formatOptimizationLabel(getOptimizationDisplayInfo(comp, name));
            statusDot = `<div class="status-dot optimized" title="Optimized (${optLabel})"></div>`;
        } else if (isStoredComponent(comp)) {
            statusDot = `<div class="status-dot stored" title="Stored (Q3)"></div>`;
        } else if (libraryOnlyCard) {
            statusDot = '<div class="status-dot inventory" title="In library"></div>';
        } else if (!controlled) {
            statusDot = '<div class="status-dot inventory" title="Not controlled"></div>';
        } else {
            const label = comp?.statecontrol ? _deps.placementUiLabel(comp) : 'INVENTORY';
            statusDot = `<div class="status-dot ${isPlaced ? 'placed' : 'inventory'}" title="${label}"></div>`;
        }

        const controlBadge = controlled
            ? '<span class="inv-badge inv-badge--tracked">Controlled</span>'
            : '<span class="inv-badge inv-badge--idle">Not controlled</span>';
        const mountBadge = libraryOnlyCard
            ? '<span class="inv-badge inv-badge--mount">In library</span>'
            : `<span class="inv-badge inv-badge--mount">${mount}</span>`;

        let motorBadge = '';
        if (catalogRow && catalogRow.motor_ids && catalogRow.motor_ids.length > 0) {
            motorBadge =
                '<span class="material-icons-round" style="font-size: var(--text-sm); color: #f59e0b; margin-right: 4px;" title="Motorized">settings_input_component</span>';
        }

        let optBadge = '';
        const optInfo = getOptimizationDisplayInfo(comp, name);
        if (optInfo) {
            optBadge = `<span class="inv-badge inv-badge--optimized" title="Optimization score">${formatOptimizationLabel(optInfo)}</span>`;
        }

        card.innerHTML = `
            <div class="comp-icon material-icons-round">${icon}</div>
            <div class="comp-info">
                <span class="comp-name" style="${unknownTag ? 'color: #f59e0b;' : ''}">${displayName}</span>
                <span class="comp-meta">${controlBadge}${mountBadge}${optBadge}${motorBadge}${displayType.replace('OPTICAL_', '')} • ${name}</span>
            </div>
            ${statusDot}
        `;

        card.appendChild(createTrackToggleButton(name, controlled));

        if (!libraryOnlyCard) {
            card.addEventListener('click', (ev) => {
                const add = !!(ev.ctrlKey || ev.metaKey);
                _deps.openPanel(name, { add });
            });
        } else {
            card.classList.add('library-only');
            card.style.cursor = 'default';
        }

        componentList.appendChild(card);
    };

    Object.entries(components).forEach(([name, comp]) => {
        appendComponentCard(name, comp);
    });

    if (libraryOnly.length > 0) {
        const header = document.createElement('div');
        header.className = 'inventory-section-label';
        header.textContent = 'In library';
        componentList.appendChild(header);

        libraryOnly.forEach((tagId) => {
            const row = getCatalogRow(tagId) || {};
            appendComponentCard(
                tagId,
                { id: tagId, type: row.type || 'UNKNOWN' },
                { libraryOnlyCard: true },
            );
        });
    }

    _lastSidebarSnapshot = computeSidebarSnapshot();
}

function maybeRebuildComponentSidebar({ force = false } = {}) {
    const snapshot = computeSidebarSnapshot();
    if (!force && snapshot === _lastSidebarSnapshot) {
        syncComponentSidebarHighlights();
        return;
    }
    if (!force && _sidebarPointerInside) {
        _sidebarPendingRebuild = true;
        syncComponentSidebarHighlights();
        return;
    }
    _sidebarPendingRebuild = false;
    rebuildComponentSidebar();
}

/**
 * @param {{ forceSidebar?: boolean }} [opts]
 */
export function updateUI(opts = {}) {
    if (!store.labState) return;
    // Catalog / other callers must not build sidebar or canvas before SYNC ready.
    if (!isLabInitReady()) return;

    const statusBadge = document.getElementById('system-status-badge');
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
    } else if (status === 'TELEOP') {
        badgeClass = '';
        badgeColor = '';
        badgeStyle = 'background-color: #0891b2; box-shadow: 0 0 8px rgba(34, 211, 238, 0.45);';
    } else if (status === 'HOLDING') {
        badgeClass = '';
        badgeColor = '';
        const hld = getHolding(store.labState);
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
    maybeRebuildComponentSidebar({ force: !!opts.forceSidebar });
    maybeRefreshBenchChromeBar();
    reconcileLiveFeedPopouts(store.labState);
    syncLiveFeedSessionChrome({
        fetchLabState: _deps.fetchLabState || undefined,
        log: _deps.log,
    });
    _deps.updateMotorAngleLabels(store.selectedComponent);
    updateLayoutWarningBanner();
    _deps.render();
}

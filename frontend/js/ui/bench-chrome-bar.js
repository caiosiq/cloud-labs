/**
 * Chrome bar above the optical table canvas: fixed bench components
 * (tunables but no TablePose — table-top camera, future laser sources).
 *
 * Rebuilds are snapshot-gated (not every lab-state poll) and deferred while
 * the pointer is over the bench header so clicks on camera / laser controls
 * are not torn down mid-interaction.
 */
import { store } from '../state/store.js';
import {
    getCatalogRow,
    listChromeComponentTags,
} from '../component-model.js';
import { getComponentIcon } from './icons.js';
import {
    getOptimizationHighlightForTag,
    optimizationHighlightColor,
    isOptimizationPlanningActive,
} from '../state/optimization-builder.js';

let _onSelect = () => {};
let _lastSnapshot = '';
let _benchHeaderPointerInside = false;
let _pendingRebuild = false;
let _interactionInit = false;
const _pointerLeaveHandlers = [];

/** Register a callback to run when the pointer leaves the bench header strip. */
export function onBenchHeaderPointerLeave(handler) {
    if (typeof handler === 'function') _pointerLeaveHandlers.push(handler);
}

export function isBenchHeaderHovered() {
    return _benchHeaderPointerInside;
}

/**
 * @param {{ onSelect: (tagId: string, opts?: { add?: boolean }) => void }} deps
 *   ``onSelect`` is invoked with the clicked chrome tag and the
 *   add/replace intent (Ctrl/Cmd+click → add a panel without closing
 *   the existing ones; plain click → legacy "replace").
 */
export function initBenchChromeBar(deps) {
    if (deps && typeof deps.onSelect === 'function') _onSelect = deps.onSelect;
}

/** Defer chrome-bar DOM rebuilds while the operator uses the bench header strip. */
export function initBenchChromeBarInteraction() {
    if (_interactionInit) return;
    const header = document.getElementById('bench-header');
    if (!header) return;
    _interactionInit = true;
    header.addEventListener('pointerenter', () => {
        _benchHeaderPointerInside = true;
    });
    header.addEventListener('pointerleave', () => {
        _benchHeaderPointerInside = false;
        if (_pendingRebuild) {
            _pendingRebuild = false;
            rebuildBenchChromeBar();
        }
        _pointerLeaveHandlers.forEach((handler) => {
            try {
                handler();
            } catch (e) {
                console.warn('[bench-chrome-bar] pointer-leave handler failed', e);
            }
        });
    });
}

function computeBenchChromeSnapshot() {
    const tags = listChromeComponentTags(store.labState);
    const parts = [tags.join(',')];
    tags.forEach((tagId) => {
        const comp = store.labState?.components?.[tagId];
        const row = getCatalogRow(tagId);
        const displayName = (row && row.name) || tagId;
        parts.push(`${tagId}=${displayName}:${comp?.type || ''}:${getComponentIcon(comp?.type)}`);
    });
    return parts.join('|');
}

/** Update active styling without recreating buttons. */
export function syncBenchChromeHighlights() {
    const bar = document.getElementById('bench-chrome-bar');
    if (!bar) return;
    bar.querySelectorAll('.bench-chrome-bar__btn[data-tag-id]').forEach((btn) => {
        const tagId = btn.dataset.tagId;
        const active = tagId === store.focusedPanel;
        btn.classList.toggle('bench-chrome-bar__btn--active', active);
        btn.classList.remove(
            'opt-plan-scope',
            'opt-plan-objective',
            'opt-plan-variable',
            'opt-plan-both',
        );
        btn.style.borderColor = '';
        btn.style.boxShadow = '';
        if (isOptimizationPlanningActive()) {
            const role = getOptimizationHighlightForTag(tagId);
            if (role) {
                btn.classList.add(`opt-plan-${role}`);
                const color = optimizationHighlightColor(role);
                btn.style.borderColor = color;
                btn.style.boxShadow = `0 0 8px ${color}66`;
            }
        }
    });
}

function rebuildBenchChromeBar() {
    const bar = document.getElementById('bench-chrome-bar');
    if (!bar) return;

    const tags = listChromeComponentTags(store.labState);
    bar.innerHTML = '';
    if (!tags.length) {
        bar.hidden = true;
        _lastSnapshot = computeBenchChromeSnapshot();
        return;
    }
    bar.hidden = false;

    const label = document.createElement('span');
    label.className = 'bench-chrome-bar__label';
    label.textContent = 'Bench';
    bar.appendChild(label);

    tags.forEach((tagId) => {
        const comp = store.labState.components[tagId];
        const row = getCatalogRow(tagId);
        const displayName = (row && row.name) || tagId;
        const icon = getComponentIcon(comp && comp.type);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'bench-chrome-bar__btn';
        btn.dataset.tagId = tagId;
        if (tagId === store.focusedPanel) btn.classList.add('bench-chrome-bar__btn--active');
        btn.title = `${displayName} (${tagId}) — fixed bench component`;
        btn.innerHTML = `
            <span class="material-icons-round bench-chrome-bar__icon">${icon}</span>
            <span class="bench-chrome-bar__name">${displayName}</span>
        `;
        btn.addEventListener('click', (ev) => {
            const add = !!(ev.ctrlKey || ev.metaKey);
            _onSelect(tagId, { add });
        });
        bar.appendChild(btn);
    });
    _lastSnapshot = computeBenchChromeSnapshot();
}

/**
 * Refresh the bench chrome bar when its snapshot changes. Skips full rebuilds
 * during hover over the bench header (same pattern as the component sidebar).
 * @param {{ force?: boolean }} [opts]
 */
export function maybeRefreshBenchChromeBar({ force = false } = {}) {
    const snapshot = computeBenchChromeSnapshot();
    if (!force && snapshot === _lastSnapshot) {
        syncBenchChromeHighlights();
        return;
    }
    if (!force && _benchHeaderPointerInside) {
        _pendingRebuild = true;
        syncBenchChromeHighlights();
        return;
    }
    _pendingRebuild = false;
    rebuildBenchChromeBar();
}

/** @deprecated Prefer {@link maybeRefreshBenchChromeBar}. Kept for callers that force a rebuild. */
export function refreshBenchChromeBar() {
    maybeRefreshBenchChromeBar({ force: true });
}

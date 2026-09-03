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
    isOverviewCamera,
    listChromeComponentTags,
} from '../component-model.js';
import { getComponentIcon } from './icons.js';
import {
    getOptimizationHighlightForTag,
    optimizationHighlightColor,
    isOptimizationPlanningActive,
} from '../state/optimization-builder.js';
import {
    getParameterScanHighlightForTag,
    isParameterScanPlanningActive,
} from '../state/parameter-scan-builder.js';
import { isLiveFeedActive, normalizeCapabilities } from '../component-state.js';
import { startLiveFeed, endLiveFeed } from '../api/live-feed.js';
import { openLiveFeedPopout } from './live-feed-popout.js';
import { fetchLabState } from '../state/lab-state.js';
import { log } from './log.js';

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

function _declaresLiveFeed(tagId) {
    const row = getCatalogRow(tagId);
    const caps = normalizeCapabilities(row?.capabilities);
    const prims = caps.primitives || [];
    return prims.includes('START_LIVE_FEED');
}

function computeBenchChromeSnapshot() {
    const tags = listChromeComponentTags(store.labState);
    const parts = [tags.join(',')];
    tags.forEach((tagId) => {
        const comp = store.labState?.components?.[tagId];
        const row = getCatalogRow(tagId);
        const displayName = (row && row.name) || tagId;
        const live = isLiveFeedActive(comp, 'stream') ? '1' : '0';
        parts.push(
            `${tagId}=${displayName}:${comp?.type || ''}:${getComponentIcon(comp?.type)}:live${live}`,
        );
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
            'scan-plan-axis',
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
        } else if (isParameterScanPlanningActive() && getParameterScanHighlightForTag(tagId)) {
            btn.classList.add('scan-plan-axis');
            btn.style.borderColor = '#a78bfa';
            btn.style.boxShadow = '0 0 8px #a78bfa66';
        }
    });
    bar.querySelectorAll('.bench-chrome-bar__live[data-tag-id]').forEach((btn) => {
        const tagId = btn.dataset.tagId;
        const comp = store.labState?.components?.[tagId];
        const live = isLiveFeedActive(comp, 'stream');
        btn.classList.toggle('bench-chrome-bar__live--on', live);
        btn.setAttribute('aria-pressed', live ? 'true' : 'false');
        btn.title = live
            ? 'End table-top live feed'
            : 'Turn on table-top live feed';
        const label = btn.querySelector('.bench-chrome-bar__live-label');
        if (label) label.textContent = live ? 'Live on' : 'Live feed';
    });
}

function _bindLiveToggle(btn, tagId) {
    btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const comp = store.labState?.components?.[tagId];
        const live = isLiveFeedActive(comp, 'stream');
        btn.disabled = true;
        const done = () => {
            btn.disabled = false;
            syncBenchChromeHighlights();
        };
        if (live) {
            void endLiveFeed(tagId, 'all', { skipConfirm: true })
                .then(async () => {
                    await fetchLabState();
                    log(`Table-top live feed off (${tagId})`, 'info');
                })
                .catch((e) => {
                    if (String(e?.message || e) === 'cancelled') return;
                    log(`End live feed failed: ${e.message || e}`, 'error');
                })
                .finally(done);
            return;
        }
        void startLiveFeed(tagId, 'stream', { skipConfirm: true })
            .then(async () => {
                await fetchLabState();
                openLiveFeedPopout(tagId, { fetchLabState });
                log(`Table-top live feed on (${tagId})`, 'info');
            })
            .catch((e) => {
                if (String(e?.message || e) === 'cancelled') return;
                log(`Start live feed failed: ${e.message || e}`, 'error');
            })
            .finally(done);
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

        if (_declaresLiveFeed(tagId) && isOverviewCamera(tagId)) {
            const live = isLiveFeedActive(comp, 'stream');
            const liveBtn = document.createElement('button');
            liveBtn.type = 'button';
            liveBtn.className = 'bench-chrome-bar__live';
            if (live) liveBtn.classList.add('bench-chrome-bar__live--on');
            liveBtn.dataset.tagId = tagId;
            liveBtn.setAttribute('aria-pressed', live ? 'true' : 'false');
            liveBtn.title = live
                ? 'End table-top live feed'
                : 'Turn on table-top live feed';
            liveBtn.innerHTML = `
                <span class="material-icons-round" aria-hidden="true">videocam</span>
                <span class="bench-chrome-bar__live-label">${live ? 'Live on' : 'Live feed'}</span>
            `;
            _bindLiveToggle(liveBtn, tagId);
            bar.appendChild(liveBtn);
        }
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

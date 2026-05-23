/**
 * Pose-refresh modal + runner.
 *
 * Pose refresh = ask the lab to re-localize on-table components from a camera scan.
 * The user can freeze a subset of tags (uncheck them in the modal) so the entire row
 * (measurables / tunables / nominal pose) is preserved through the refresh.
 *
 * Endpoint: POST /api/lab-state/refresh-pose  {preserve_tag_ids: string[]}
 *
 * Wired into:
 *   - the "Refresh Pose" sidebar button (app-main)
 *   - the Command Console "refresh_pose" command
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { isOnTableComponent } from '../component-model.js';
import { fetchLaserLines } from './laser-lines-panel.js';

let _fetchLabState = async () => {};

/**
 * @param {{ fetchLabState: () => Promise<void> }} deps
 */
export function initPoseRefresh(deps) {
    if (deps && typeof deps.fetchLabState === 'function') _fetchLabState = deps.fetchLabState;
}

/** Collect tag ids eligible for simulated / camera pose refresh (on layout canvas). */
function poseRefreshEligibleTagIds() {
    const comps = store.labState?.components || {};
    return Object.keys(comps)
        .filter((tid) => comps[tid] && isOnTableComponent(comps[tid]))
        .sort();
}

/**
 * Modal: unchecked tags stay frozen (full component row unchanged); checked tags get scan updates.
 * @returns {Promise<string[]|null>} preserve list, empty if none unchecked, ``null`` if cancelled.
 */
function promptRefreshPosePreserveIds() {
    const ids = poseRefreshEligibleTagIds();
    if (!ids.length) {
        return Promise.resolve([]);
    }
    return new Promise((resolve) => {
        const existing = document.getElementById('refresh-pose-preserve-modal');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'refresh-pose-preserve-modal';
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0,0,0,0.82)';
        overlay.style.zIndex = '3100';
        overlay.style.display = 'flex';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';
        overlay.style.backdropFilter = 'blur(4px)';

        const card = document.createElement('div');
        card.style.backgroundColor = '#181b21';
        card.style.border = '1px solid var(--primary-accent, #3b82f6)';
        card.style.borderRadius = '8px';
        card.style.padding = '24px';
        card.style.width = '440px';
        card.style.maxHeight = '76vh';
        card.style.overflow = 'auto';
        card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

        const titleEl = document.createElement('h2');
        titleEl.style.margin = '0 0 8px 0';
        titleEl.style.color = '#e2e8f0';
        titleEl.style.fontSize = '18px';
        titleEl.textContent = 'Refresh poses from camera';

        const sub = document.createElement('p');
        sub.style.margin = '0 0 12px 0';
        sub.style.color = '#94a3b8';
        sub.style.fontSize = '13px';
        sub.style.lineHeight = '1.5';
        sub.textContent =
            'Checked = update this component from the scan. Unchecked = keep the entire current row (measurables, tunables, nominal pose) unchanged.';

        const listHost = document.createElement('div');
        listHost.style.maxHeight = '260px';
        listHost.style.overflow = 'auto';
        listHost.style.marginBottom = '16px';
        listHost.style.border = '1px solid var(--border-color, #2a2e36)';
        listHost.style.borderRadius = '6px';
        listHost.style.padding = '8px';

        ids.forEach((tid) => {
            const row = document.createElement('label');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.gap = '8px';
            row.style.padding = '4px 0';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.dataset.tagId = tid;
            const span = document.createElement('span');
            span.textContent = tid;
            row.appendChild(cb);
            row.appendChild(span);
            listHost.appendChild(row);
        });

        const rowSel = document.createElement('div');
        rowSel.style.display = 'flex';
        rowSel.style.flexWrap = 'wrap';
        rowSel.style.gap = '8px';
        rowSel.style.marginBottom = '12px';

        const allBtn = document.createElement('button');
        allBtn.type = 'button';
        allBtn.className = 'btn btn-secondary';
        allBtn.style.width = 'auto';
        allBtn.textContent = 'Refresh all';
        allBtn.onclick = () => {
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                cb.checked = true;
            });
        };
        const noneBtn = document.createElement('button');
        noneBtn.type = 'button';
        noneBtn.className = 'btn btn-secondary';
        noneBtn.style.width = 'auto';
        noneBtn.textContent = 'Keep all (no pose updates)';
        noneBtn.onclick = () => {
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                cb.checked = false;
            });
        };
        rowSel.appendChild(allBtn);
        rowSel.appendChild(noneBtn);

        const btnRow = document.createElement('div');
        btnRow.style.display = 'flex';
        btnRow.style.gap = '10px';
        btnRow.style.justifyContent = 'flex-end';

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn btn-secondary';
        cancelBtn.style.width = 'auto';
        cancelBtn.textContent = 'Cancel';

        const goBtn = document.createElement('button');
        goBtn.type = 'button';
        goBtn.className = 'btn btn-primary';
        goBtn.style.width = 'auto';
        goBtn.textContent = 'Start refresh';

        const finish = () => {
            overlay.remove();
        };

        cancelBtn.onclick = () => {
            finish();
            resolve(null);
        };
        goBtn.onclick = () => {
            const preserve = [];
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                if (!cb.checked && cb.dataset.tagId) preserve.push(cb.dataset.tagId);
            });
            finish();
            resolve(preserve);
        };

        overlay.addEventListener('click', (ev) => {
            if (ev.target === overlay) cancelBtn.click();
        });

        btnRow.appendChild(cancelBtn);
        btnRow.appendChild(goBtn);
        card.appendChild(titleEl);
        card.appendChild(sub);
        card.appendChild(rowSel);
        card.appendChild(listHost);
        card.appendChild(btnRow);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
    });
}

/** Same behavior as the Refresh Pose button (shared with Command Console): camera pose pass → measurables.pose. */
export async function runLabPoseRefresh() {
    const preserve_tag_ids = await promptRefreshPosePreserveIds();
    if (preserve_tag_ids === null) {
        log('Refresh poses cancelled.', 'info');
        return;
    }
    log(
        preserve_tag_ids.length
            ? `Refreshing poses (${preserve_tag_ids.length} tag(s) frozen)…`
            : 'Refreshing poses from camera (re-localize)…',
        'warn',
    );
    const res = await fetch('/api/lab-state/refresh-pose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preserve_tag_ids }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Refresh pose failed');
    }

    const start = Date.now();
    while (Date.now() - start < 30000) {
        const stateRes = await fetch('/api/lab-state');
        const state = await stateRes.json();
        if (state && state.system_status === 'IDLE') break;
        await new Promise((r) => setTimeout(r, 500));
    }

    store.forceGhostSync = true;
    await fetchLaserLines();
    await _fetchLabState();
}

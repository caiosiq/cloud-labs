/**
 * Layout-conflict UI: warning banner + one-issue-at-a-time blocking modal.
 *
 * Source of issues:
 *   - `store.layoutIssues` (server-detected, from /api/layout-conflicts) — the
 *     single authoritative source. The client no longer recomputes layout rules.
 *
 * Issue kinds (server):
 *   - PLACED_IN_Q3: a "placed" tag has nominal pose inside the storage rectangle.
 *   - STORED_OUTSIDE_Q3 / STORED_OFF_SLOT: a "stored" tag's pose isn't on its grid cell.
 *
 * The modal only shows while system_status is IDLE so we don't shove a prompt at the user
 * mid-motion. Dismissals are remembered per session so the modal doesn't re-pop until the
 * issue actually changes.
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { isStorageRegion } from '../storage-region.js';
import { drawPose } from '../component-model.js';

let _executeSendCommand = async () => {};

/**
 * @param {{ executeSendCommand: (cmd: object) => Promise<any> }} deps
 */
export function initLayoutConflicts(deps) {
    if (deps && typeof deps.executeSendCommand === 'function') {
        _executeSendCommand = deps.executeSendCommand;
    }
}

export function updateLayoutWarningBanner() {
    const el = document.getElementById('layout-warnings');
    if (!el || !store.labState) return;
    const seen = new Set();
    const lines = [];
    for (const s of (store.layoutIssues || []).map((i) => i.message)) {
        if (!seen.has(s)) {
            seen.add(s);
            lines.push(s);
        }
    }
    if (!lines.length) {
        el.style.display = 'none';
        el.textContent = '';
        return;
    }
    el.style.display = 'block';
    el.innerHTML =
        '<span class="material-icons-round" style="font-size: var(--text-base);vertical-align:middle;color:#f59e0b;">warning</span> ' +
        '<strong>Layout</strong>: ' +
        lines.map((w) => `<span style="display:block;margin-top:4px;">${w}</span>`).join('');
}

const dismissedLayoutIssueKeys = new Set();

function layoutIssueKey(issue) {
    return `${issue.kind}:${issue.tag_id}`;
}

function layoutConflictMoveDefaults(tagId) {
    const c = store.labState?.components?.[tagId];
    const g = store.ghostState[tagId];
    const mp = drawPose(c || {});
    const rot = typeof mp.rotation === 'number' ? mp.rotation : 0;
    if (g && typeof g.x === 'number' && typeof g.y === 'number' && !isStorageRegion(g.x, g.y)) {
        return { x: g.x, y: g.y, rotation: typeof g.rotation === 'number' ? g.rotation : rot };
    }
    return { x: 150, y: 150, rotation: rot };
}

export function removeLayoutConflictModal() {
    document.getElementById('layout-conflict-modal')?.remove();
}

export function updateLayoutConflictModal() {
    if (!store.labState || store.labState.system_status !== 'IDLE') {
        removeLayoutConflictModal();
        return;
    }
    const issues = store.layoutIssues || [];
    for (const k of [...dismissedLayoutIssueKeys]) {
        if (!issues.some((i) => layoutIssueKey(i) === k)) dismissedLayoutIssueKeys.delete(k);
    }
    const next = issues.find((i) => !dismissedLayoutIssueKeys.has(layoutIssueKey(i)));
    if (!next) {
        removeLayoutConflictModal();
        return;
    }
    const existing = document.getElementById('layout-conflict-modal');
    const prevKey = existing?.dataset?.issueKey;
    const key = layoutIssueKey(next);
    if (existing && prevKey === key) return;

    removeLayoutConflictModal();
    const overlay = document.createElement('div');
    overlay.id = 'layout-conflict-modal';
    overlay.dataset.issueKey = key;
    overlay.style.cssText =
        'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:2990;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

    const card = document.createElement('div');
    card.style.cssText =
        'background:#181b21;border:1px solid rgba(245,158,11,0.45);border-radius:10px;padding:22px;max-width:440px;width:92%;box-shadow:0 20px 50px rgba(0,0,0,0.65);';

    const kind = next.kind;
    const tid = next.tag_id;
    let bodyHtml = `<h3 style="margin:0 0 8px 0;color:#e2e8f0;font-size:16px;display:flex;align-items:center;gap:8px;"><span class="material-icons-round" style="color:#f59e0b;font-size:22px;">warning</span> Inventory layout</h3>`;
    bodyHtml += `<p style="margin:0 0 16px 0;color:#94a3b8;font-size: var(--text-base);line-height:1.45;">${next.message}</p>`;

    if (kind === 'PLACED_IN_Q3') {
        const d = layoutConflictMoveDefaults(tid);
        bodyHtml += `<div style="font-size: var(--text-sm);color:#64748b;margin-bottom:8px;">Move to a breadboard pose (mm, degrees):</div>`;
        bodyHtml += `<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px;">`;
        bodyHtml += `<label style="font-size: var(--text-xs);color:#94a3b8;">X<br><input id="lconf-tx" type="number" step="0.1" value="${d.x.toFixed(1)}" style="width:100%;padding:6px;border-radius:6px;border:1px solid #334155;background:#0f1115;color:#e2e8f0;"></label>`;
        bodyHtml += `<label style="font-size: var(--text-xs);color:#94a3b8;">Y<br><input id="lconf-ty" type="number" step="0.1" value="${d.y.toFixed(1)}" style="width:100%;padding:6px;border-radius:6px;border:1px solid #334155;background:#0f1115;color:#e2e8f0;"></label>`;
        bodyHtml += `<label style="font-size: var(--text-xs);color:#94a3b8;">Rot<br><input id="lconf-tr" type="number" step="0.1" value="${d.rotation.toFixed(1)}" style="width:100%;padding:6px;border-radius:6px;border:1px solid #334155;background:#0f1115;color:#e2e8f0;"></label>`;
        bodyHtml += `</div>`;
        bodyHtml += `<div style="display:flex;flex-direction:column;gap:8px;">`;
        bodyHtml += `<button type="button" id="lconf-move" class="btn btn-primary" style="width:100%;justify-content:center;">Move to target</button>`;
        bodyHtml += `<button type="button" id="lconf-store" class="btn btn-secondary" style="width:100%;justify-content:center;">Store with packing (grid)</button>`;
        bodyHtml += `<button type="button" id="lconf-dismiss" class="btn btn-secondary" style="width:100%;opacity:0.85;">Dismiss</button>`;
        bodyHtml += `</div>`;
    } else if (kind === 'STORED_OUTSIDE_Q3' || kind === 'STORED_OFF_SLOT') {
        bodyHtml += `<div style="display:flex;flex-direction:column;gap:8px;">`;
        bodyHtml += `<button type="button" id="lconf-affirm" class="btn btn-primary" style="width:100%;justify-content:center;">Mark as PLACED (keep current pose)</button>`;
        // Off-slot / outside-rect with a stored assignment → re-center on the
        // assigned cell (same as panel "Re-center in cell"). REPACK would move
        // to a *different* free cell and was still a stub on real edge.
        bodyHtml += `<button type="button" id="lconf-recenter" class="btn btn-secondary" style="width:100%;justify-content:center;">Re-center in cell (0°)</button>`;
        bodyHtml += `<button type="button" id="lconf-dismiss" class="btn btn-secondary" style="width:100%;opacity:0.85;">Dismiss</button>`;
        bodyHtml += `</div>`;
    } else {
        bodyHtml += `<button type="button" id="lconf-dismiss" class="btn btn-secondary" style="width:100%;">Dismiss</button>`;
    }

    card.innerHTML = bodyHtml;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const dismiss = () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
    };

    overlay.querySelector('#lconf-dismiss')?.addEventListener('click', dismiss);

    overlay.querySelector('#lconf-move')?.addEventListener('click', async () => {
        const tx = parseFloat(document.getElementById('lconf-tx')?.value || '0');
        const ty = parseFloat(document.getElementById('lconf-ty')?.value || '0');
        const tr = parseFloat(document.getElementById('lconf-tr')?.value || '0');
        if (isStorageRegion(tx, ty)) {
            log('Target must not lie in the storage (inventory) rectangle.', 'error');
            return;
        }
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await _executeSendCommand({
            action: 'MOVE_COMPONENT',
            target_id: tid,
            parameters: { target_x: tx, target_y: ty, rotation: tr },
        });
    });

    overlay.querySelector('#lconf-store')?.addEventListener('click', async () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await _executeSendCommand({ action: 'STORE_COMPONENT', target_id: tid, parameters: {} });
    });

    overlay.querySelector('#lconf-affirm')?.addEventListener('click', async () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        await _executeSendCommand({ action: 'AFFIRM_PLACED_AT_CURRENT', target_id: tid, parameters: {} });
    });

    overlay.querySelector('#lconf-recenter')?.addEventListener('click', async () => {
        dismissedLayoutIssueKeys.add(key);
        removeLayoutConflictModal();
        store.forceGhostSync = true;
        await _executeSendCommand({
            action: 'RECENTER_IN_STORAGE',
            target_id: tid,
            parameters: {},
        });
    });
}

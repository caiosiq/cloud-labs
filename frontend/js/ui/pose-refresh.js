/**
 * Pose-refresh modal + runner.
 *
 * Pose refresh = RECORD_TUNABLES for ``nominal_pose`` on selected on-table tags.
 * Dry-run offers stay on GET; apply goes through the language plane
 * (``POST /api/command``). Unchecked tags are preserved.
 *
 * Endpoints:
 *   GET  /api/lab-state/refresh-pose/offers?tag_ids=…  (preview only)
 *   POST /api/command  { action: RECORD_TUNABLES, parameters: { tag_ids, tunable_paths: ["nominal_pose"] } }
 *
 * Wired into:
 *   - the "Refresh Pose" sidebar button (app-main)
 *   - the Command Console "refresh_pose" command
 */
import { store } from '../state/store.js';
import { runtimeEditableOrMessage } from '../control/control-state.js';
import { log } from './log.js';
import { isOnTableComponent } from '../component-model.js';
import { fetchLaserLines } from './laser-lines-panel.js';
import { withBackendQuery } from '../state/backend-selection.js';
import { executeSendCommand } from '../api/commands.js';

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
 * Map modal selection → RECORD_TUNABLES tag_ids.
 * @param {{ apply_tag_ids?: string[], preserve_tag_ids?: string[], tag_ids?: string[] }} selection
 * @returns {string[]}
 */
export function resolveRecordTagIds(selection) {
    if (!selection || typeof selection !== 'object') return [];
    if (Array.isArray(selection.apply_tag_ids)) {
        return selection.apply_tag_ids.filter(Boolean);
    }
    const scope =
        Array.isArray(selection.tag_ids) && selection.tag_ids.length
            ? selection.tag_ids.filter(Boolean)
            : poseRefreshEligibleTagIds();
    if (Array.isArray(selection.preserve_tag_ids)) {
        const frozen = new Set(selection.preserve_tag_ids.filter(Boolean));
        return scope.filter((tid) => !frozen.has(tid));
    }
    return scope;
}

async function fetchRefreshPoseOffers(scopeTagIds = null) {
    const qs =
        scopeTagIds && scopeTagIds.length
            ? `?tag_ids=${encodeURIComponent(scopeTagIds.join(','))}`
            : '';
    const res = await fetch(withBackendQuery(`/api/lab-state/refresh-pose/offers${qs}`));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.detail || res.statusText || 'Refresh pose offers failed');
    }
    return data;
}

function formatPose(pose) {
    if (!pose || typeof pose !== 'object') return '—';
    const x = Number(pose.x);
    const y = Number(pose.y);
    const r = Number(pose.rotation);
    if ([x, y, r].some((v) => Number.isNaN(v))) return '—';
    return `(${x.toFixed(1)}, ${y.toFixed(1)}, ${r.toFixed(1)}°)`;
}

/**
 * Tolerance-aware modal from GET offers. Checked tags will be refreshed; unchecked preserved.
 * @returns {Promise<string[]|null>} apply list, or null if cancelled.
 */
function promptRefreshPoseOffersModal(payload) {
    const offers = Array.isArray(payload.offers) ? payload.offers : [];
    const thresholds = payload.thresholds || {};
    const posMm = thresholds.position_mm ?? 2;
    const yawDeg = thresholds.yaw_deg ?? 5;

    if (!offers.length) {
        return Promise.resolve([]);
    }

    return new Promise((resolve) => {
        const existing = document.getElementById('refresh-pose-offers-modal');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'refresh-pose-offers-modal';
        overlay.style.cssText =
            'position:fixed;inset:0;background:rgba(0,0,0,0.82);z-index:3100;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

        const card = document.createElement('div');
        card.style.cssText =
            'background:#181b21;border:1px solid #3b82f6;border-radius:8px;padding:24px;width:520px;max-height:80vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,0.7);';

        const titleEl = document.createElement('h2');
        titleEl.style.cssText = 'margin:0 0 8px 0;color:#e2e8f0;font-size:18px;';
        titleEl.textContent = 'Refresh poses from camera';

        const sub = document.createElement('p');
        sub.style.cssText = 'margin:0 0 8px 0;color:#94a3b8;font-size:13px;line-height:1.5;';
        sub.textContent =
            'Checked components will be overwritten via RECORD_TUNABLES → tunables.nominal_pose. Unchecked rows stay frozen. Applying runs a camera scan on the bench.';

        const meta = document.createElement('p');
        meta.style.cssText = 'margin:0 0 12px 0;color:#64748b;font-size:12px;';
        meta.textContent = `Tolerance ±${posMm} mm, ±${yawDeg}° yaw (same as session reconciliation). Small deltas are unchecked by default.`;

        const listHost = document.createElement('div');
        listHost.style.cssText =
            'max-height:280px;overflow:auto;margin-bottom:12px;border:1px solid #2a2e36;border-radius:6px;padding:8px;';

        offers.forEach((offer) => {
            const tid = offer.tag_id || '?';
            const row = document.createElement('label');
            row.style.cssText =
                'display:block;padding:8px 4px;border-bottom:1px solid rgba(42,46,54,0.6);cursor:pointer;';
            const head = document.createElement('div');
            head.style.cssText = 'display:flex;align-items:flex-start;gap:8px;';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = offer.default_apply !== false;
            cb.dataset.tagId = tid;
            const body = document.createElement('div');
            body.style.flex = '1';
            const name = document.createElement('div');
            name.style.cssText = 'color:#e2e8f0;font-size:13px;font-weight:600;';
            name.textContent = tid;
            const detail = document.createElement('div');
            detail.style.cssText = 'color:#94a3b8;font-size:11px;line-height:1.45;margin-top:4px;';
            const deltaMm = offer.delta_mm != null ? Number(offer.delta_mm).toFixed(2) : '?';
            const deltaYaw =
                offer.delta_yaw_deg != null ? Number(offer.delta_yaw_deg).toFixed(2) : '?';
            const note = offer.within_tolerance
                ? `Δ ${deltaMm} mm, ${deltaYaw}° — within tolerance (skip unless you check)`
                : `Δ ${deltaMm} mm, ${deltaYaw}° — exceeds tolerance`;
            detail.innerHTML = `${note}<br>Current ${formatPose(offer.current_pose)} → scan ${formatPose(offer.proposed_pose)}`;
            body.appendChild(name);
            body.appendChild(detail);
            head.appendChild(cb);
            head.appendChild(body);
            row.appendChild(head);
            listHost.appendChild(row);
        });

        const rowSel = document.createElement('div');
        rowSel.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;';
        const allBtn = document.createElement('button');
        allBtn.type = 'button';
        allBtn.className = 'btn btn-secondary';
        allBtn.style.width = 'auto';
        allBtn.textContent = 'Select all';
        allBtn.onclick = () => {
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                cb.checked = true;
            });
        };
        const sigBtn = document.createElement('button');
        sigBtn.type = 'button';
        sigBtn.className = 'btn btn-secondary';
        sigBtn.style.width = 'auto';
        sigBtn.textContent = 'Significant only';
        sigBtn.onclick = () => {
            offers.forEach((offer, index) => {
                const cb = listHost.querySelectorAll('input[type="checkbox"]')[index];
                if (cb) cb.checked = offer.default_apply !== false;
            });
        };
        const noneBtn = document.createElement('button');
        noneBtn.type = 'button';
        noneBtn.className = 'btn btn-secondary';
        noneBtn.style.width = 'auto';
        noneBtn.textContent = 'Keep all (no updates)';
        noneBtn.onclick = () => {
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                cb.checked = false;
            });
        };
        rowSel.appendChild(allBtn);
        rowSel.appendChild(sigBtn);
        rowSel.appendChild(noneBtn);

        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;';
        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn btn-secondary';
        cancelBtn.style.width = 'auto';
        cancelBtn.textContent = 'Cancel';
        const goBtn = document.createElement('button');
        goBtn.type = 'button';
        goBtn.className = 'btn btn-primary';
        goBtn.style.width = 'auto';
        goBtn.textContent = 'RECORD nominal_pose';

        const finish = () => overlay.remove();

        cancelBtn.onclick = () => {
            finish();
            resolve(null);
        };
        goBtn.onclick = () => {
            const apply = [];
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                if (cb.checked && cb.dataset.tagId) apply.push(cb.dataset.tagId);
            });
            finish();
            resolve(apply);
        };

        overlay.addEventListener('click', (ev) => {
            if (ev.target === overlay) cancelBtn.click();
        });

        btnRow.appendChild(cancelBtn);
        btnRow.appendChild(goBtn);
        card.appendChild(titleEl);
        card.appendChild(sub);
        card.appendChild(meta);
        card.appendChild(rowSel);
        card.appendChild(listHost);
        card.appendChild(btnRow);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
    });
}

/**
 * Legacy modal when offers endpoint is unsupported (real bench until F2).
 * @returns {Promise<string[]|null>}
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
            'Checked = freeze this tag (skip RECORD). Unchecked = overwrite tunables.nominal_pose from the scan.';

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
        goBtn.textContent = 'RECORD nominal_pose';

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

async function resolveRefreshSelection(scopeTagIds = null) {
    try {
        const offersPayload = await fetchRefreshPoseOffers(scopeTagIds);
        if (offersPayload.supported) {
            if (offersPayload.skipped_reason === 'no_eligible_components') {
                log('No on-table components eligible for pose refresh.', 'info');
                return null;
            }
            if (offersPayload.skipped_reason?.startsWith('busy:')) {
                throw new Error(`System is ${offersPayload.skipped_reason.slice(5)}. Please wait.`);
            }
            const apply_tag_ids = await promptRefreshPoseOffersModal(offersPayload);
            if (apply_tag_ids === null) return null;
            return { apply_tag_ids, tag_ids: scopeTagIds || undefined };
        }
    } catch (e) {
        console.warn('[pose-refresh] offers unavailable, using legacy modal:', e);
    }
    const preserve_tag_ids = await promptRefreshPosePreserveIds();
    if (preserve_tag_ids === null) return null;
    return { preserve_tag_ids, tag_ids: scopeTagIds || undefined };
}

async function executePoseRefresh(selection) {
    const tagIds = resolveRecordTagIds(selection);
    if (!tagIds.length) {
        log('No tags selected for RECORD_TUNABLES (nominal_pose).', 'info');
        return;
    }
    log(
        `RECORD_TUNABLES nominal_pose for ${tagIds.length} tag(s): ${tagIds.join(', ')}…`,
        'warn',
    );
    const result = await executeSendCommand({
        action: 'RECORD_TUNABLES',
        parameters: {
            tag_ids: tagIds,
            tunable_paths: ['nominal_pose'],
            force_rescan: true,
        },
    });
    if (!result.ok) {
        throw new Error(result.error || 'RECORD_TUNABLES failed');
    }

    const start = Date.now();
    while (Date.now() - start < 30000) {
        const stateRes = await fetch(withBackendQuery('/api/lab-state'));
        const state = await stateRes.json();
        if (state && state.system_status === 'IDLE') break;
        await new Promise((r) => setTimeout(r, 500));
    }

    store.forceGhostSync = true;
    await fetchLaserLines();
    await _fetchLabState();
}

/**
 * Scoped pose refresh for one or more tags (e.g. after inventory add).
 * @param {string[]} tagIds
 * @param {{ skipModal?: boolean }} [opts]
 */
export async function runScopedPoseRefresh(tagIds, { skipModal = false } = {}) {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        log(blocked, 'warn');
        return false;
    }
    const scope = (tagIds || []).filter(Boolean);
    if (!scope.length) return false;

    let selection;
    if (skipModal) {
        selection = { tag_ids: scope, apply_tag_ids: scope };
    } else {
        selection = await resolveRefreshSelection(scope);
        if (selection === null) {
            log('Refresh poses cancelled.', 'info');
            return false;
        }
    }

    try {
        await executePoseRefresh(selection);
        return true;
    } catch (err) {
        log(err.message || 'Refresh pose failed', 'error');
        return false;
    }
}

/** Refresh Pose button / Command Console: RECORD_TUNABLES → tunables.nominal_pose. */
export async function runLabPoseRefresh() {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        log(blocked, 'warn');
        return;
    }

    const selection = await resolveRefreshSelection();
    if (selection === null) {
        log('Refresh poses cancelled.', 'info');
        return;
    }
    try {
        await executePoseRefresh(selection);
    } catch (err) {
        log(err.message || 'Refresh pose failed', 'error');
    }
}

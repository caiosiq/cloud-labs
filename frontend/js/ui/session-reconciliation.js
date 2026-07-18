/**
 * Session-reconciliation modal: on boot, ask backend if any tags still match the last
 * graceful-shutdown checkpoint within tolerance, and offer to reload tunables/measurables
 * for the user-chosen subset.
 *
 * Endpoints:
 *   GET  /api/session-reconciliation/offers
 *   POST /api/session-reconciliation/apply  {tag_ids: string[]}
 *
 * Hardware is never re-commanded — we only restore software state. After apply, the caller
 * is responsible for refreshing lab state (wired via {@link initSessionReconciliation}).
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { showErrorModal } from './modals.js';

const SESSION_REC_STORAGE_DISMISS_KEY = 'optics-session-reconcile-dismiss';

let _fetchLabState = async () => {};

/**
 * @param {{ fetchLabState: () => Promise<void> }} deps
 */
export function initSessionReconciliation(deps) {
    if (deps && typeof deps.fetchLabState === 'function') {
        _fetchLabState = deps.fetchLabState;
    }
}

export async function maybeTriggerSessionReconciliation() {
    if (store.sessionReconciliationFetched) return;
    try {
        if (
            typeof sessionStorage !== 'undefined' &&
            sessionStorage.getItem(SESSION_REC_STORAGE_DISMISS_KEY) === '1'
        ) {
            store.sessionReconciliationFetched = true;
            return;
        }
    } catch (_) {
        /* ignore */
    }
    try {
        const res = await fetch('/api/session-reconciliation/offers');
        const data = await res.json();
        if (!res.ok) {
            console.warn('[session-reconcile] offers HTTP', res.status, data);
            return;
        }
        const skipReason = typeof data.skipped_reason === 'string' ? data.skipped_reason : '';
        console.info('[session-reconcile] offers response', {
            enabled: data.enabled,
            skipped_reason: skipReason || null,
            offer_count: (data.offers || []).length,
            checkpoint_path: data.checkpoint_path,
            checkpoint_saved_at: data.checkpoint_saved_at,
            checkpoint_exists: !!data.checkpoint_saved_at,
            debug: data.debug,
            manifest: data.manifest,
        });
        if (/^busy:/i.test(skipReason)) return;

        store.sessionReconciliationFetched = true;
        if (!data || document.getElementById('session-reconcile-modal')) return;
        if (!data.offers || !data.offers.length) return;

        openSessionReconciliationModal(data);
    } catch (e) {
        console.warn('session reconciliation offers unavailable:', e);
    }
}

function closeSessionReconciliationModal() {
    const el = document.getElementById('session-reconcile-modal');
    if (el) el.remove();
}

function openSessionReconciliationModal(payload) {
    if (document.getElementById('session-reconcile-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'session-reconcile-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100vw';
    overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.82)';
    overlay.style.zIndex = '2999';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(4px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #f59e0b';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '480px';
    card.style.maxHeight = '80vh';
    card.style.overflow = 'auto';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    const offers = payload.offers || [];
    const staleEl =
        payload.stale_warning ?
            document.createElement('p')
        : null;
    if (staleEl) {
        staleEl.style.margin = '0 0 12px 0';
        staleEl.style.padding = '8px 10px';
        staleEl.style.borderRadius = '6px';
        staleEl.style.background = '#3b2f0b';
        staleEl.style.color = '#fde68a';
        staleEl.style.fontSize = '12px';
        staleEl.style.lineHeight = '1.4';
        const at =
            payload.checkpoint_saved_at != null ? String(payload.checkpoint_saved_at) : 'unknown';
        staleEl.textContent = `Last session file is older than ${payload.stale_warning_hours}h (saved ${at}). Review before restoring.`;
    }

    const listHost = document.createElement('div');
    listHost.style.maxHeight = '220px';
    listHost.style.overflow = 'auto';
    listHost.style.marginBottom = '16px';
    listHost.style.border = '1px solid var(--border-color, #2a2e36)';
    listHost.style.borderRadius = '6px';
    listHost.style.padding = '8px';

    offers.forEach((o) => {
        const row = document.createElement('label');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.gap = '8px';
        row.style.padding = '4px 0';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.dataset.tagId = o.tag_id;
        const span = document.createElement('span');
        span.textContent = o.tag_id;
        row.appendChild(cb);
        row.appendChild(span);
        listHost.appendChild(row);
    });

    const titleEl = document.createElement('h2');
    titleEl.style.margin = '0 0 8px 0';
    titleEl.style.color = '#e2e8f0';
    titleEl.style.fontSize = '18px';
    titleEl.textContent = 'Restore last session (tunables & measurables)';

    const sub = document.createElement('p');
    sub.style.margin = '0 0 14px 0';
    sub.style.color = '#94a3b8';
    sub.style.fontSize = '13px';
    sub.style.lineHeight = '1.5';
    sub.textContent =
        'Measured poses still match within tolerance for these tags; you can reload saved ' +
        'tunables and measurables from the last graceful shutdown checkpoint. Hardware is not commanded.';

    const meta = document.createElement('div');
    meta.style.fontSize = '11px';
    meta.style.color = '#64748b';
    meta.style.marginBottom = '10px';
    meta.textContent = `Checkpoint ${payload.checkpoint_saved_at || '?'} · age ${payload.age_hours != null ? payload.age_hours.toFixed(1) + ' h' : '—'} · ±${payload.thresholds?.position_mm} mm · ±${payload.thresholds?.yaw_deg}° yaw`;

    const btnRow = document.createElement('div');
    btnRow.style.display = 'flex';
    btnRow.style.flexWrap = 'wrap';
    btnRow.style.gap = '8px';
    btnRow.style.justifyContent = 'flex-end';

    const dismissBtn = document.createElement('button');
    dismissBtn.className = 'btn btn-secondary';
    dismissBtn.style.width = 'auto';
    dismissBtn.textContent = 'Dismiss';
    dismissBtn.onclick = () => {
        try {
            if (typeof sessionStorage !== 'undefined') {
                sessionStorage.setItem(SESSION_REC_STORAGE_DISMISS_KEY, '1');
            }
        } catch (_) {
            /* ignore */
        }
        closeSessionReconciliationModal();
    };

    const selAllBtn = document.createElement('button');
    selAllBtn.className = 'btn btn-secondary';
    selAllBtn.style.width = 'auto';
    selAllBtn.textContent = offers.length ? 'Select all' : '—';
    selAllBtn.disabled = !offers.length;
    selAllBtn.onclick = () => {
        listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
            cb.checked = true;
        });
    };

    const selNoneBtn = document.createElement('button');
    selNoneBtn.className = 'btn btn-secondary';
    selNoneBtn.style.width = 'auto';
    selNoneBtn.textContent = 'Clear';
    selNoneBtn.onclick = () => {
        listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
            cb.checked = false;
        });
    };

    const applyBtn = document.createElement('button');
    applyBtn.className = 'btn btn-primary';
    applyBtn.style.width = 'auto';
    applyBtn.textContent = 'Apply selected';

    async function submitSelection(all) {
        let ids = all ? offers.map((o) => o.tag_id) : [];
        if (!all) {
            ids = [];
            listHost.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                if (cb.checked) ids.push(cb.dataset.tagId);
            });
        }
        if (!ids.length) {
            log('No tags selected for session restore.', 'warn');
            return;
        }
        try {
            const res = await fetch('/api/session-reconciliation/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag_ids: ids }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'Apply failed');
            log(
                `Restored checkpoint for tags: ${(data.applied_tag_ids || ids).join(', ')}`,
                'info',
            );
            store.forceGhostSync = true;
            closeSessionReconciliationModal();
            await _fetchLabState();
        } catch (e) {
            console.error(e);
            showErrorModal('Session Restore Failed', e.message || String(e));
        }
    }

    applyBtn.onclick = () => submitSelection(false);

    const applyAllBtn = document.createElement('button');
    applyAllBtn.className = 'btn btn-primary';
    applyAllBtn.style.width = 'auto';
    applyAllBtn.textContent = 'Apply all listed';
    applyAllBtn.onclick = () => submitSelection(true);

    btnRow.appendChild(dismissBtn);
    btnRow.appendChild(selNoneBtn);
    btnRow.appendChild(selAllBtn);
    btnRow.appendChild(applyAllBtn);
    btnRow.appendChild(applyBtn);

    card.appendChild(titleEl);
    card.appendChild(sub);
    card.appendChild(meta);
    if (staleEl) card.appendChild(staleEl);
    card.appendChild(listHost);
    card.appendChild(btnRow);
    overlay.appendChild(card);

    overlay.addEventListener('click', (ev) => {
        if (ev.target === overlay) dismissBtn.click();
    });
    document.body.appendChild(overlay);
}

/**
 * Control repo switching and creation (Phase A1).
 */
import {
    createControlRepo,
    fetchControlRepos,
    shortCommitId,
} from '../api/control.js';
import {
    isConfigViewMode,
    setAppliedPointer,
    clearPreviewOverlay,
} from '../control/control-state.js';
import { store } from '../state/store.js';
import { showConfirmationModal, showErrorModal } from './modals.js';

/**
 * @param {HTMLSelectElement|null} repoSelect
 * @param {string} newRepoId
 * @param {string} previousRepoId
 * @param {{
 *   onRefresh: () => Promise<void>,
 *   onRuntimeRefresh: () => Promise<void>,
 * }} deps
 */
export function handleRepoSelectChange(repoSelect, newRepoId, previousRepoId, deps) {
    if (!newRepoId || newRepoId === previousRepoId) return;
    if (repoSelect) repoSelect.value = previousRepoId;

    const repos = store.control.repos || [];
    const target = repos.find((r) => r.repo_id === newRepoId);
    const label = target?.display_name || newRepoId;
    const applied = target?.applied?.configuration_id;
    const appliedLabel = applied ? shortCommitId(applied) : null;

    let message =
        `Switch to repo <strong>${escapeHtml(label)}</strong> (<code>${escapeHtml(newRepoId)}</code>)?<br><br>`;

    message +=
        'The <strong>physical bench does not change</strong> when you switch repos. ' +
        'You arrive with your current table as uncommitted work — relative to this ' +
        "repo's shared empty baseline.<br><br>";

    if (appliedLabel) {
        message +=
            `To move onto this repo's history (e.g. <code>${escapeHtml(appliedLabel)}</code>), ` +
            'first <em>Commit</em> the current bench as a new node/branch or ' +
            '<em>Stash</em> it.<br><br>';
    } else {
        message +=
            'This repo has <strong>no commits yet</strong> — ' +
            'your first Commit captures the current bench.<br><br>';
    }

    if (isConfigViewMode()) {
        message +=
            'You are in view mode on the current repo — finish with ' +
            '<em>Return to bench</em> first.';
    }

    showConfirmationModal(
        message,
        () => {
            void executeRepoSwitch(repoSelect, newRepoId, previousRepoId, deps);
        },
        () => {
            if (repoSelect) repoSelect.value = previousRepoId;
        },
    );
}

async function executeRepoSwitch(repoSelect, newRepoId, previousRepoId, deps) {
    try {
        if (repoSelect) repoSelect.value = newRepoId;
        store.control.repoId = newRepoId;
        // Unresolved: refreshControlPanel adopts the applied branch of the target.
        store.control.branch = null;
        clearPreviewOverlay();
        store.control.viewingCommitId = null;
        store.control.selectedCommitId = null;
        store.control.liveHeadId = null;

        // Switching repos changes only the version-control context; the physical
        // bench is left exactly as-is (no soft/hard checkout). The new repo will
        // report the bench as uncommitted work on top of its empty baseline
        // unless this repo already owns the bench. The user commits or stashes
        // to reconcile.
        await deps.onRefresh();
        await deps.onRuntimeRefresh();
    } catch (e) {
        console.error('[control-repo] switch failed', e);
        if (repoSelect) repoSelect.value = previousRepoId;
        store.control.repoId = previousRepoId;
        showErrorModal('Switch repo failed', e.message || String(e));
    }
}

/**
 * Prompt for a new repo id, create it, and switch to it.
 * @param {{
 *   onRefresh: () => Promise<void>,
 *   onRuntimeRefresh: () => Promise<void>,
 *   onCreated?: (repoId: string) => void,
 * }} deps
 */
export async function promptCreateControlRepo(deps) {
    const rawId = prompt(
        'New configuration repo id (lowercase, e.g. experiment-a):',
        'experiment',
    );
    if (rawId == null || !rawId.trim()) return;

    const displayName = prompt('Display name (optional):', rawId.trim()) ?? rawId.trim();

    try {
        const data = await createControlRepo({
            repoId: rawId.trim().toLowerCase(),
            displayName: displayName.trim() || null,
        });
        const repo = data.repo || {};
        setAppliedPointer(repo.applied);
        store.control.repoId = repo.repo_id || rawId.trim().toLowerCase();
        store.control.branch = null;
        store.control.viewingCommitId = null;
        store.control.selectedCommitId = null;
        store.control.liveHeadId = null;
        await deps.onRefresh();
        await deps.onRuntimeRefresh();
        deps.onCreated?.(store.control.repoId);
    } catch (e) {
        console.error('[control-repo] create failed', e);
        showErrorModal('Create repo failed', e.message || String(e));
    }
}

/**
 * Opt-in entry point: list available repos and let the user pick one to enter
 * (or create a new one). Calls `deps.onEnter(repoId)` once a repo is chosen.
 * @param {{ onEnter: (repoId: string) => void, onRefresh: () => Promise<void>,
 *   onRuntimeRefresh: () => Promise<void> }} deps
 */
export async function promptStartVersionControl(deps) {
    let repos = [];
    try {
        const payload = await fetchControlRepos();
        repos = payload.repos || [];
    } catch (e) {
        showErrorModal('Version control', `Could not load repos: ${e.message || e}`);
        return;
    }

    const existing = document.getElementById('vc-start-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'vc-start-modal';
    overlay.style.cssText =
        'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:2990;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

    const card = document.createElement('div');
    card.style.cssText =
        'background:#181b21;border:1px solid rgba(148,163,184,0.25);border-radius:10px;padding:22px;max-width:420px;width:92%;box-shadow:0 20px 50px rgba(0,0,0,0.65);';

    let html =
        '<h3 style="margin:0 0 6px 0;color:#e2e8f0;font-size:16px;">Start version control</h3>' +
        '<p style="margin:0 0 14px 0;color:#94a3b8;font-size:12px;line-height:1.45;">' +
        'Pick a repo to enter. Your physical bench is left untouched — you start with ' +
        'no current node and Set one (or Commit) to establish a base.</p>' +
        '<div style="display:flex;flex-direction:column;gap:8px;">';
    if (repos.length) {
        for (const r of repos) {
            const id = escapeHtml(r.repo_id);
            const name = escapeHtml(r.display_name || r.repo_id);
            html += `<button type="button" class="btn btn-secondary vc-pick" data-repo="${id}" style="width:100%;justify-content:flex-start;">${name} <span style="opacity:0.5;margin-left:6px;font-family:ui-monospace,monospace;">${id}</span></button>`;
        }
    } else {
        html += '<div style="color:#64748b;font-size:12px;">No repos yet — create one.</div>';
    }
    html +=
        '</div><div style="display:flex;gap:8px;margin-top:14px;">' +
        '<button type="button" id="vc-start-new" class="btn btn-primary" style="flex:1;justify-content:center;">New repo</button>' +
        '<button type="button" id="vc-start-cancel" class="btn btn-secondary" style="flex:1;justify-content:center;">Cancel</button>' +
        '</div>';
    card.innerHTML = html;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.querySelector('#vc-start-cancel')?.addEventListener('click', close);
    overlay.addEventListener('click', (ev) => {
        if (ev.target === overlay) close();
    });
    for (const btn of overlay.querySelectorAll('.vc-pick')) {
        btn.addEventListener('click', () => {
            const repoId = btn.getAttribute('data-repo');
            close();
            if (repoId) deps.onEnter(repoId);
        });
    }
    overlay.querySelector('#vc-start-new')?.addEventListener('click', () => {
        close();
        void promptCreateControlRepo({
            onRefresh: deps.onRefresh,
            onRuntimeRefresh: deps.onRuntimeRefresh,
            onCreated: (repoId) => deps.onEnter(repoId),
        });
    });
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

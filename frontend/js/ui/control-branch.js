/**
 * Branch switching — confirm, soft-checkout HEAD, enter view mode.
 */
import { softCheckoutConfiguration, shortCommitId } from '../api/control.js';
import {
    getAppliedCommitId,
    isDirty,
    setViewingCommit,
    setPreviewOverlay,
    clearPreviewOverlay,
} from '../control/control-state.js';
import { store } from '../state/store.js';
import { showConfirmationModal, showErrorModal } from './modals.js';

/**
 * @param {HTMLSelectElement|null} branchSelect
 * @param {string} newBranch
 * @param {string} previousBranch
 * @param {{ onSwitch: (branch: string, headId: string|null) => Promise<void> }} deps
 */
export function handleBranchSelectChange(branchSelect, newBranch, previousBranch, deps) {
    if (!newBranch || newBranch === previousBranch) return;

    if (branchSelect) branchSelect.value = previousBranch;

    // Git-like guard: can't switch branches with a dirty working table.
    if (isDirty()) {
        showErrorModal(
            'Uncommitted changes',
            'You have uncommitted changes on the bench. Commit or stash them before switching branches.',
            { kind: 'dismissible' },
        );
        return;
    }

    const headId = store.control.heads?.[newBranch] ?? null;
    const headLabel = headId ? shortCommitId(headId) : '—';
    const appliedId = getAppliedCommitId();
    const appliedLabel = appliedId ? shortCommitId(appliedId) : 'current bench';

    const message =
        `Switch to branch <strong>${escapeHtml(newBranch)}</strong> ` +
        `(HEAD ${headLabel})?<br><br>` +
        `The table will show that configuration in <strong>view mode</strong>. ` +
        `The bench stays at <code>${escapeHtml(appliedLabel)}</code> until you apply.`;

    showConfirmationModal(
        message,
        () => {
            void executeBranchSwitch(branchSelect, newBranch, previousBranch, headId, deps);
        },
        () => {
            if (branchSelect) branchSelect.value = previousBranch;
        },
    );
}

async function executeBranchSwitch(branchSelect, newBranch, previousBranch, headId, deps) {
    try {
        if (branchSelect) branchSelect.value = newBranch;
        store.control.branch = newBranch;

        if (headId) {
            const res = await softCheckoutConfiguration(headId);
            setPreviewOverlay(res && res.configuration, res && res.metadata);
            store.control.selectedCommitId = headId;
            setViewingCommit(headId);
            await deps.onSwitch(newBranch, headId);
        } else {
            clearPreviewOverlay();
            store.control.viewingCommitId = null;
            store.control.selectedCommitId = null;
            await deps.onSwitch(newBranch, null);
        }
    } catch (e) {
        console.error('[control-branch] switch failed', e);
        if (branchSelect) branchSelect.value = previousBranch;
        store.control.branch = previousBranch;
        showErrorModal('Switch branch failed', e.message || String(e));
    }
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

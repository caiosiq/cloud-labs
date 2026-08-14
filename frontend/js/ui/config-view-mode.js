/**
 * Historical configuration view mode — read-only canvas preview of a past commit.
 *
 * When `store.control.viewingCommitId` differs from the applied node, the UI shows:
 *   - orange frame + shield on the optical table (no canvas interaction)
 *   - compact badge beside the canvas with commit id + Set as reference / Apply / Return
 *
 * During "Apply on bench", the badge switches to a live step list while the
 * orange preview chrome stays up until every step finishes.
 */
import {
    controlCapabilities,
    getAppliedCommitId,
    isConfigViewMode,
    isReconcileRunning,
    isUnadopted,
    isUncommittedRuntime,
    runtimeEditableOrMessage,
} from '../control/control-state.js';
import { shortCommitId } from '../api/control.js';
import { store } from '../state/store.js';
import { updatePencilToolButtonUi } from '../canvas/guides.js';

let _els = {};
let _onApply = async () => {};
let _onSetReference = async () => {};
let _onReturn = async () => {};

export { isConfigViewMode, isUncommittedRuntime, isRuntimeEditable } from '../control/control-state.js';
export { runtimeEditableOrMessage };

/**
 * @param {{
 *   onApply?: () => void|Promise<void>,
 *   onSetReference?: () => void|Promise<void>,
 *   onReturn?: () => void|Promise<void>,
 * }} handlers
 */
export function initConfigViewMode(handlers = {}) {
    if (typeof handlers.onApply === 'function') _onApply = handlers.onApply;
    if (typeof handlers.onSetReference === 'function') {
        _onSetReference = handlers.onSetReference;
    }
    if (typeof handlers.onReturn === 'function') _onReturn = handlers.onReturn;

    _els = {
        stage: document.getElementById('canvas-stage'),
        wrapper: document.getElementById('canvas-wrapper'),
        shield: document.getElementById('canvas-view-shield'),
        badge: document.getElementById('config-view-badge'),
        viewingPanel: document.getElementById('config-view-viewing'),
        applyingPanel: document.getElementById('config-view-applying'),
        commitId: document.getElementById('config-view-commit-id'),
        sourceLabel: document.getElementById('config-view-source'),
        applyingId: document.getElementById('config-view-applying-id'),
        reconcileProgress: document.getElementById('config-view-reconcile-progress'),
        reconcileSteps: document.getElementById('config-view-reconcile-steps'),
        setRefBtn: document.getElementById('config-view-set-ref-btn'),
        applyBtn: document.getElementById('config-view-apply-btn'),
        returnBtn: document.getElementById('config-view-return-btn'),
    };

    _els.setRefBtn?.addEventListener('click', () => void _onSetReference());
    _els.applyBtn?.addEventListener('click', () => void _onApply());
    _els.returnBtn?.addEventListener('click', () => void _onReturn());
}

function renderReconcileSteps(progress) {
    if (!_els.reconcileSteps || !progress) return;
    _els.reconcileSteps.innerHTML = '';
    progress.steps.forEach((step) => {
        const li = document.createElement('li');
        li.className = 'config-view-reconcile__step';
        if (step.status === 'active') li.classList.add('is-active');
        if (step.status === 'done') li.classList.add('is-done');
        if (step.status === 'error') li.classList.add('is-error');

        const marker = document.createElement('span');
        marker.className = 'config-view-reconcile__marker';
        if (step.status === 'done') marker.textContent = '✓';
        else if (step.status === 'active') marker.textContent = '▶';
        else if (step.status === 'error') marker.textContent = '!';
        else marker.textContent = '·';

        const label = document.createElement('span');
        label.className = 'config-view-reconcile__label';
        label.textContent = step.label || step.action || 'step';

        li.appendChild(marker);
        li.appendChild(label);
        _els.reconcileSteps.appendChild(li);
    });
}

/** Keep reconcile progress panel in sync while Apply on bench runs. */
export function syncReconcileProgressUi() {
    const progress = store.control.reconcileProgress;
    const applying = isReconcileRunning();
    if (_els.badge) _els.badge.classList.toggle('is-applying', applying);
    if (_els.viewingPanel) _els.viewingPanel.hidden = applying;
    if (_els.applyingPanel) _els.applyingPanel.hidden = !applying;
    if (!applying || !progress) return;
    if (_els.applyingId) {
        _els.applyingId.textContent = shortCommitId(progress.commitId || '') || '—';
    }
    if (_els.reconcileProgress) {
        const total = Math.max(1, Number(progress.total) || 1);
        const current = Math.min(total, Number(progress.current) || 1);
        _els.reconcileProgress.textContent = `Step ${current} of ${total}`;
    }
    renderReconcileSteps(progress);
}

export function updateConfigViewUi() {
    const active = isConfigViewMode();
    const applying = isReconcileRunning();
    const unadopted = isUnadopted();
    const caps = controlCapabilities();
    const viewingId = store.control.viewingCommitId;
    const appliedId = getAppliedCommitId();
    const viewingOther = Boolean(viewingId && viewingId !== appliedId);

    _els.stage?.classList.toggle('is-config-view', active);
    _els.wrapper?.classList.toggle('is-config-view', active);
    document.getElementById('control-graph-panel')?.classList.toggle('is-config-view', active);
    document.body.classList.toggle('is-config-view', active);

    _els.stage?.classList.toggle('is-uncommitted-runtime', isUncommittedRuntime());

    if (_els.shield) {
        const showShield = active && !applying;
        _els.shield.hidden = !showShield;
        _els.shield.setAttribute('aria-hidden', showShield ? 'false' : 'true');
    }

    if (_els.badge) {
        _els.badge.hidden = !active;
    }

    if (_els.commitId && !applying) {
        _els.commitId.textContent = viewingId ? shortCommitId(viewingId) : '—';
    }

    if (_els.sourceLabel && !applying) {
        const src = store.control.snapshotSource;
        if (src?.source === 'catalog') {
            const name = src.display_name || src.pin_id || 'pin';
            _els.sourceLabel.hidden = false;
            _els.sourceLabel.classList.remove('is-local');
            _els.sourceLabel.textContent = `Catalog · ${name}`;
            _els.sourceLabel.title =
                'Frozen snapshot pin — does not follow local branch heads';
        } else if (viewingId) {
            const repo = store.control.repoId || 'repo';
            const branch = store.control.branch || 'main';
            _els.sourceLabel.hidden = false;
            _els.sourceLabel.classList.add('is-local');
            _els.sourceLabel.textContent = `Local · ${repo} @ ${branch}`;
            _els.sourceLabel.title = 'Local control-repo commit (live VC graph)';
        } else {
            _els.sourceLabel.hidden = true;
            _els.sourceLabel.textContent = '';
        }
    }

    // Set as reference: soft retarget (no motion). Available whenever we can
    // adopt and the previewed node is not already the applied reference.
    if (_els.setRefBtn) {
        const canSet =
            Boolean(caps.canSetNode) && (unadopted || viewingOther);
        _els.setRefBtn.hidden = !active || !canSet;
        _els.setRefBtn.disabled = !canSet || applying;
        _els.setRefBtn.textContent = unadopted ? 'Set as reference' : 'Set as reference';
        _els.setRefBtn.title = unadopted
            ? 'Use this node as your diff base (no robot motion)'
            : 'Retarget the diff base to this node without moving the bench';
        _els.setRefBtn.classList.toggle('config-view-badge__btn--primary', unadopted || canSet);
    }

    // Apply on bench: hard reconcile. Hidden while unadopted (establish a
    // reference first). Dirty working table still blocks via the apply handler.
    if (_els.applyBtn) {
        const showApply = active && !unadopted && viewingOther;
        _els.applyBtn.hidden = !showApply;
        _els.applyBtn.disabled = !showApply || applying;
        _els.applyBtn.textContent = 'Apply on bench';
        _els.applyBtn.title = 'Reconcile the physical bench to this configuration';
        _els.applyBtn.classList.toggle('config-view-badge__btn--primary', false);
    }

    const saveBtn = document.getElementById('control-save-config-btn');
    const forkBtn = document.getElementById('control-fork-btn');
    if (saveBtn) {
        saveBtn.disabled = active;
    }
    if (forkBtn) {
        // Fork stays available while previewing so you can branch from the node.
        forkBtn.disabled = applying;
    }

    if (!active && !applying) {
        store.pencilToolActive = false;
        updatePencilToolButtonUi();
    }

    syncReconcileProgressUi();
}

/** Alias used by control-panel.js */
export function syncConfigViewMode() {
    updateConfigViewUi();
}

/** Disable badge actions during long-running checkout. */
export function setConfigViewActionsBusy(busy) {
    if (_els.setRefBtn) _els.setRefBtn.disabled = busy;
    if (_els.applyBtn) _els.applyBtn.disabled = busy;
    if (_els.returnBtn) _els.returnBtn.disabled = busy;
}

/**

 * Historical configuration view mode — read-only canvas preview of a past commit.

 *

 * When `store.control.viewingCommitId` differs from branch HEAD, the UI shows:

 *   - orange frame + shield on the optical table (no canvas interaction)

 *   - compact badge beside the canvas with commit id + Apply / HEAD actions

 *

 * During "Apply on bench", the badge switches to a live step list while the

 * orange preview chrome stays up until every step finishes.

 */

import {

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

let _onReturn = async () => {};



export { isConfigViewMode, isUncommittedRuntime, isRuntimeEditable } from '../control/control-state.js';

export { runtimeEditableOrMessage };



/**

 * @param {{ onApply?: () => void|Promise<void>, onReturn?: () => void|Promise<void> }} handlers

 */

export function initConfigViewMode(handlers = {}) {

    if (typeof handlers.onApply === 'function') _onApply = handlers.onApply;

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

        applyBtn: document.getElementById('config-view-apply-btn'),

        returnBtn: document.getElementById('config-view-return-btn'),

    };



    _els.applyBtn?.addEventListener('click', () => void _onApply());

    _els.returnBtn?.addEventListener('click', () => void _onReturn());

}



function renderReconcileSteps(progress) {

    if (!_els.reconcileSteps || !progress) return;

    _els.reconcileSteps.innerHTML = '';

    progress.steps.forEach((step, index) => {

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

        else marker.textContent = String(index + 1);



        const label = document.createElement('span');

        label.className = 'config-view-reconcile__label';

        label.textContent = step.label;



        li.appendChild(marker);

        li.appendChild(label);

        _els.reconcileSteps.appendChild(li);

    });

}



/** Refresh the applying / step-list panel from `store.control.reconcileProgress`. */

export function syncReconcileProgressUi() {

    const progress = store.control.reconcileProgress;

    const applying = Boolean(progress && progress.active);



    _els.badge?.classList.toggle('is-applying', applying);



    if (_els.viewingPanel) _els.viewingPanel.hidden = applying;

    if (_els.applyingPanel) _els.applyingPanel.hidden = !applying;



    if (applying && progress) {

        const id = progress.commitId ? shortCommitId(progress.commitId) : '—';

        if (_els.applyingId) _els.applyingId.textContent = id;

        const current = progress.currentIndex >= 0 ? progress.currentIndex + 1 : 0;

        const total = progress.steps.length;

        if (_els.reconcileProgress) {

            _els.reconcileProgress.textContent =

                current > 0

                    ? `Step ${current} of ${total}`

                    : `Preparing ${total} step(s)…`;

        }

        renderReconcileSteps(progress);

    }



    syncConfigViewMode();

}



/** Sync DOM chrome from `store.control` — safe to call frequently. */

export function syncConfigViewMode() {

    const applying = isReconcileRunning();

    const active = isConfigViewMode() || applying;

    const uncommitted = isUncommittedRuntime();

    const graphPanel = document.getElementById('control-graph-panel');



    _els.stage?.classList.toggle('is-config-view', active);

    _els.stage?.classList.toggle('is-applying-config', applying);

    _els.stage?.classList.toggle('is-uncommitted-runtime', uncommitted);

    _els.wrapper?.classList.toggle('is-config-view', active);

    graphPanel?.classList.toggle('is-config-view', active);

    graphPanel?.classList.toggle('is-applying-config', applying);

    graphPanel?.classList.toggle('is-uncommitted-runtime', uncommitted);

    document.body.classList.toggle('is-config-view', active);

    document.body.classList.toggle('is-applying-config', applying);



    if (_els.shield) {

        const showShield = active && !applying;

        _els.shield.hidden = !showShield;

        _els.shield.setAttribute('aria-hidden', showShield ? 'false' : 'true');

    }



    if (_els.badge) {

        _els.badge.hidden = !active;

    }



    if (_els.commitId && !applying) {

        _els.commitId.textContent = store.control.viewingCommitId

            ? shortCommitId(store.control.viewingCommitId)

            : '—';

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
        } else if (store.control.viewingCommitId) {
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



    if (_els.applyBtn) {

        const setAsNode = isUnadopted();

        _els.applyBtn.textContent = setAsNode ? 'Set as node' : 'Apply on bench';

        _els.applyBtn.title = setAsNode

            ? 'Set this node as your current node (no robot motion)'

            : 'Apply this configuration on the bench';

    }



    const saveBtn = document.getElementById('control-save-config-btn');

    const forkBtn = document.getElementById('control-fork-btn');

    if (saveBtn) {

        saveBtn.disabled = active;

        saveBtn.title = active

            ? 'Return to bench before committing'

            : 'Commit layout';

    }

    if (forkBtn) {

        forkBtn.disabled = active;

        forkBtn.title = active ? 'Return to bench before forking' : 'Fork branch';

    }



    if (active && store.pencilToolActive) {

        store.pencilToolActive = false;

        updatePencilToolButtonUi();

    }

}



/** Disable badge actions during long-running checkout. */

export function setConfigViewActionsBusy(busy) {

    if (_els.applyBtn) _els.applyBtn.disabled = busy;

    if (_els.returnBtn) _els.returnBtn.disabled = busy;

}



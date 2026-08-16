/**

 * Configuration version control — Config workspace tab + bench status strip.

 */

import { store } from '../state/store.js';
import { activateWorkspaceTab } from './workspace-tabs.js';

import {
    adoptConfiguration,
    commitConfiguration,
    fetchAllControlNodes,
    fetchControlHistory,
    fetchControlRepos,
    fetchControlStatus,

    forkControlBranch,

    finalizeHardCheckout,

    previewHardCheckout,

    shortCommitId,

    softCheckoutConfiguration,

    stashChanges,

    finalizeStash,

    popStash,

    finalizeStashPop,

    dropStash,

} from '../api/control.js';

import { submitPublishRequest, fetchCatalogPins } from '../api/catalog.js';

import { runReconcilePlan } from '../control/reconcile-runner.js';

import {

    layoutCommitGraph,

    renderControlGraph,

    renderControlGraphLoading,

} from './control-graph.js';

import {
    initConfigViewMode,
    isConfigViewMode,
    setConfigViewActionsBusy,
    syncConfigViewMode,
} from './config-view-mode.js';

import {
    controlCapabilities,
    getAppliedBranch,
    getAppliedCommitId,
    getStash,
    hasStash,
    isDetached,
    isDirty,
    isUnadopted,
    isUncommittedRuntime,
    normalizeControlViewState,
    runtimeEditableOrMessage,
    setAppliedPointer,
    setPreviewOverlay,
    clearPreviewOverlay,
    setViewingCommit,
    syncControlStatus,
} from '../control/control-state.js';

import { handleBranchSelectChange } from './control-branch.js';

import {
    handleRepoSelectChange,
    promptCreateControlRepo,
    promptStartVersionControl,
} from './control-repo.js';

import { log } from './log.js';

import { getCatalogRow } from '../component-model.js';

import { showConfirmationModal, showErrorModal } from './modals.js';
import { runBringBenchWizard } from './bring-bench-wizard.js';



let _deps = {

    fetchLabState: async () => {},

    render: () => {},

};



let _els = {};

const DEFAULT_REPO_ID = 'default';
const DEFAULT_REPO_DISPLAY = 'optical-cavity';

function _headLabelParts() {
    if (!_els.headLabel) return { tag: null, id: null };
    return {
        tag: _els.headLabel.querySelector('.control-graph-head-pill__tag'),
        id: _els.headLabel.querySelector('.control-graph-head-pill__id'),
    };
}

function _statusPillParts() {
    if (!_els.statusPill) return { tag: null, id: null };
    return {
        tag: _els.statusPill.querySelector('.control-graph-head-pill__tag'),
        id: _els.statusPill.querySelector('.control-graph-head-pill__id'),
    };
}

function _syncStatusStripFromHeadLabel() {
    if (!_els.statusPill || !_els.headLabel) return;
    const src = _headLabelParts();
    const dst = _statusPillParts();
    if (src.tag && dst.tag) dst.tag.textContent = src.tag.textContent;
    if (src.id && dst.id) dst.id.textContent = src.id.textContent;
    _els.statusPill.classList.toggle(
        'is-uncommitted',
        _els.headLabel.classList.contains('is-uncommitted'),
    );
    _els.statusPill.title = _els.headLabel.title || '';
    if (_els.statusBranch) {
        _els.statusBranch.textContent = store.control.branch || '—';
    }
    if (_els.statusLabel) {
        const active = Boolean(store.control.repoId);
        _els.statusLabel.textContent = active ? 'Config' : 'Version control';
    }
}

/** Pick the right HEAD-pill rendering for the current working state. */
function updateHeadPill() {
    // Inline "pick a node" guidance: shown only while unadopted (no current
    // node established yet), alongside the HEAD pill's "No node — pick".
    if (_els.adoptNotice) _els.adoptNotice.hidden = !isUnadopted();
    if (!_els.headLabel) return;
    const head = store.control.liveHeadId;
    if (isUnadopted()) {
        updateHeadLabel({ unadopted: true });
    } else if (!head && isUncommittedRuntime()) {
        updateHeadLabel({ uncommitted: true });
    } else if (isDetached()) {
        updateHeadLabel({ headId: getAppliedCommitId() || head, detached: true });
    } else if (head) {
        updateHeadLabel({ headId: head });
    } else {
        updateHeadLabel();
    }
}

function updateHeadLabel({ uncommitted = false, headId = null, detached = false, unadopted = false } = {}) {
    if (!_els.headLabel) return;
    const { tag, id } = _headLabelParts();
    _els.headLabel.classList.toggle('is-uncommitted', uncommitted || unadopted);
    if (unadopted) {
        if (tag) tag.textContent = 'No node';
        if (id) id.textContent = 'pick';
        _els.headLabel.title =
            'No current reference — preview a node and "Set as reference", or Commit the current bench';
        _syncStatusStripFromHeadLabel();
        return;
    }
    if (uncommitted) {
        if (tag) tag.textContent = 'Working';
        if (id) id.textContent = 'table';
        _els.headLabel.title =
            'Uncommitted layout on this branch — Commit to start version history';
        _syncStatusStripFromHeadLabel();
        return;
    }
    if (detached) {
        if (tag) tag.textContent = 'AT';
        if (id) id.textContent = headId ? shortCommitId(headId) : '—';
        _els.headLabel.title = headId
            ? `Detached — bench is at ${headId} (not a branch HEAD). Fork to edit.`
            : 'Detached from branch HEAD';
        _syncStatusStripFromHeadLabel();
        return;
    }
    if (tag) tag.textContent = 'HEAD';
    if (headId) {
        if (id) id.textContent = shortCommitId(headId);
        _els.headLabel.title = `Branch HEAD · ${headId}`;
    } else {
        if (id) id.textContent = '—';
        _els.headLabel.title = 'Branch HEAD';
    }
    _syncStatusStripFromHeadLabel();
}

export function initControlPanel(deps = {}) {

    _deps = { ..._deps, ...deps };

    _els = {

        graphPanel: document.getElementById('control-graph-panel'),

        vcStart: document.getElementById('control-vc-start'),

        vcStartBtn: document.getElementById('control-vc-start-btn'),

        stopVcBtn: document.getElementById('control-stop-vc-btn'),

        repoSelect: document.getElementById('control-repo-select'),

        newRepoBtn: document.getElementById('control-new-repo-btn'),

        branchSelect: document.getElementById('control-branch-select'),

        saveBtn: document.getElementById('control-save-config-btn'),

        publishBtn: document.getElementById('control-publish-btn'),

        catalogPinBtn: document.getElementById('control-catalog-pin-btn'),

        forkBtn: document.getElementById('control-fork-btn'),

        planPreview: document.getElementById('control-plan-preview'),

        adoptNotice: document.getElementById('control-adopt-notice'),

        headLabel: document.getElementById('control-head-label'),

        stashBox: document.getElementById('control-stash-box'),

        stashBtn: document.getElementById('control-stash-btn'),

        stashPresent: document.getElementById('control-stash-present'),

        stashPopBtn: document.getElementById('control-stash-pop-btn'),

        stashDropBtn: document.getElementById('control-stash-drop-btn'),

        statusBar: document.getElementById('control-vc-status'),
        statusOpen: document.getElementById('control-vc-status-open'),
        statusLabel: document.getElementById('control-vc-status-label'),
        statusMeta: document.getElementById('control-vc-status-meta'),
        statusBranch: document.getElementById('control-vc-status-branch'),
        statusPill: document.getElementById('control-vc-status-pill'),
        openSidebarBtn: document.getElementById('control-vc-open-sidebar-btn'),

    };



    if (!_els.graphPanel) return;



    initConfigViewMode({

        onApply: () => onApplyOnBench(),

        onSetReference: () => {
            const id = store.control.selectedCommitId || store.control.viewingCommitId;
            if (id) onSetAsReference(id);
        },

        onReturn: () => returnToLiveHead(),

    });



    const openConfigTab = () => activateWorkspaceTab('config');
    _els.statusOpen?.addEventListener('click', openConfigTab);
    _els.openSidebarBtn?.addEventListener('click', openConfigTab);

    _els.saveBtn?.addEventListener('click', () => void onSaveConfiguration());

    _els.publishBtn?.addEventListener('click', () => void onPublishToCatalog());

    _els.catalogPinBtn?.addEventListener('click', () => void onStartFromCatalogPin());

    _els.forkBtn?.addEventListener('click', () => void onForkBranch());

    _els.stashBtn?.addEventListener('click', () => void onStashChanges());

    _els.stashPopBtn?.addEventListener('click', () => void onPopStash());

    _els.stashDropBtn?.addEventListener('click', () => void onDropStash());

    const startVcDeps = () => ({
        onEnter: (repoId) => enterRepo(repoId),
        onRefresh: async () => {
            await refreshControlPanel();
            await softDefaultMainHeadReference();
        },
        onRuntimeRefresh: async () => {
            await _deps.fetchLabState();
            _deps.render();
            updateConfigViewUi();
        },
    });

    _els.vcStartBtn?.addEventListener('click', () => {
        void promptStartVersionControl(startVcDeps());
    });

    _els.stopVcBtn?.addEventListener('click', () => stopVersionControl());

    _els.newRepoBtn?.addEventListener('click', () => {

        void promptCreateControlRepo({

            onRefresh: () => refreshControlPanel(),

            onRuntimeRefresh: async () => {

                await _deps.fetchLabState();

                _deps.render();

                updateConfigViewUi();

            },

            onCreated: (repoId) => {

                log(`Created configuration repo "${repoId}" — Commit to start history`, 'info');

            },

        });

    });

    _els.repoSelect?.addEventListener('change', () => {

        const previousRepo = store.control.repoId || 'default';

        const newRepo = _els.repoSelect?.value || 'default';

        if (newRepo === previousRepo) return;

        handleRepoSelectChange(_els.repoSelect, newRepo, previousRepo, {

            onRefresh: async () => {
                await refreshControlPanel();
                await softDefaultMainHeadReference();
            },

            onRuntimeRefresh: async () => {

                await _deps.fetchLabState();

                _deps.render();

                updateConfigViewUi();

            },

        });

    });

    _els.branchSelect?.addEventListener('change', () => {

        const previousBranch = store.control.branch || 'main';

        const newBranch = _els.branchSelect?.value || 'main';

        handleBranchSelectChange(_els.branchSelect, newBranch, previousBranch, {

            onSwitch: async (branch, headId) => {

                await _deps.fetchLabState();

                _deps.render();

                await refreshControlPanel();

                if (headId) {

                    log(

                        `Viewing branch ${branch} at HEAD ${shortCommitId(headId)} (view mode)`,

                        'info',

                    );

                }

            },

        });

    });



    // Opt-in: start with version control off. Nothing is fetched until the user
    // explicitly enters a repo via "Start version control".
    setVcActive(Boolean(store.control.repoId));
    if (store.control.repoId) {
        void refreshControlPanel();
    }

}

/** Toggle the panel between the start screen and the live toolbar. */
function setVcActive(active) {
    _els.graphPanel?.classList.toggle('vc-inactive', !active);
    _els.statusBar?.classList.toggle('vc-inactive', !active);
    if (_els.vcStart) _els.vcStart.hidden = active;
    if (_els.statusMeta) _els.statusMeta.hidden = !active;
    if (_els.statusLabel) {
        _els.statusLabel.textContent = active ? 'Config' : 'Version control';
    }
    if (active && _els.statusBranch) {
        _els.statusBranch.textContent = store.control.branch || '—';
    }
}

/** Enter a repo (opt-in). The physical bench is untouched. */
function enterRepo(repoId) {
    if (!repoId) return;
    store.control.repoId = repoId;
    // Leave the branch unresolved until soft-default points at main HEAD.
    store.control.branch = null;
    clearPreviewOverlay();
    store.control.viewingCommitId = null;
    store.control.selectedCommitId = null;
    store.control.snapshotSource = null;
    store.control.liveHeadId = null;
    store.control.working = null;
    setVcActive(true);
    activateWorkspaceTab('config');
    void (async () => {
        await refreshControlPanel();
        await softDefaultMainHeadReference();
        await _deps.fetchLabState();
        _deps.render();
        updateConfigViewUi();
    })();
}

/** Leave version control: hide the panel. Bench and ownership are untouched. */
function stopVersionControl() {
    store.control.repoId = null;
    clearPreviewOverlay();
    store.control.viewingCommitId = null;
    store.control.selectedCommitId = null;
    store.control.snapshotSource = null;
    store.control.working = null;
    setVcActive(false);
    _deps.render?.();
    updateConfigViewUi();
}



/** @deprecated Use isConfigViewMode from config-view-mode.js */
export function isViewingHistoricalConfiguration() {
    return isConfigViewMode();
}



export async function refreshControlPanel() {

    if (!_els.graphPanel) return;

    if (!store.control.repoId) { setVcActive(false); return; }

    store.control.loading = true;

    renderControlGraphLoading(_els.graphPanel);

    try {

        const reposPayload = await fetchControlRepos();

        store.control.repos = reposPayload.repos || [];

        populateRepoSelect(store.control.repos);

        const status = await fetchControlStatus();

        const heads = status.heads || {};

        store.control.heads = heads;

        syncControlStatus(status);

        // On fresh entry / repo switch the branch is left unresolved (null); adopt
        // the branch the bench is actually applied to so the HEAD pill and branch
        // dropdown reflect where we really are, instead of defaulting to 'main'.
        const appliedBranch = getAppliedBranch();
        if (!store.control.branch) {
            store.control.branch = appliedBranch || 'main';
        }

        const branch = store.control.branch || 'main';

        const headForBranch = heads[branch] ?? null;

        if (headForBranch) {

            store.control.liveHeadId = headForBranch;

        }

        console.debug(
            '[control] refresh',
            {
                repo: store.control.repoId,
                branch,
                appliedBranch,
                appliedId: getAppliedCommitId(),
                liveHeadId: store.control.liveHeadId,
                onHead: status.working?.on_head,
                detached: status.working?.detached,
                dirty: status.working?.dirty,
                unadopted: status.working?.unadopted,
                ownsBench: status.working?.owns_bench,
                heads,
            },
        );



        populateBranchSelect(Object.keys(heads));



        const [allHistory, branchHistory] = await Promise.all([

            fetchAllControlNodes(),

            fetchControlHistory(branch),

        ]);

        store.control.graphNodes = allHistory.nodes || [];

        store.control.nodes = branchHistory.nodes || [];

        if (branchHistory.head && typeof branchHistory.head === 'string') {

            store.control.liveHeadId = branchHistory.head;

        }

        normalizeControlViewState();

        renderCommitGraph();

        updateConfigViewUi();

    } catch (e) {

        console.warn('[control-panel] refresh failed', e);

        renderControlGraph(_els.graphPanel, { nodes: [] }, {});

        const emptyEl = _els.graphPanel.querySelector('#control-graph-empty');

        if (emptyEl) {

            emptyEl.hidden = false;

            emptyEl.textContent = 'Could not load configuration history.';

        }

    } finally {

        store.control.loading = false;

    }

}



/**
 * Lightweight refresh of just the working-tree flags (dirty / detached / stash)
 * without reloading the whole commit graph. Called by the lab-state poll after a
 * command settles so the Stash button and HEAD pill reflect live edits.
 */
export async function refreshControlWorkingState() {
    if (!_els.graphPanel) return;
    if (!store.control.repoId) return;
    try {
        const status = await fetchControlStatus();
        syncControlStatus(status);
        if (status.heads) store.control.heads = status.heads;
        // Update only the HEAD pill + stash controls — avoid a full graph
        // re-render (which would reset scroll) on every command settle.
        updateHeadPill();
        updateConfigViewUi();
    } catch (e) {
        // Non-fatal: the next full refresh will reconcile.
    }
}

function populateRepoSelect(repos) {

    if (!_els.repoSelect) return;

    const current = store.control.repoId || 'default';

    _els.repoSelect.innerHTML = '';

    const list = repos?.length
        ? repos
        : [{ repo_id: DEFAULT_REPO_ID, display_name: DEFAULT_REPO_DISPLAY }];

    list.forEach((repo) => {

        const opt = document.createElement('option');

        opt.value = repo.repo_id;

        const count = repo.configuration_count ?? 0;

        opt.textContent = repo.display_name || repo.repo_id;
        if (count === 0) {
            opt.title = 'No commits yet — use Commit to start history';
        } else {
            opt.title = `${count} configuration${count === 1 ? '' : 's'}`;
        }

        _els.repoSelect.appendChild(opt);

    });

    if (!list.some((r) => r.repo_id === current)) {

        const opt = document.createElement('option');

        opt.value = current;

        opt.textContent = current;

        _els.repoSelect.appendChild(opt);

    }

    _els.repoSelect.value = list.some((r) => r.repo_id === current)

        ? current

        : list[0]?.repo_id || 'default';

    store.control.repoId = _els.repoSelect.value;

}



function populateBranchSelect(branchNames) {

    if (!_els.branchSelect) return;

    const names = branchNames.length ? branchNames : ['main'];

    const current = store.control.branch || 'main';

    _els.branchSelect.innerHTML = '';

    names.forEach((name) => {

        const opt = document.createElement('option');

        opt.value = name;

        opt.textContent = name;

        _els.branchSelect.appendChild(opt);

    });

    if (!names.includes(current)) {

        const opt = document.createElement('option');

        opt.value = current;

        opt.textContent = current;

        _els.branchSelect.appendChild(opt);

    }

    _els.branchSelect.value = names.includes(current) ? current : names[0];

    store.control.branch = _els.branchSelect.value;

}



function renderCommitGraph() {

    const nodes = store.control.graphNodes || store.control.nodes || [];

    const head = store.control.liveHeadId;

    const branch = store.control.branch || 'main';



    updateHeadPill();



    const layout = layoutCommitGraph(nodes, {

        heads: store.control.heads || {},

        activeBranch: branch,

        headId: head,

    });



    renderControlGraph(_els.graphPanel, layout, {

        headId: head,

        appliedId: getAppliedCommitId(),

        selectedId: store.control.selectedCommitId,

        viewingId: store.control.viewingCommitId,

        uncommitted: isUncommittedRuntime(),

        onNodeClick: (id, node) => selectCommitNode(id, node),

    });

}



function selectCommitNode(commitId, node) {
    // Soft preview is always allowed while dirty — Set as reference can retarget
    // the base without motion. Hard Apply on bench still prompts to stash/commit.
    store.control.selectedCommitId = commitId;
    store.control.snapshotSource = {
        source: 'local',
        repo_id: store.control.repoId || undefined,
        branch: store.control.branch || 'main',
        configuration_id: commitId,
    };

    void viewConfiguration(commitId, node);
}

/**
 * Offer the user a way out when they try to leave a dirty working table:
 * stash the changes aside, or commit them. Cancel leaves the bench untouched.
 * @param {string} action human-readable description of the blocked action
 */
function promptResolveDirty(action) {
    const message =
        `You have <strong>uncommitted changes</strong> on the bench.<br><br>` +
        `Commit or stash them before ${escapeHtml(action)}.`;
    showConfirmationModal(
        message,
        () => {
            void onStashChanges();
        },
        () => {},
        { confirmLabel: 'Stash changes', cancelLabel: 'Cancel' },
    );
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}



async function viewConfiguration(commitId, node) {

    if (!commitId) return;

    try {

        const res = await softCheckoutConfiguration(commitId);

        // Read-only preview: stash the node's configuration as an overlay. The
        // live bench is NOT touched, so no fetchLabState / ghost resync. Viewing
        // the applied node shows the live bench directly (no overlay).
        const appliedNow = getAppliedCommitId();
        if (commitId === appliedNow) {
            clearPreviewOverlay();
        } else {
            setPreviewOverlay(res && res.configuration, res && res.metadata);
        }

        setViewingCommit(commitId);

        _deps.render();

        renderCommitGraph();

        updateConfigViewUi();

        const head = store.control.liveHeadId;

        const applied = getAppliedCommitId();

        if (commitId === applied) {

            log(

                `At applied bench configuration ${shortCommitId(commitId)}`,

                'info',

            );

        } else if (head && commitId === head) {

            log(

                `Viewing branch HEAD ${shortCommitId(commitId)} — ${node?.message || '(no message)'}`,

                'info',

            );

        } else {

            log(`Viewing configuration ${shortCommitId(commitId)} (soft checkout)`, 'info');

        }

    } catch (e) {

        console.error('[control-panel] checkout failed', e);

        showErrorModal('Checkout Failed', e.message || String(e));

    }

}



function formatStepTargetLabel(tagId) {
    const id = tagId == null || tagId === '' ? '' : String(tagId);
    if (!id) return '—';
    const name = getCatalogRow(id)?.name;
    if (name && name !== id) {
        return `${escapeHtml(name)} <span style="opacity:0.65">(${escapeHtml(id)})</span>`;
    }
    return escapeHtml(id);
}

function formatStepParamHint(step) {
    const params = step && typeof step.parameters === 'object' ? step.parameters : null;
    if (!params) return '';
    const bits = [];
    if (params.motor_id != null) bits.push(`motor ${params.motor_id}`);
    if (params.angle_deg != null && Number.isFinite(Number(params.angle_deg))) {
        bits.push(`${Number(params.angle_deg).toFixed(1)}°`);
    }
    if (params.exposure_time_ms != null && Number.isFinite(Number(params.exposure_time_ms))) {
        bits.push(`${Number(params.exposure_time_ms)} ms`);
    }
    if (
        params.target_x != null &&
        params.target_y != null &&
        Number.isFinite(Number(params.target_x)) &&
        Number.isFinite(Number(params.target_y))
    ) {
        bits.push(
            `(${Number(params.target_x).toFixed(1)}, ${Number(params.target_y).toFixed(1)})`,
        );
    }
    return bits.length ? ` · ${escapeHtml(bits.join(' · '))}` : '';
}

function formatPlanPreview(plan) {
    if (!plan?.length) {
        return '<strong>No motion required</strong> — the bench already matches this configuration.';
    }
    // Use <ol> numbering only — do not prefix "${i}." inside <li> (that doubles digits).
    const items = plan
        .map((step) => {
            const action = escapeHtml(step.action || '?');
            const target = formatStepTargetLabel(step.target_id);
            const hint = formatStepParamHint(step);
            return `<li style="text-align:left">${action} → ${target}${hint}</li>`;
        })
        .join('');
    return `<strong>${plan.length} primitive step(s):</strong><ol style="text-align:left;margin:8px 0 0;padding-left:1.4em">${items}</ol>`;
}



function showPlanPreview(html) {

    if (_els.planPreview) {

        _els.planPreview.innerHTML = html;

        _els.planPreview.hidden = false;

    }

}



function hidePlanPreview() {

    if (_els.planPreview) {

        _els.planPreview.hidden = true;

        _els.planPreview.innerHTML = '';

    }

}



function updateConfigViewUi() {
    syncConfigViewMode();
    if (!isConfigViewMode()) {
        hidePlanPreview();
    }
    updateStashUi();
}

/** Reflect dirty / detached / stash state on the toolbar controls.
 *
 * All gating decisions come from {@link controlCapabilities} (backend rules +
 * preview gate) so the toolbar can never disagree with server enforcement. */
function updateStashUi() {
    const caps = controlCapabilities();
    const { dirty, detached, viewing, hasStash: hasStashNow } = caps;
    const stash = getStash();

    // Combined stash box: hidden unless there's something to stash or a stash to
    // manage. Amber only when a stash actually exists.
    if (_els.stashBox) {
        _els.stashBox.hidden = !dirty && !hasStashNow;
        _els.stashBox.classList.toggle('has-stash', hasStashNow);
        if (hasStashNow) {
            const base = stash.base_configuration_id
                ? shortCommitId(stash.base_configuration_id)
                : '—';
            _els.stashBox.title = stash.message
                ? `Stashed changes (from ${base}): ${stash.message}`
                : `Stashed changes (from ${base})`;
        } else {
            _els.stashBox.title = 'Set uncommitted bench changes aside';
        }
    }
    if (_els.stashBtn) {
        // The "Stash" create action only makes sense with no stash yet.
        _els.stashBtn.hidden = !caps.canStash;
        _els.stashBtn.disabled = !caps.canStash;
    }
    if (_els.stashPresent) {
        _els.stashPresent.hidden = !hasStashNow;
    }
    if (_els.stashPopBtn) {
        // Pop only onto a clean HEAD (changes can only land on HEAD).
        _els.stashPopBtn.disabled = !caps.canPopStash;
        _els.stashPopBtn.title = detached
            ? 'Fork a branch before popping (changes land on HEAD)'
            : dirty
                ? 'Commit or drop current changes before popping'
                : viewing
                    ? 'Return to the bench before popping'
                    : 'Restore stashed changes onto the bench';
    }
    if (_els.stashDropBtn) {
        _els.stashDropBtn.disabled = !caps.canDropStash;
        _els.stashDropBtn.title = 'Discard the stash (no bench motion)';
    }

    // Commit button: highlighted only when there is something to commit
    // (uncommitted edits, or the very first commit on a fresh table).
    if (_els.saveBtn) {
        _els.saveBtn.classList.toggle('is-active', caps.commitRecommended);
        if (!viewing) {
            _els.saveBtn.disabled = !caps.canCommit;
            _els.saveBtn.title = detached
                ? 'Fork a branch here before committing'
                : 'Commit layout';
        }
    }
    if (!viewing && _els.forkBtn) {
        _els.forkBtn.disabled = !caps.canFork;
        _els.forkBtn.title = detached
            ? 'Fork a branch here to start editing'
            : 'Fork branch';
    }

    const publishCommitId = getAppliedCommitId() || store.control.selectedCommitId;
    if (_els.publishBtn) {
        const canPublish = Boolean(publishCommitId) && !viewing;
        _els.publishBtn.disabled = !canPublish;
        _els.publishBtn.title = canPublish
            ? `Request publish of ${shortCommitId(publishCommitId)} to Snapshots`
            : 'Select or apply a commit before publishing';
    }
    if (_els.catalogPinBtn) {
        _els.catalogPinBtn.disabled = false;
        _els.catalogPinBtn.title =
            'Preview a frozen snapshot pin (separate from local branch heads)';
    }

    const graphPanel = document.getElementById('control-graph-panel');
    graphPanel?.classList.toggle('is-dirty', dirty);
    graphPanel?.classList.toggle('is-detached', detached);
}



/** "Set as reference": adopt a previewed node as the diff base, no robot motion. */
function onSetAsReference(commitId) {
    showConfirmationModal(
        `Set <code>${shortCommitId(commitId)}</code> as your reference?<br><br>` +
            'No robot motion runs — the bench stays exactly as it is. Your current ' +
            'table becomes uncommitted changes relative to this node, which you can ' +
            'then Commit, Stash, or Fork.',
        () => { void executeSetAsReference(commitId); },
        () => {},
        { confirmLabel: 'Set as reference', cancelLabel: 'Cancel' },
    );
}

/** @deprecated alias — older call sites */
function onSetAsNode(commitId) {
    onSetAsReference(commitId);
}

async function executeSetAsReference(commitId) {
    try {
        const res = await adoptConfiguration(commitId);
        clearPreviewOverlay();
        store.control.viewingCommitId = null;
        store.control.selectedCommitId = commitId;
        if (res?.applied) setAppliedPointer(res.applied);
        if (res?.branch) store.control.branch = res.branch;
        _deps.render();
        await refreshControlPanel();
        updateConfigViewUi();
        log(`Set reference to ${shortCommitId(commitId)} (no bench motion)`, 'info');
    } catch (e) {
        console.error('[control-panel] set as reference failed', e);
        showErrorModal('Set as reference failed', e.message || String(e), { kind: 'dismissible' });
    }
}

/**
 * On enter / repo switch: soft-point the applied pointer at ``main`` HEAD
 * without moving the bench. Empty repos stay unadopted until the first Commit.
 */
async function softDefaultMainHeadReference() {
    const heads = store.control.heads || {};
    const mainHead = heads.main;
    if (!mainHead) {
        log(
            `Repo "${store.control.repoId}" has no main HEAD yet — Commit to create a root, ` +
                'or Set as reference on another branch tip.',
            'info',
        );
        return;
    }
    if (!isUnadopted() && getAppliedCommitId() === mainHead) {
        store.control.branch = 'main';
        return;
    }
    try {
        const res = await adoptConfiguration(mainHead);
        if (res?.applied) setAppliedPointer(res.applied);
        store.control.branch = 'main';
        store.control.selectedCommitId = mainHead;
        store.control.viewingCommitId = null;
        clearPreviewOverlay();
        await refreshControlPanel();
        log(
            `Reference set to main HEAD ${shortCommitId(mainHead)} (bench unchanged). ` +
                'Preview another node and Set as reference to branch from it.',
            'info',
        );
    } catch (e) {
        console.warn('[control-panel] soft-default main HEAD failed', e);
        log(
            `Could not set main HEAD as reference: ${e.message || e}. ` +
                'Preview a node and use Set as reference.',
            'warn',
        );
    }
}

async function onApplyOnBench() {

    const commitId =

        store.control.selectedCommitId || store.control.viewingCommitId;

    if (!commitId) {

        showErrorModal('Apply on bench', 'Select a commit on the graph first.', { kind: 'dismissible' });

        return;

    }

    if (isUnadopted()) {

        onSetAsReference(commitId);

        return;

    }

    if (commitId === getAppliedCommitId()) {

        showErrorModal('Apply on bench', 'Selected commit is already the applied reference. Use Set as reference only if retargeting.', { kind: 'dismissible' });

        return;

    }

    // Hard apply moves hardware — dirty bench must be resolved first.
    if (isDirty()) {
        promptResolveDirty('applying another configuration on the bench');
        return;
    }



    try {

        const ready = await runBringBenchWizard(commitId);
        if (!ready) return;

        const preview = await previewHardCheckout(commitId);

        const plan = preview.plan || [];

        showPlanPreview(formatPlanPreview(plan));



        const stepNote =

            plan.length === 0

                ? 'Apply configuration on the bench (no primitives)?'

                : `Run ${plan.length} primitive step(s) on the bench?`;

        const message = `${stepNote}<br><br>${formatPlanPreview(plan)}`;



        showConfirmationModal(message, () => {

            void executeHardCheckout(commitId, plan, store.control.previewConfig);

        });

    } catch (e) {

        console.error('[control-panel] hard checkout preview failed', e);

        showErrorModal('Apply on bench failed', e.message || String(e), { kind: 'dismissible' });

    }

}



async function executeHardCheckout(commitId, plan, targetConfig) {

    const steps = Array.isArray(plan) ? plan : [];

    try {

        setConfigViewActionsBusy(true);

        // Stop rendering the preview overlay so live component motion is visible,
        // but keep viewingCommitId so the orange/blue apply chrome stays until done.
        store.control.previewConfig = null;

        store.control.selectedCommitId = commitId;

        _deps.render();

        await runReconcilePlan(steps, {
            title: `Apply ${shortCommitId(commitId)}`,
            commitId,
            targetConfig: targetConfig || null,
            applyOverlaysFirst: Boolean(targetConfig),
        });

        const result = await finalizeHardCheckout(commitId);

        clearPreviewOverlay();

        store.control.viewingCommitId = null;

        setAppliedPointer(

            result.applied ?? {

                configuration_id: commitId,

                branch: result.branch ?? store.control.branch,

            },

        );

        if (result.branch) {

            store.control.branch = result.branch;

        }

        store.forceGhostSync = true;

        await _deps.fetchLabState();

        _deps.render();

        await refreshControlPanel();

        hidePlanPreview();

        syncConfigViewMode();

        log(

            `Applied ${shortCommitId(commitId)} on the bench (${steps.length} step(s))`,

            'info',

        );

    } catch (e) {

        console.error('[control-panel] hard checkout failed', e);

        // A step may have already moved part of the bench — resync so the UI
        // reflects reality before surfacing the error.
        store.forceGhostSync = true;

        await _deps.fetchLabState().catch(() => {});

        await refreshControlPanel().catch(() => {});

        syncConfigViewMode();

        showErrorModal('Apply on bench failed', e.message || String(e), { kind: 'dismissible' });

    } finally {

        setConfigViewActionsBusy(false);

    }

}



async function returnToLiveHead() {

    const applied = getAppliedCommitId();

    const appliedBranch = getAppliedBranch();

    if (!applied) {

        clearPreviewOverlay();

        store.control.viewingCommitId = null;

        store.control.snapshotSource = null;

        _deps.render();

        renderCommitGraph();

        updateConfigViewUi();

        return;

    }

    try {

        if (appliedBranch) {

            store.control.branch = appliedBranch;

        }

        // Returning to the bench is now purely clearing the read-only preview
        // overlay — the live bench was never modified, so there is nothing to
        // re-apply.
        clearPreviewOverlay();

        store.control.viewingCommitId = null;

        store.control.selectedCommitId = applied;

        store.control.snapshotSource = null;

        _deps.render();

        await refreshControlPanel();

        log(`Returned to applied bench configuration ${shortCommitId(applied)}`, 'info');

    } catch (e) {

        showErrorModal('Return to bench failed', e.message || String(e));

    }

}



async function onSaveConfiguration() {

    const blocked = runtimeEditableOrMessage();

    if (blocked) {

        showErrorModal('View mode', blocked);

        return;

    }

    const defaultMsg = `commit ${new Date().toISOString().slice(0, 19).replace('T', ' ')}`;

    const message = prompt('Save configuration (commit message):', defaultMsg);

    if (message == null) return;

    try {

        const data = await commitConfiguration({

            message,

            branch: store.control.branch || 'main',

        });

        const commit = data.commit || {};

        store.control.liveHeadId = commit.id || store.control.liveHeadId;

        setAppliedPointer({

            configuration_id: commit.id,

            branch: store.control.branch,

        });

        clearPreviewOverlay();

        store.control.viewingCommitId = null;

        store.control.selectedCommitId = commit.id || null;

        await refreshControlPanel();

        log(`Saved configuration ${shortCommitId(commit.id)}`, 'info');

    } catch (e) {

        console.error('[control-panel] commit failed', e);

        showErrorModal('Save Configuration Failed', e.message || String(e));

    }

}



async function onStartFromCatalogPin() {
    if (isDirty()) {
        promptResolveDirty('opening a snapshot pin');
        return;
    }

    let pins = [];
    try {
        const payload = await fetchCatalogPins();
        pins = Array.isArray(payload.pins) ? payload.pins : [];
    } catch (e) {
        showErrorModal('Snapshots', `Could not load pins: ${e.message || e}`);
        return;
    }

    if (!pins.length) {
        showErrorModal(
            'Snapshots',
            'No approved snapshot pins on this backend. Publish from Twin or seed pins in the snapshots store.',
            { kind: 'dismissible' },
        );
        return;
    }

    const existing = document.getElementById('catalog-pin-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'catalog-pin-modal';
    overlay.style.cssText =
        'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:2990;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

    const card = document.createElement('div');
    card.style.cssText =
        'background:#181b21;border:1px solid rgba(59,130,246,0.35);border-radius:10px;padding:22px;max-width:480px;width:92%;max-height:80vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,0.65);';

    let html =
        '<h3 style="margin:0 0 6px 0;color:#e2e8f0;font-size:16px;">Start from snapshot pin</h3>' +
        '<p style="margin:0 0 14px 0;color:#94a3b8;font-size:12px;line-height:1.45;">' +
        'Frozen approved snapshots — not live local branches. Selecting a pin opens a ' +
        'read-only preview of that commit (same as viewing a graph node).</p>' +
        '<div style="display:flex;flex-direction:column;gap:8px;">';

    for (const pin of pins) {
        const id = escapeHtml(pin.pin_id);
        const name = escapeHtml(pin.display_name || pin.pin_id);
        const origin = escapeHtml(`${pin.repo_id}@${pin.branch || 'main'}`);
        const commit = escapeHtml(shortCommitId(pin.configuration_id));
        html +=
            `<button type="button" class="btn btn-secondary catalog-pin-pick" data-pin="${id}" ` +
            `style="width:100%;justify-content:flex-start;text-align:left;flex-direction:column;align-items:flex-start;gap:2px;padding:10px 12px;">` +
            `<span><span style="color:#93c5fd;font-size:10px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;margin-right:6px;">Snapshot pin</span>${name}</span>` +
            `<span style="opacity:0.65;font-size:11px;font-family:ui-monospace,monospace;">${id} · ${origin} · ${commit}</span>` +
            `</button>`;
    }
    html +=
        '</div><div style="margin-top:14px;display:flex;justify-content:flex-end;">' +
        '<button type="button" id="catalog-pin-cancel" class="btn btn-secondary" style="width:auto;padding:8px 16px;">Cancel</button>' +
        '</div>';

    card.innerHTML = html;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    document.getElementById('catalog-pin-cancel')?.addEventListener('click', () => {
        overlay.remove();
    });
    overlay.addEventListener('click', (ev) => {
        if (ev.target === overlay) overlay.remove();
    });

    card.querySelectorAll('button.catalog-pin-pick').forEach((btn) => {
        btn.addEventListener('click', () => {
            const pin = pins.find((p) => p.pin_id === btn.dataset.pin);
            overlay.remove();
            if (pin) void applyCatalogPinPreview(pin);
        });
    });
}

async function applyCatalogPinPreview(pin) {
    const repoId = String(pin.repo_id || '').trim();
    const configurationId = String(pin.configuration_id || pin.commit || '').trim();
    const pinId = String(pin.pin_id || '').trim();
    if (!repoId || !configurationId || !pinId) {
        showErrorModal('Snapshot pin', 'Pin is incomplete (missing repo or commit).');
        return;
    }

    try {
        if (!store.control.repoId) {
            setVcActive(true);
        }
        if (store.control.repoId !== repoId) {
            store.control.repoId = repoId;
            store.control.branch = pin.branch || 'main';
            store.control.liveHeadId = null;
            store.control.working = null;
            setVcActive(true);
            await refreshControlPanel();
        } else if (pin.branch) {
            store.control.branch = pin.branch;
        }

        store.control.snapshotSource = {
            source: 'catalog',
            pin_id: pinId,
            display_name: pin.display_name || pinId,
            repo_id: repoId,
            branch: pin.branch || 'main',
            configuration_id: configurationId,
        };
        store.control.selectedCommitId = configurationId;

        await viewConfiguration(configurationId, {
            message: pin.display_name || pinId,
        });
        log(
            `Snapshot pin <code>${escapeHtml(pinId)}</code> — frozen commit ` +
                `${shortCommitId(configurationId)} (does not follow local branch heads)`,
            'info',
        );
    } catch (e) {
        console.error('[control-panel] catalog pin preview failed', e);
        store.control.snapshotSource = null;
        showErrorModal('Snapshot pin', e.message || String(e));
    }
}

async function onPublishToCatalog() {
    const blocked = runtimeEditableOrMessage();
    if (blocked) {
        showErrorModal('Publish', blocked);
        return;
    }

    const configurationId =
        getAppliedCommitId() || store.control.selectedCommitId || store.control.liveHeadId;
    if (!configurationId) {
        showErrorModal(
            'Publish to Snapshots',
            'No committed configuration to publish. Commit your layout first.',
            { kind: 'dismissible' },
        );
        return;
    }

    const repo = store.control.repoId;
    const branch = store.control.branch || 'main';
    const defaultMsg = `snapshot pin ${shortCommitId(configurationId)}`;
    const message = prompt('Snapshot display name / message:', defaultMsg);
    if (message == null) return;

    const pinIdRaw = prompt(
        'Optional snapshot pin id (leave blank for auto-generated id):',
        `${repo}-${shortCommitId(configurationId)}`,
    );
    if (pinIdRaw == null) return;

    try {
        const backendId = store.labState?.active_backend_id;
        const result = await submitPublishRequest({
            repoId: repo,
            configurationId,
            branch,
            message: message.trim() || defaultMsg,
            pinId: pinIdRaw.trim() || undefined,
            requestedBy: undefined, // catalog defaults to getClientHolder('twin')
            backendId,
        });

        if (result.status === 'approved') {
            const pin = result.catalog_pin_id || '—';
            log(
                `Published as snapshot pin <code>${pin}</code> — see <a href="/wiki#backends" target="_blank">Wiki → Backends</a>`,
                'info',
            );
        } else {
            log(
                `Publish request <code>${result.request_id}</code> submitted (pending owner approval).`,
                'info',
            );
        }
    } catch (e) {
        console.error('[control-panel] publish failed', e);
        showErrorModal('Publish failed', e.message || String(e));
    }
}



async function onForkBranch() {

    // Forking is how you escape a detached HEAD, so it must stay available when
    // detached — only block it while previewing a configuration.
    if (isConfigViewMode()) {

        showErrorModal('View mode', 'Return to bench or apply on bench before forking.');

        return;

    }

    const parentId =

        (isDetached() ? getAppliedCommitId() : null) ||

        store.control.selectedCommitId || store.control.liveHeadId;

    if (!parentId) {

        showErrorModal('Fork Branch', 'Select a commit on the graph first.');

        return;

    }

    const branch = prompt('New branch name:', 'experiment');

    if (!branch || !branch.trim()) return;

    try {

        await forkControlBranch({ branch: branch.trim(), parentId });

        store.control.branch = branch.trim();

        store.control.selectedCommitId = parentId;

        // Fork lands the bench on the new branch HEAD carrying any uncommitted
        // changes — refresh the runtime/panel rather than entering view mode.

        clearPreviewOverlay();

        store.control.viewingCommitId = null;

        store.forceGhostSync = true;

        await _deps.fetchLabState();

        _deps.render();

        await refreshControlPanel();

        log(`Forked branch "${branch.trim()}" from ${shortCommitId(parentId)} — now editable`, 'info');

    } catch (e) {

        showErrorModal('Fork Failed', e.message || String(e));

    }

}



async function onStashChanges() {

    if (!isDirty()) {

        showErrorModal('Stash', 'No uncommitted changes to stash.', { kind: 'dismissible' });

        return;

    }

    const defaultMsg = `stash ${new Date().toISOString().slice(0, 19).replace('T', ' ')}`;

    const message = prompt('Stash message (optional):', defaultMsg);

    if (message === null) return;

    try {

        const preview = await stashChanges(message, { preview: true });

        const plan = preview.plan || [];

        const snapshot = preview.snapshot || {};

        showPlanPreview(formatPlanPreview(plan));

        const stepNote =

            plan.length === 0

                ? 'Stash changes (bench already matches the applied configuration — no motion)?'

                : `Stash changes and run ${plan.length} primitive step(s) to return the bench to the applied configuration?`;

        const confirmMsg = `${stepNote}<br><br>${formatPlanPreview(plan)}`;

        showConfirmationModal(

            confirmMsg,

            () => { void executeStash(message, plan, snapshot, preview.metadata || null); },

            () => { hidePlanPreview(); },

            { confirmLabel: 'Stash changes', cancelLabel: 'Cancel' },

        );

    } catch (e) {

        console.error('[control-panel] stash preview failed', e);

        showErrorModal('Stash Failed', e.message || String(e), { kind: 'dismissible' });

    }

}



async function executeStash(message, plan, snapshot, metadata) {

    const steps = Array.isArray(plan) ? plan : [];

    try {

        // Drive the bench back to the applied node one primitive at a time, then
        // record the stash from the snapshot captured before we moved anything.
        await runReconcilePlan(steps, { title: 'Stash' });

        await finalizeStash(message, snapshot, metadata);

        store.forceGhostSync = true;

        await _deps.fetchLabState();

        _deps.render();

        await refreshControlPanel();

        hidePlanPreview();

        log('Stashed uncommitted changes — bench reconciled to applied configuration', 'info');

    } catch (e) {

        console.error('[control-panel] stash failed', e);

        store.forceGhostSync = true;

        await _deps.fetchLabState().catch(() => {});

        await refreshControlPanel().catch(() => {});

        showErrorModal('Stash Failed', e.message || String(e), { kind: 'dismissible' });

    }

}



async function onPopStash() {

    if (!hasStash()) {

        showErrorModal('Pop stash', 'There is no stash to pop.', { kind: 'dismissible' });

        return;

    }

    try {

        const preview = await popStash({ preview: true });

        const plan = preview.plan || [];

        showPlanPreview(formatPlanPreview(plan));

        const stepNote =

            plan.length === 0

                ? 'Restore stashed changes (bench already matches the stash — no motion)?'

                : `Restore stashed changes and run ${plan.length} primitive step(s) on the bench?`;

        const confirmMsg = `${stepNote}<br><br>${formatPlanPreview(plan)}`;

        showConfirmationModal(

            confirmMsg,

            () => { void executePopStash(plan); },

            () => { hidePlanPreview(); },

            { confirmLabel: 'Restore changes', cancelLabel: 'Cancel' },

        );

    } catch (e) {

        console.error('[control-panel] pop stash preview failed', e);

        showErrorModal('Pop Stash Failed', e.message || String(e), { kind: 'dismissible' });

    }

}



async function executePopStash(plan) {

    const steps = Array.isArray(plan) ? plan : [];

    try {

        // Drive the bench to the stashed snapshot one primitive at a time, then
        // clear the stash slot (no motion in finalize).
        await runReconcilePlan(steps, { title: 'Pop stash' });

        await finalizeStashPop();

        store.forceGhostSync = true;

        await _deps.fetchLabState();

        _deps.render();

        await refreshControlPanel();

        hidePlanPreview();

        log('Popped stash — changes restored on the bench as uncommitted edits', 'info');

    } catch (e) {

        console.error('[control-panel] pop stash failed', e);

        store.forceGhostSync = true;

        await _deps.fetchLabState().catch(() => {});

        await refreshControlPanel().catch(() => {});

        showErrorModal('Pop Stash Failed', e.message || String(e), { kind: 'dismissible' });

    }

}



async function onDropStash() {

    if (!hasStash()) return;

    showConfirmationModal(

        'Discard the stashed changes? This cannot be undone and does not move the bench.',

        async () => {

            try {

                await dropStash();

                await refreshControlPanel();

                log('Dropped stash', 'info');

            } catch (e) {

                console.error('[control-panel] drop stash failed', e);

                showErrorModal('Drop Stash Failed', e.message || String(e), { kind: 'dismissible' });

            }

        },

        () => {},

        { confirmLabel: 'Drop stash', cancelLabel: 'Keep' },

    );

}



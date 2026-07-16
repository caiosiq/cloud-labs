/**
 * Configuration VC state — applied bench vs soft-checkout preview.
 *
 * - **applied** — last hard-applied (or committed) configuration on the bench
 * - **viewing** — configuration currently projected on the table (soft checkout)
 *
 * View mode is active when preview differs from what is applied on the bench.
 */
import { store } from '../state/store.js';
import {
    acquireSessionLease,
    commandLeaseRequired,
    otherHoldsSessionLease,
    releaseSessionLease,
    weHoldSessionLease,
} from '../api/session-lease.js';
import { log } from '../ui/log.js';

/** The backend working-tree mirror (`store.control.working`), or an empty shape. */
function working() {
    return store.control.working || {};
}

/** @returns {string|null} */
export function getAppliedCommitId() {
    return working().applied?.configuration_id ?? null;
}

/** @returns {string|null} */
export function getAppliedBranch() {
    return working().applied?.branch ?? null;
}

/** @returns {object|null} The current stash summary, or null. */
export function getStash() {
    return working().stash ?? null;
}

/**
 * Mirror the full backend control status into the single `store.control.working`
 * object. This is the one place working-tree state enters the frontend.
 * @param {object|null|undefined} status A `/api/control/{repo}/status` payload.
 */
export function syncControlStatus(status) {
    if (!status || typeof status !== 'object') {
        store.control.working = null;
        return;
    }
    if (status.working && typeof status.working === 'object') {
        store.control.working = status.working;
        return;
    }
    // No runtime-derived working block (e.g. lab offline): synthesize a minimal
    // mirror from the top-level status fields so the applied pointer and stash
    // still read correctly.
    const applied = status.applied || { configuration_id: null, branch: null };
    store.control.working = {
        applied,
        head: status.heads ? status.heads[applied.branch] ?? null : null,
        on_head: true,
        detached: false,
        dirty: false,
        stash: status.stash ?? null,
        capabilities: null,
    };
}

/**
 * Optimistically overwrite just the applied pointer in the mirror (used right
 * after a checkout/commit/create response, before the full status refresh).
 * @param {{ configuration_id?: string|null, branch?: string|null }|null|undefined} applied
 */
export function setAppliedPointer(applied) {
    if (!applied || typeof applied !== 'object') return;
    if (!store.control.working) store.control.working = {};
    store.control.working.applied = {
        configuration_id: applied.configuration_id ?? null,
        branch: applied.branch ?? null,
    };
}

/**
 * Final UI action capabilities — the single backend rule set (`working.capabilities`)
 * combined with the frontend-only preview gate. Every mutating action is suppressed
 * while previewing a node, since a preview is a read-only overlay on the live bench.
 *
 * @returns {{
 *   canCommit: boolean, commitRecommended: boolean,
 *   canStash: boolean, canPopStash: boolean, canDropStash: boolean,
 *   canFork: boolean, canCheckoutOther: boolean,
 *   viewing: boolean, dirty: boolean, detached: boolean, hasStash: boolean,
 * }}
 */
export function controlCapabilities() {
    const caps = working().capabilities || {};
    const viewing = isConfigViewMode();
    return {
        canCommit: Boolean(caps.can_commit) && !viewing,
        commitRecommended: Boolean(caps.commit_recommended) && !viewing,
        canStash: Boolean(caps.can_stash) && !viewing,
        canPopStash: Boolean(caps.can_pop_stash) && !viewing,
        // Dropping a stash never moves the bench, so it is safe while previewing.
        canDropStash: Boolean(caps.can_drop_stash),
        canFork: Boolean(caps.can_fork),
        canCheckoutOther: Boolean(caps.can_checkout_other),
        canSetNode: Boolean(caps.can_set_node),
        viewing,
        dirty: isDirty(),
        detached: isDetached(),
        hasStash: hasStash(),
        unadopted: isUnadopted(),
    };
}

/** Bench has uncommitted edits relative to the applied node (and not previewing). */
export function isDirty() {
    return Boolean(working().dirty) && !isConfigViewMode();
}

/** Applied node is the tip of its branch (editing is allowed here). */
export function isOnHead() {
    return working().on_head !== false && !working().detached;
}

/** Applied node is an older commit (detached) — editing requires forking first. */
export function isDetached() {
    return Boolean(working().detached);
}

/** A stash currently exists for the active repo. */
export function hasStash() {
    return Boolean(working().stash);
}

/**
 * Repo entered but no current node established yet: the bench is not diffed
 * against anything. The user must "Set as node" an existing commit (or commit
 * the current bench in a fresh repo) before stash/fork/commit-child apply.
 */
export function isUnadopted() {
    return Boolean(working().unadopted);
}

/** Preview on table differs from hard-applied bench configuration. */
export function isConfigViewMode() {
    const viewing = store.control.viewingCommitId;
    if (!viewing) return false;
    const applied = getAppliedCommitId();
    if (!applied) return true;
    return viewing !== applied;
}

/** A reconcile plan is executing (Apply on bench / stash / pop). */
export function isReconcileRunning() {
    return Boolean(store.control.reconcileProgress && store.control.reconcileProgress.active);
}

/** Branch has at least one commit (HEAD exists). */
export function hasRepoCommits() {
    return Boolean(store.control.liveHeadId);
}

/** Live working table with no commit history on the active branch (and not previewing). */
export function isUncommittedRuntime() {
    return !hasRepoCommits() && !isConfigViewMode();
}

/** Bench/layout mutations allowed (not previewing and on branch HEAD). */
export function isRuntimeEditable() {
    return !isConfigViewMode() && !isDetached();
}

/** User-facing block reason, or null if edits are allowed. */
export function runtimeEditableOrMessage() {
    if (otherHoldsSessionLease()) {
        const holder = store.labState?.session_lease?.holder || 'another client';
        return `Bench leased by ${holder}. Wait, or ask them to release control.`;
    }
    if (commandLeaseRequired() && !weHoldSessionLease()) {
        return 'Take control of this backend before editing (session lease).';
    }
    if (isConfigViewMode()) {
        return 'You are viewing a configuration preview. Return to bench or apply on bench before editing.';
    }
    if (isDetached()) {
        return 'You are on an older commit (detached). Fork a new branch here before making changes.';
    }
    return null;
}

async function _refreshLabStateSoft() {
    try {
        const { fetchLabState } = await import('../state/lab-state.js');
        await fetchLabState();
    } catch (_) {
        /* poll will catch up */
    }
}

/** Update the Twin UI lease banner from lab-state polling. */
export function refreshSessionLeaseBanner() {
    const el = document.getElementById('session-lease-banner');
    if (!el) return;

    const lease = store.labState?.session_lease;
    const solo = Boolean(store.coordinatorPolicy?.solo);
    const ours = weHoldSessionLease();
    const other = otherHoldsSessionLease();
    const needLease = commandLeaseRequired();

    el.style.display = 'block';
    el.style.lineHeight = '1.45';

    const wire = (html) => {
        el.innerHTML = html;
        el.querySelector('[data-lease-action="take"]')?.addEventListener('click', async () => {
            const r = await acquireSessionLease();
            if (!r.ok) log(r.error || 'Take control failed', 'warn');
            await _refreshLabStateSoft();
            refreshSessionLeaseBanner();
        });
        el.querySelector('[data-lease-action="release"]')?.addEventListener('click', async () => {
            await releaseSessionLease();
            await _refreshLabStateSoft();
            refreshSessionLeaseBanner();
        });
    };

    if (ours) {
        el.style.color = '#86efac';
        el.style.borderColor = 'rgba(34, 197, 94, 0.4)';
        el.style.background = 'rgba(34, 197, 94, 0.12)';
        wire(
            `You have control as <strong>${lease?.holder || 'this client'}</strong>. `
            + `<button type="button" data-lease-action="release" style="margin-left:8px;cursor:pointer;font:inherit;color:inherit;background:transparent;border:1px solid currentColor;border-radius:4px;padding:2px 8px">Release</button> `
            + `<a href="/operations" target="_blank" style="color:inherit;margin-left:6px">Operations</a>`,
        );
        return;
    }

    if (other) {
        el.style.color = '#93c5fd';
        el.style.borderColor = 'rgba(59, 130, 246, 0.35)';
        el.style.background = 'rgba(59, 130, 246, 0.12)';
        wire(
            `Bench leased by <strong>${lease.holder}</strong> — direct control disabled. `
            + `<a href="/operations" target="_blank" style="color:inherit">Operations</a>`,
        );
        return;
    }

    if (needLease) {
        el.style.color = '#fde68a';
        el.style.borderColor = 'rgba(234, 179, 8, 0.4)';
        el.style.background = 'rgba(234, 179, 8, 0.1)';
        wire(
            `No session lease — `
            + `<button type="button" data-lease-action="take" style="cursor:pointer;font:inherit;color:inherit;background:transparent;border:1px solid currentColor;border-radius:4px;padding:2px 8px">Take control</button> `
            + `to edit this backend.`,
        );
        return;
    }

    el.style.color = '#cbd5e1';
    el.style.borderColor = 'rgba(148, 163, 184, 0.35)';
    el.style.background = 'rgba(148, 163, 184, 0.1)';
    wire(
        (solo ? 'Solo mode — ' : 'Mock soft lease — ')
        + `edits allowed without a lease. `
        + `<button type="button" data-lease-action="take" style="cursor:pointer;font:inherit;color:inherit;background:transparent;border:1px solid currentColor;border-radius:4px;padding:2px 8px">Take control</button> `
        + `to lock others out. `
        + `<a href="/operations" target="_blank" style="color:inherit">Operations</a>`,
    );
}

/** Clear stale preview pointers when on an uncommitted working table. */
export function normalizeControlViewState() {
    normalizeViewingCommit();
    if (!hasRepoCommits() && !getAppliedCommitId()) {
        store.control.viewingCommitId = null;
    }
}

/** Clear read-only preview overlay (configuration + metadata). */
export function clearPreviewOverlay() {
    store.control.previewConfig = null;
    store.control.previewMetadata = null;
}

/**
 * Set read-only preview overlay from a soft checkout response.
 * @param {object|null} configuration
 * @param {object|null|undefined} metadata
 */
export function setPreviewOverlay(configuration, metadata) {
    store.control.previewConfig = configuration || null;
    store.control.previewMetadata = metadata || null;
}

/**
 * Optimization metadata for a tag from the viewed commit (if any).
 * @param {string} tagId
 * @returns {object|null}
 */
export function getPreviewOptimizationEntry(tagId) {
    const meta = store.control.previewMetadata;
    if (!tagId || !meta || typeof meta !== 'object') return null;
    const opt = meta.optimization;
    if (!opt || typeof opt !== 'object') return null;
    return opt[tagId] || null;
}

/**
 * After soft checkout, set viewing id unless it matches applied (live bench).
 * @param {string|null} commitId
 */
export function setViewingCommit(commitId) {
    if (!commitId || commitId === getAppliedCommitId()) {
        store.control.viewingCommitId = null;
        return;
    }
    store.control.viewingCommitId = commitId;
}

/** Clear preview pointer when it matches applied. */
export function normalizeViewingCommit() {
    if (
        store.control.viewingCommitId &&
        store.control.viewingCommitId === getAppliedCommitId()
    ) {
        store.control.viewingCommitId = null;
    }
}

/**
 * ControlManager API — configuration commits, branch graph, soft checkout.
 */
import { store } from '../state/store.js';

const DEFAULT_REPO = 'default';

function repoId() {
    return store.control?.repoId || DEFAULT_REPO;
}

async function parseJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data.detail;
        let msg;
        if (typeof detail === 'string') {
            msg = detail;
        } else if (Array.isArray(detail)) {
            msg = detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
        } else if (detail && typeof detail === 'object') {
            // Structured backend errors (e.g. 409 checkout) carry { message, ... }.
            msg = detail.message || JSON.stringify(detail);
        } else {
            msg = res.statusText || 'Request failed';
        }
        throw new Error(msg);
    }
    return data;
}

export async function fetchControlRepos() {
    const res = await fetch('/api/control/repos');
    return parseJson(res);
}

export async function createControlRepo({ repoId, displayName } = {}) {
    const res = await fetch('/api/control/repos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            repo_id: repoId,
            display_name: displayName ?? null,
        }),
    });
    return parseJson(res);
}

export async function fetchControlStatus() {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/status`);
    return parseJson(res);
}

export async function fetchControlHistory(branch = undefined) {
    let url = `/api/control/${encodeURIComponent(repoId())}/history`;
    if (branch === undefined) {
        const b = store.control?.branch ?? 'main';
        url += `?branch=${encodeURIComponent(b)}`;
    } else if (branch !== null) {
        url += `?branch=${encodeURIComponent(branch)}`;
    }
    const res = await fetch(url);
    return parseJson(res);
}

/** All commit nodes across branches (for graph layout). */
export async function fetchAllControlNodes() {
    return fetchControlHistory(null);
}

export async function commitConfiguration({ message, branch, parentId } = {}) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/configurations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: message || '',
            branch: branch || store.control?.branch || 'main',
            parent_id: parentId ?? null,
        }),
    });
    return parseJson(res);
}

export async function forkControlBranch({ branch, parentId }) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/branches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch, parent_id: parentId }),
    });
    return parseJson(res);
}

export async function softCheckoutConfiguration(configurationId) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            configuration_id: configurationId,
            mode: 'soft',
        }),
    });
    return parseJson(res);
}

export async function previewHardCheckout(configurationId) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            configuration_id: configurationId,
            mode: 'hard',
            preview: true,
        }),
    });
    return parseJson(res);
}

/** "Set as node": adopt a commit as the current node without moving the bench. */
export async function adoptConfiguration(configurationId) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            configuration_id: configurationId,
            mode: 'adopt',
        }),
    });
    return parseJson(res);
}

export async function hardCheckoutConfiguration(configurationId) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            configuration_id: configurationId,
            mode: 'hard',
            preview: false,
        }),
    });
    return parseJson(res);
}

/**
 * Record the outcome of a step-by-step "Apply on bench" after the frontend has
 * already driven every reconcile primitive through /api/command. No motion runs
 * server-side — this only moves the applied pointer + projection + bench claim.
 */
export async function finalizeHardCheckout(configurationId) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            configuration_id: configurationId,
            mode: 'hard',
            finalize: true,
        }),
    });
    return parseJson(res);
}

export async function stashChanges(message, { preview = false } = {}) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/stash`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message ?? null, preview }),
    });
    return parseJson(res);
}

export async function popStash({ preview = false } = {}) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/stash/pop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preview }),
    });
    return parseJson(res);
}

/**
 * Record a step-by-step stash after the frontend drove the primitives back to
 * the applied node. ``snapshot`` is the dirty-bench configuration captured at
 * preview time (before the primitives ran), so the saved stash reflects the
 * changes that were set aside — not the now-clean bench.
 */
export async function finalizeStash(message, snapshot, metadata) {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/stash`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: message ?? null,
            finalize: true,
            snapshot,
            metadata: metadata ?? null,
        }),
    });
    return parseJson(res);
}

/** Record a step-by-step stash pop after the frontend restored the snapshot. */
export async function finalizeStashPop() {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/stash/pop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ finalize: true }),
    });
    return parseJson(res);
}

export async function dropStash() {
    const res = await fetch(`/api/control/${encodeURIComponent(repoId())}/stash`, {
        method: 'DELETE',
    });
    return parseJson(res);
}

export async function fetchCheckoutCompatibilityReport(configurationId) {
    const q = new URLSearchParams({ configuration_id: configurationId });
    const res = await fetch(
        `/api/control/${encodeURIComponent(repoId())}/checkout-report?${q.toString()}`,
    );
    return parseJson(res);
}

export async function fetchConfigurationDiff(fromId, toId) {
    const q = new URLSearchParams({ from: fromId, to: toId });
    const res = await fetch(
        `/api/control/${encodeURIComponent(repoId())}/diff?${q.toString()}`,
    );
    return parseJson(res);
}

export function shortCommitId(id) {
    if (!id || typeof id !== 'string') return '—';
    return id.length <= 10 ? id : `${id.slice(0, 8)}…`;
}

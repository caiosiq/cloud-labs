/**
 * Embeddable Snapshots browser for Wiki → Backends → Snapshots tab.
 * Mount into a container; scoped to the currently selected backend.
 */
import {
    approvePublishRequest,
    fetchCatalogPins,
    fetchPublishRequests,
    rejectPublishRequest,
} from '../api/catalog.js';
import {
    fetchControlRepos,
    fetchControlHistoryFor,
    fetchControlStatusFor,
    shortCommitId,
} from '../api/control.js';
import { layoutCommitGraph, renderControlGraph } from '../ui/control-graph.js';

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * @param {HTMLElement} root
 * @param {{ backendId: string }} opts
 * @returns {{ destroy: () => void, refresh: () => Promise<void> }}
 */
export function mountSnapshotsPanel(root, opts) {
    const backendId = String(opts.backendId || '').trim();
    let destroyed = false;
    /** @type {object[]} */
    let allPins = [];
    /** @type {Map<string, object>} */
    let repoMetaById = new Map();
    let selectedRepoId = null;
    let selectedBranch = null;
    let selectedCommitId = null;
    /** @type {object|null} */
    let selectedPin = null;
    /** @type {object[]} */
    let graphNodes = [];
    /** @type {Record<string, string|null>} */
    let graphHeads = {};
    const isMock = backendId.startsWith('mock.');

    root.innerHTML = `
        <div class="snap-panel">
            <p class="snap-lede">Frozen configurations for <code>${escapeHtml(backendId)}</code>. Publish from Twin; load via SDK <code>load_snapshot(catalog_pin=…)</code>.</p>
            <details class="snap-queue" ${isMock ? '' : 'open'}>
                <summary>Owner queue</summary>
                <p class="snap-hint" data-role="hint">${isMock ? 'Mock: publishes auto-approve.' : 'Approve or reject pending publishes.'}</p>
                <div data-role="requests-empty" class="empty">No pending publishes.</div>
                <table data-role="requests-table" hidden>
                    <thead><tr><th>Request</th><th>Commit</th><th></th></tr></thead>
                    <tbody data-role="requests-body"></tbody>
                </table>
            </details>
            <h3 class="snap-heading" data-role="heading">Choose a repository</h3>
            <div class="breadcrumb" data-role="breadcrumb"></div>
            <div data-role="browse"></div>
            <div data-role="graph-section" hidden>
                <div class="graph-panel">
                    <div class="graph-panel-head">
                        <span data-role="graph-label">Commit graph</span>
                        <span class="empty" data-role="graph-hint" style="font-size:11px"></span>
                    </div>
                    <div class="control-graph-wrap" data-role="graph-wrap">
                        <div class="control-graph-empty">Select a branch.</div>
                    </div>
                </div>
                <div class="pin-detail empty" data-role="pin-detail">Select a pin node to copy a load snippet.</div>
            </div>
        </div>`;

    const el = {
        heading: root.querySelector('[data-role="heading"]'),
        breadcrumb: root.querySelector('[data-role="breadcrumb"]'),
        browse: root.querySelector('[data-role="browse"]'),
        graphSection: root.querySelector('[data-role="graph-section"]'),
        graphWrap: root.querySelector('[data-role="graph-wrap"]'),
        graphLabel: root.querySelector('[data-role="graph-label"]'),
        graphHint: root.querySelector('[data-role="graph-hint"]'),
        pinDetail: root.querySelector('[data-role="pin-detail"]'),
        requestsEmpty: root.querySelector('[data-role="requests-empty"]'),
        requestsTable: root.querySelector('[data-role="requests-table"]'),
        requestsBody: root.querySelector('[data-role="requests-body"]'),
        queue: root.querySelector('.snap-queue'),
    };

    function shortId(value) {
        const s = String(value || '');
        return s.length > 10 ? `${s.slice(0, 8)}…` : s;
    }

    function pinsForRepo(repoId) {
        return allPins.filter((p) => p.repo_id === repoId);
    }

    function pinsForBranch(repoId, branch) {
        return pinsForRepo(repoId).filter((p) => (p.branch || 'main') === branch);
    }

    function pinByCommit(repoId, commitId) {
        return (
            allPins.find(
                (p) =>
                    p.repo_id === repoId &&
                    String(p.configuration_id) === String(commitId),
            ) || null
        );
    }

    function repoDisplayName(repoId) {
        const meta = repoMetaById.get(repoId);
        return (meta && (meta.display_name || meta.repo_id)) || repoId;
    }

    function scriptSnippetForPin(pin) {
        const id = String(pin.pin_id || '');
        return (
            `# Frozen snapshot pin\n` +
            `snap = lab.load_snapshot(catalog_pin=${JSON.stringify(id)})\n` +
            `lab.reconcile_hardware()`
        );
    }

    function setHeading(text) {
        if (el.heading) el.heading.textContent = text;
    }

    function renderBreadcrumb() {
        const parts = [];
        parts.push(`<button type="button" class="linkish" data-nav="repos">Repos</button>`);
        if (selectedRepoId) {
            parts.push(`<span class="sep">/</span>`);
            const label = escapeHtml(repoDisplayName(selectedRepoId));
            if (selectedBranch) {
                parts.push(`<button type="button" class="linkish" data-nav="repo">${label}</button>`);
                parts.push(`<span class="sep">/</span>`);
                if (selectedCommitId) {
                    parts.push(
                        `<button type="button" class="linkish" data-nav="branch">${escapeHtml(selectedBranch)}</button>`,
                    );
                    parts.push(`<span class="sep">/</span>`);
                    const tip = selectedPin
                        ? escapeHtml(selectedPin.display_name || selectedPin.pin_id)
                        : escapeHtml(shortCommitId(selectedCommitId));
                    parts.push(`<span class="current">${tip}</span>`);
                } else {
                    parts.push(`<span class="current">${escapeHtml(selectedBranch)}</span>`);
                }
            } else {
                parts.push(`<span class="current">${label}</span>`);
            }
        }
        el.breadcrumb.innerHTML = parts.join('');
        el.breadcrumb.querySelectorAll('[data-nav]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const nav = btn.getAttribute('data-nav');
                if (nav === 'repos') {
                    selectedRepoId = null;
                    selectedBranch = null;
                    selectedCommitId = null;
                    selectedPin = null;
                    renderBrowse();
                } else if (nav === 'repo') {
                    selectedBranch = null;
                    selectedCommitId = null;
                    selectedPin = null;
                    renderBrowse();
                } else if (nav === 'branch') {
                    selectedCommitId = null;
                    selectedPin = null;
                    renderBrowse();
                }
            });
        });
    }

    function renderRepoList() {
        setHeading('Choose a repository');
        const repoIds = [...new Set(allPins.map((p) => p.repo_id).filter(Boolean))].sort();
        if (!repoIds.length) {
            el.browse.innerHTML =
                '<p class="empty">No approved snapshots yet. Publish a Twin commit to create the first pin.</p>';
            el.graphSection.hidden = true;
            return;
        }
        el.browse.innerHTML = `<div class="pick-grid">${repoIds
            .map((id) => {
                const pins = pinsForRepo(id);
                return `<button type="button" class="pick-card" data-repo="${escapeHtml(id)}">
                    <h3>${escapeHtml(repoDisplayName(id))}</h3>
                    <div class="meta">${escapeHtml(id)}</div>
                    <div class="badge-row"><span class="pin-badge">${pins.length} pin${pins.length === 1 ? '' : 's'}</span></div>
                </button>`;
            })
            .join('')}</div>`;
        el.browse.querySelectorAll('[data-repo]').forEach((btn) => {
            btn.addEventListener('click', () => {
                selectedRepoId = btn.getAttribute('data-repo');
                selectedBranch = null;
                selectedCommitId = null;
                selectedPin = null;
                renderBrowse();
            });
        });
        el.graphSection.hidden = true;
    }

    async function renderBranchList() {
        setHeading('Choose a branch');
        const pins = pinsForRepo(selectedRepoId);
        const branchSet = new Set(pins.map((p) => p.branch || 'main'));
        try {
            const status = await fetchControlStatusFor(selectedRepoId);
            Object.keys(status.heads || {}).forEach((b) => {
                if (status.heads[b]) branchSet.add(b);
            });
        } catch (_) {
            /* pins alone */
        }
        const branches = [...branchSet].sort((a, b) => {
            if (a === 'main') return -1;
            if (b === 'main') return 1;
            return a.localeCompare(b);
        });
        el.browse.innerHTML = `<div class="pick-grid">${branches
            .map((branch) => {
                const bpins = pinsForBranch(selectedRepoId, branch);
                return `<button type="button" class="pick-card" data-branch="${escapeHtml(branch)}">
                    <h3>${escapeHtml(branch)}</h3>
                    <div class="meta">${escapeHtml(selectedRepoId)} @ ${escapeHtml(branch)}</div>
                    <div class="badge-row">${
                        bpins.length
                            ? `<span class="pin-badge">${bpins.length} pin${bpins.length === 1 ? '' : 's'}</span>`
                            : `<span class="meta">browse only</span>`
                    }</div>
                </button>`;
            })
            .join('')}</div>`;
        el.browse.querySelectorAll('[data-branch]').forEach((btn) => {
            btn.addEventListener('click', () => {
                selectedBranch = btn.getAttribute('data-branch');
                selectedCommitId = null;
                selectedPin = null;
                renderBrowse();
            });
        });
        el.graphSection.hidden = true;
    }

    function renderPinDetail(pin, commitId = null) {
        if (!pin && !commitId) {
            el.pinDetail.className = 'pin-detail empty';
            el.pinDetail.textContent = 'Select a pin node to copy a load snippet.';
            return;
        }
        if (pin) {
            const script = scriptSnippetForPin(pin);
            const json = JSON.stringify(
                {
                    source: 'catalog',
                    pin_id: pin.pin_id,
                    repo_id: pin.repo_id,
                    branch: pin.branch || 'main',
                    commit: pin.configuration_id,
                },
                null,
                2,
            );
            el.pinDetail.className = 'pin-detail';
            el.pinDetail.innerHTML = `
                <div><span class="pin-badge">Snapshot pin</span> <strong>${escapeHtml(pin.display_name || pin.pin_id)}</strong></div>
                <div class="meta" style="margin-top:6px"><code>${escapeHtml(pin.pin_id)}</code></div>
                <div class="pin-detail-block"><h4>Script</h4><pre>${escapeHtml(script)}</pre></div>
                <div class="pin-detail-block"><h4>JSON</h4><pre>${escapeHtml(json)}</pre></div>
                <div class="pin-actions">
                    <button type="button" data-copy-script>Copy script</button>
                    <button type="button" data-copy-json>Copy JSON</button>
                </div>`;
            el.pinDetail.querySelector('[data-copy-script]')?.addEventListener('click', () => {
                void navigator.clipboard.writeText(script);
            });
            el.pinDetail.querySelector('[data-copy-json]')?.addEventListener('click', () => {
                void navigator.clipboard.writeText(json);
            });
            return;
        }
        el.pinDetail.className = 'pin-detail';
        el.pinDetail.innerHTML = `
            <div>Commit <code>${escapeHtml(shortId(commitId))}</code>
            <span class="meta"> — not an approved pin</span></div>
            <p class="empty" style="margin:8px 0 0">Publish from Twin to freeze this node.</p>`;
    }

    function paintGraph(selectedId) {
        if (!graphNodes.length) return;
        const headId = graphHeads[selectedBranch] || null;
        const layout = layoutCommitGraph(graphNodes, {
            heads: graphHeads,
            activeBranch: selectedBranch,
            headId,
        });
        const pinIds = new Set(
            pinsForRepo(selectedRepoId).map((p) => String(p.configuration_id)),
        );
        renderControlGraph(el.graphWrap, layout, {
            headId,
            appliedId: null,
            selectedId: selectedId || null,
            viewingId: selectedId || null,
            uncommitted: false,
            onNodeClick: (id) => {
                selectedCommitId = id;
                selectedPin = pinByCommit(selectedRepoId, id);
                paintGraph(id);
                renderBreadcrumb();
                renderPinDetail(selectedPin, id);
            },
        });
        if (el.graphHint) {
            const pinCount = graphNodes.filter((n) => pinIds.has(String(n.id))).length;
            el.graphHint.textContent = `${pinCount} snapshot pin(s) — click a node`;
        }
    }

    async function renderGraphLevel() {
        setHeading('Pick a pin on the graph');
        el.browse.innerHTML = '';
        el.graphSection.hidden = false;
        if (el.graphLabel) {
            el.graphLabel.textContent = `${repoDisplayName(selectedRepoId)} @ ${selectedBranch}`;
        }
        el.graphWrap.innerHTML = `<div class="control-graph-empty">Loading graph…</div>`;
        renderPinDetail(null);
        try {
            const [allHistory, branchHistory, status] = await Promise.all([
                fetchControlHistoryFor(selectedRepoId, null),
                fetchControlHistoryFor(selectedRepoId, selectedBranch),
                fetchControlStatusFor(selectedRepoId),
            ]);
            graphHeads = status.heads || {};
            graphNodes =
                Array.isArray(allHistory.nodes) && allHistory.nodes.length
                    ? allHistory.nodes
                    : Array.isArray(branchHistory.nodes)
                      ? branchHistory.nodes
                      : [];
            if (!graphNodes.length) {
                el.graphWrap.innerHTML =
                    '<div class="control-graph-empty">No commits on this branch yet.</div>';
                return;
            }
            paintGraph(selectedCommitId);
        } catch (err) {
            el.graphWrap.innerHTML = `<div class="control-graph-empty">Graph failed: ${escapeHtml(err.message || err)}</div>`;
        }
    }

    function renderBrowse() {
        if (destroyed) return;
        renderBreadcrumb();
        if (!selectedRepoId) {
            renderRepoList();
            return;
        }
        if (!selectedBranch) {
            void renderBranchList();
            return;
        }
        void renderGraphLevel();
    }

    function renderRequests(requests) {
        const pending = (requests || []).filter((r) => r.status === 'pending');
        if (!pending.length) {
            el.requestsTable.hidden = true;
            el.requestsEmpty.hidden = false;
            if (el.queue && isMock) el.queue.open = false;
            return;
        }
        el.requestsEmpty.hidden = true;
        el.requestsTable.hidden = false;
        if (el.queue) el.queue.open = true;
        el.requestsBody.innerHTML = pending
            .map(
                (req) => `
            <tr>
                <td><code>${escapeHtml(req.request_id)}</code><br>
                    <span class="meta">${escapeHtml(req.message || '—')}</span></td>
                <td>${escapeHtml(req.repo_id)}@${escapeHtml(req.branch || 'main')}<br>
                    <code>${escapeHtml(shortId(req.configuration_id))}</code></td>
                <td>${
                    isMock
                        ? ''
                        : `<button type="button" data-approve="${escapeHtml(req.request_id)}">Approve</button>
                           <button type="button" data-reject="${escapeHtml(req.request_id)}">Reject</button>`
                }</td>
            </tr>`,
            )
            .join('');
        el.requestsBody.querySelectorAll('[data-approve]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                await approvePublishRequest(btn.dataset.approve, {});
                await refresh();
            });
        });
        el.requestsBody.querySelectorAll('[data-reject]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                await rejectPublishRequest(btn.dataset.reject, { reason: '' });
                await refresh();
            });
        });
    }

    async function refresh() {
        if (destroyed) return;
        try {
            const [catalog, requestsPayload, reposPayload] = await Promise.all([
                fetchCatalogPins(),
                fetchPublishRequests(),
                fetchControlRepos().catch(() => ({ repos: [] })),
            ]);
            allPins = catalog.pins || [];
            repoMetaById = new Map((reposPayload.repos || []).map((r) => [r.repo_id, r]));
            renderRequests(requestsPayload.requests || []);
            renderBrowse();
        } catch (err) {
            el.browse.innerHTML = `<p class="empty">Failed to load snapshots: ${escapeHtml(err.message)}</p>`;
        }
    }

    void refresh();

    return {
        destroy() {
            destroyed = true;
            root.innerHTML = '';
        },
        refresh,
    };
}

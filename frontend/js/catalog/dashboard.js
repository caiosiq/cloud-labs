/**
 * Catalog — hierarchical browse of approved pins:
 * repos → branches → Twin-like commit graph → read-only twin + copy.
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
import { clearCatalogPreview, previewConfiguration } from './twin-preview.js';

const backendsPanel = document.getElementById('backends-panel');
const requestsBody = document.getElementById('requests-body');
const requestsTable = document.getElementById('requests-table');
const requestsEmpty = document.getElementById('requests-empty');
const publishHint = document.getElementById('publish-hint');
const breadcrumbEl = document.getElementById('catalog-breadcrumb');
const browseEl = document.getElementById('catalog-browse');
const graphSection = document.getElementById('catalog-graph-section');
const graphWrap = document.getElementById('catalog-graph-wrap');
const graphLabel = document.getElementById('catalog-graph-label');
const graphHint = document.getElementById('catalog-graph-hint');
const pinSnapshot = document.getElementById('pin-snapshot');

/** @type {object[]} */
let allPins = [];
/** @type {Map<string, object>} */
let repoMetaById = new Map();
/** @type {string|null} */
let selectedRepoId = null;
/** @type {string|null} */
let selectedBranch = null;
/** @type {string|null} */
let selectedCommitId = null;
/** @type {object|null} */
let selectedPin = null;
/** @type {object[]} */
let graphNodes = [];
/** @type {Record<string, string|null>} */
let graphHeads = {};
let isMockBackend = true;

async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} → ${res.status}`);
    return res.json();
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

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

function snapshotJsonForPin(pin) {
    return {
        source: 'catalog',
        pin_id: pin.pin_id,
        repo_id: pin.repo_id,
        branch: pin.branch || 'main',
        commit: pin.configuration_id,
    };
}

function snapshotJsonLocal(repoId, branch, commitId) {
    return {
        source: 'local',
        repo_id: repoId,
        branch: branch || 'main',
        commit: commitId,
    };
}

function scriptSnippetForPin(pin) {
    const id = String(pin.pin_id || '');
    return (
        `# Frozen catalog pin (does not follow local branch heads)\n` +
        `snap = lab.load_snapshot(catalog_pin=${JSON.stringify(id)})\n` +
        `assert snap.source == "catalog"\n` +
        `lab.reconcile_hardware()`
    );
}

function scriptSnippetLocal(repoId, branch) {
    return (
        `# Local branch head at load time (not a catalog pin)\n` +
        `snap = lab.load_snapshot(${JSON.stringify(repoId)}, ${JSON.stringify(branch || 'main')})\n` +
        `assert snap.source == "local"\n` +
        `lab.reconcile_hardware()`
    );
}

function repoDisplayName(repoId) {
    const meta = repoMetaById.get(repoId);
    return (meta && (meta.display_name || meta.repo_id)) || repoId;
}

function renderBreadcrumb() {
    const parts = [];
    parts.push(
        `<button type="button" class="linkish" data-nav="repos">Repos</button>`,
    );
    if (selectedRepoId) {
        parts.push(`<span class="sep">/</span>`);
        const label = escapeHtml(repoDisplayName(selectedRepoId));
        if (selectedBranch) {
            parts.push(
                `<button type="button" class="linkish" data-nav="repo">${label}</button>`,
            );
            parts.push(`<span class="sep">/</span>`);
            if (selectedCommitId) {
                parts.push(
                    `<button type="button" class="linkish" data-nav="branch">${escapeHtml(selectedBranch)}</button>`,
                );
                parts.push(`<span class="sep">/</span>`);
                const pin = selectedPin;
                const tip = pin
                    ? escapeHtml(pin.display_name || pin.pin_id)
                    : escapeHtml(shortCommitId(selectedCommitId));
                parts.push(`<span class="current">${tip}</span>`);
            } else {
                parts.push(
                    `<span class="current">${escapeHtml(selectedBranch)}</span>`,
                );
            }
        } else {
            parts.push(`<span class="current">${label}</span>`);
        }
    }
    breadcrumbEl.innerHTML = parts.join('');
    breadcrumbEl.querySelectorAll('[data-nav]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const nav = btn.getAttribute('data-nav');
            if (nav === 'repos') {
                selectedRepoId = null;
                selectedBranch = null;
                selectedCommitId = null;
                selectedPin = null;
                clearCatalogPreview();
                renderBrowse();
            } else if (nav === 'repo') {
                selectedBranch = null;
                selectedCommitId = null;
                selectedPin = null;
                clearCatalogPreview();
                renderBrowse();
            } else if (nav === 'branch') {
                selectedCommitId = null;
                selectedPin = null;
                clearCatalogPreview();
                renderBrowse();
            }
        });
    });
}

function renderRepoList() {
    const repoIds = [...new Set(allPins.map((p) => p.repo_id).filter(Boolean))].sort();
    if (!repoIds.length) {
        browseEl.innerHTML =
            '<p class="empty">No approved pins yet. Publish from Twin UI to seed the catalog.</p>';
        graphSection.hidden = true;
        return;
    }
    browseEl.innerHTML = `
        <p class="empty" style="margin-bottom:10px">Select a repo that has approved pins.</p>
        <div class="pick-grid">
            ${repoIds
                .map((id) => {
                    const pins = pinsForRepo(id);
                    const branches = new Set(pins.map((p) => p.branch || 'main'));
                    return `
                        <button type="button" class="pick-card" data-repo="${escapeHtml(id)}">
                            <h3>${escapeHtml(repoDisplayName(id))}</h3>
                            <div class="meta">${escapeHtml(id)}</div>
                            <div class="badge-row">
                                <span class="pin-badge">${pins.length} pin${pins.length === 1 ? '' : 's'}</span>
                                <span class="meta">${branches.size} branch${branches.size === 1 ? '' : 'es'}</span>
                            </div>
                        </button>`;
                })
                .join('')}
        </div>`;
    browseEl.querySelectorAll('[data-repo]').forEach((btn) => {
        btn.addEventListener('click', () => {
            selectedRepoId = btn.getAttribute('data-repo');
            selectedBranch = null;
            selectedCommitId = null;
            selectedPin = null;
            clearCatalogPreview();
            renderBrowse();
        });
    });
    graphSection.hidden = true;
}

async function renderBranchList() {
    const pins = pinsForRepo(selectedRepoId);
    const branchSet = new Set(pins.map((p) => p.branch || 'main'));
    try {
        const status = await fetchControlStatusFor(selectedRepoId);
        Object.keys(status.heads || {}).forEach((b) => {
            if (status.heads[b]) branchSet.add(b);
        });
    } catch (_) {
        /* pins alone are enough */
    }
    const branches = [...branchSet].sort((a, b) => {
        if (a === 'main') return -1;
        if (b === 'main') return 1;
        return a.localeCompare(b);
    });

    browseEl.innerHTML = `
        <p class="empty" style="margin-bottom:10px">
            Branches with pins (and other heads on this backend) for
            <code>${escapeHtml(repoDisplayName(selectedRepoId))}</code>.
        </p>
        <div class="pick-grid">
            ${branches
                .map((branch) => {
                    const bpins = pinsForBranch(selectedRepoId, branch);
                    const pinned = bpins.length > 0;
                    return `
                        <button type="button" class="pick-card" data-branch="${escapeHtml(branch)}">
                            <h3>${escapeHtml(branch)}</h3>
                            <div class="meta">${escapeHtml(selectedRepoId)} @ ${escapeHtml(branch)}</div>
                            <div class="badge-row">
                                ${
                                    pinned
                                        ? `<span class="pin-badge">${bpins.length} pin${bpins.length === 1 ? '' : 's'}</span>`
                                        : `<span class="meta">no pins (browse only)</span>`
                                }
                            </div>
                        </button>`;
                })
                .join('')}
        </div>`;
    browseEl.querySelectorAll('[data-branch]').forEach((btn) => {
        btn.addEventListener('click', () => {
            selectedBranch = btn.getAttribute('data-branch');
            selectedCommitId = null;
            selectedPin = null;
            clearCatalogPreview();
            renderBrowse();
        });
    });
    graphSection.hidden = true;
}

function updateGraphHint(nodes) {
    if (!graphHint) return;
    const pinIds = new Set(
        pinsForRepo(selectedRepoId).map((p) => String(p.configuration_id)),
    );
    const pinCount = (nodes || []).filter((n) => pinIds.has(String(n.id))).length;
    graphHint.textContent =
        `${pinCount} catalog pin node(s) on this graph — click a node to preview the bench.`;
}

function paintGraph(selectedId) {
    if (!graphNodes.length) return;
    const headId = graphHeads[selectedBranch] || null;
    const layout = layoutCommitGraph(graphNodes, {
        heads: graphHeads,
        activeBranch: selectedBranch,
        headId,
    });
    renderControlGraph(graphWrap, layout, {
        headId,
        appliedId: null,
        selectedId: selectedId || null,
        viewingId: selectedId || null,
        uncommitted: false,
        onNodeClick: (id) => void onNodeClick(id),
    });
    updateGraphHint(graphNodes);
}

async function renderGraphLevel() {
    browseEl.innerHTML = '';
    graphSection.hidden = false;
    if (graphLabel) {
        graphLabel.textContent = `Node path · ${repoDisplayName(selectedRepoId)} @ ${selectedBranch}`;
    }
    graphWrap.innerHTML = `<div class="control-graph-empty">Loading graph…</div>`;
    renderPinDetail(null);

    try {
        const [allHistory, branchHistory, status] = await Promise.all([
            fetchControlHistoryFor(selectedRepoId, null),
            fetchControlHistoryFor(selectedRepoId, selectedBranch),
            fetchControlStatusFor(selectedRepoId),
        ]);
        graphHeads = status.heads || {};
        const nodes =
            Array.isArray(allHistory.nodes) && allHistory.nodes.length
                ? allHistory.nodes
                : Array.isArray(branchHistory.nodes)
                  ? branchHistory.nodes
                  : [];
        graphNodes = nodes;
        if (!nodes.length) {
            graphWrap.innerHTML =
                '<div class="control-graph-empty">No commits on this branch yet.</div>';
            if (graphHint) graphHint.textContent = 'No commits to show.';
            return;
        }

        paintGraph(selectedCommitId);
    } catch (err) {
        graphWrap.innerHTML = `<div class="control-graph-empty">Graph failed: ${escapeHtml(err.message || err)}</div>`;
    }
}

async function onNodeClick(commitId) {
    selectedCommitId = commitId;
    selectedPin = pinByCommit(selectedRepoId, commitId);
    renderBreadcrumb();
    renderPinDetail(selectedPin, commitId);
    paintGraph(selectedCommitId);

    try {
        await previewConfiguration(selectedRepoId, commitId);
    } catch (_) {
        /* error shown in twin panel */
    }
}

function renderPinDetail(pin, commitId = null) {
    if (!pin && !commitId) {
        pinSnapshot.className = 'pin-detail empty';
        pinSnapshot.textContent =
            'Select a catalog pin node to copy script or JSON. Non-pin nodes still preview on the twin.';
        return;
    }

    if (pin) {
        const json = JSON.stringify(snapshotJsonForPin(pin), null, 2);
        const script = scriptSnippetForPin(pin);
        pinSnapshot.className = 'pin-detail';
        pinSnapshot.innerHTML = `
            <div>
                <span class="pin-badge">Catalog pin</span>
                <strong>${escapeHtml(pin.display_name || pin.pin_id)}</strong>
            </div>
            <div class="meta" style="margin-top:6px;color:var(--muted);font-size:11px">
                <code>${escapeHtml(pin.pin_id)}</code> ·
                ${escapeHtml(pin.repo_id)}@${escapeHtml(pin.branch || 'main')} ·
                <code>${escapeHtml(shortId(pin.configuration_id))}</code>
            </div>
            <div class="pin-detail-block">
                <h4>Script</h4>
                <pre>${escapeHtml(script)}</pre>
            </div>
            <div class="pin-detail-block">
                <h4>Job snapshot JSON</h4>
                <pre>${escapeHtml(json)}</pre>
            </div>
            <div class="pin-actions">
                <button type="button" data-copy-script>Copy script</button>
                <button type="button" data-copy-json>Copy JSON</button>
            </div>`;
        pinSnapshot.querySelector('[data-copy-script]')?.addEventListener('click', async (ev) => {
            const btn = ev.currentTarget;
            try {
                await navigator.clipboard.writeText(script);
                btn.textContent = 'Copied';
                setTimeout(() => {
                    btn.textContent = 'Copy script';
                }, 1200);
            } catch {
                /* ignore */
            }
        });
        pinSnapshot.querySelector('[data-copy-json]')?.addEventListener('click', async (ev) => {
            const btn = ev.currentTarget;
            try {
                await navigator.clipboard.writeText(json);
                btn.textContent = 'Copied';
                setTimeout(() => {
                    btn.textContent = 'Copy JSON';
                }, 1200);
            } catch {
                /* ignore */
            }
        });
        return;
    }

    // Non-pin node: still useful for local snapshot copy
    const json = JSON.stringify(
        snapshotJsonLocal(selectedRepoId, selectedBranch, commitId),
        null,
        2,
    );
    const script = scriptSnippetLocal(selectedRepoId, selectedBranch);
    pinSnapshot.className = 'pin-detail';
    pinSnapshot.innerHTML = `
        <div>
            <strong>Local node</strong>
            <span style="color:var(--muted);font-size:11px;margin-left:6px">not an approved catalog pin</span>
        </div>
        <div class="meta" style="margin-top:6px;color:var(--muted);font-size:11px">
            ${escapeHtml(selectedRepoId)}@${escapeHtml(selectedBranch)} ·
            <code>${escapeHtml(shortId(commitId))}</code>
        </div>
        <p style="margin:10px 0 0;color:#fcd34d;font-size:11px;font-family:Inter,sans-serif;line-height:1.4">
            Publish this commit from Twin UI to create a frozen catalog pin, or use a local load:
        </p>
        <div class="pin-detail-block">
            <h4>Local script</h4>
            <pre>${escapeHtml(script)}</pre>
        </div>
        <div class="pin-detail-block">
            <h4>Local snapshot JSON</h4>
            <pre>${escapeHtml(json)}</pre>
        </div>
        <div class="pin-actions">
            <button type="button" data-copy-script>Copy script</button>
            <button type="button" data-copy-json>Copy JSON</button>
        </div>`;
    pinSnapshot.querySelector('[data-copy-script]')?.addEventListener('click', async (ev) => {
        const btn = ev.currentTarget;
        try {
            await navigator.clipboard.writeText(script);
            btn.textContent = 'Copied';
            setTimeout(() => {
                btn.textContent = 'Copy script';
            }, 1200);
        } catch {
            /* ignore */
        }
    });
    pinSnapshot.querySelector('[data-copy-json]')?.addEventListener('click', async (ev) => {
        const btn = ev.currentTarget;
        try {
            await navigator.clipboard.writeText(json);
            btn.textContent = 'Copied';
            setTimeout(() => {
                btn.textContent = 'Copy JSON';
            }, 1200);
        } catch {
            /* ignore */
        }
    });
}

function renderBrowse() {
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

function renderBackends(data) {
    const rows = data.backends || [];
    if (!rows.length) {
        backendsPanel.textContent = 'No backends reported.';
        return;
    }
    const b = rows[0];
    isMockBackend = String(b.backend_id || '').startsWith('mock.');
    backendsPanel.innerHTML = `
        <dl>
            <dt>Backend ID</dt><dd><code>${escapeHtml(b.backend_id)}</code></dd>
            <dt>Communicator</dt><dd>${escapeHtml(b.communicator)} (${escapeHtml(b.lab_mode)})</dd>
            <dt>Components</dt><dd>${escapeHtml(b.component_count)}</dd>
            <dt>System status</dt><dd>${escapeHtml(b.system_status || '—')}</dd>
        </dl>`;
    if (publishHint) {
        publishHint.textContent = isMockBackend
            ? 'Mock backend: publish requests from Twin UI are auto-approved.'
            : 'Real backend: approve or reject pending requests below.';
    }
}

function renderRequests(requests) {
    const pending = (requests || []).filter((r) => r.status === 'pending');
    if (!pending.length) {
        requestsTable.hidden = true;
        requestsEmpty.hidden = false;
        return;
    }
    requestsEmpty.hidden = true;
    requestsTable.hidden = false;
    requestsBody.innerHTML = pending
        .map(
            (req) => `
            <tr data-request-id="${escapeHtml(req.request_id)}">
                <td>
                    <code>${escapeHtml(req.request_id)}</code><br>
                    <span style="color:var(--muted)">${escapeHtml(req.message || '—')}</span>
                </td>
                <td>${escapeHtml(req.repo_id)}@${escapeHtml(req.branch || 'main')}<br>
                    <code>${escapeHtml(shortId(req.configuration_id))}</code></td>
                <td>
                    ${
                        isMockBackend
                            ? ''
                            : `<button type="button" data-approve="${escapeHtml(req.request_id)}">Approve</button>
                               <button type="button" data-reject="${escapeHtml(req.request_id)}">Reject</button>`
                    }
                </td>
            </tr>`,
        )
        .join('');

    requestsBody.querySelectorAll('button[data-approve]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const pinId = prompt('Catalog pin id (optional):', '');
            if (pinId === null) return;
            await approvePublishRequest(btn.dataset.approve, {
                pinId: pinId.trim() || undefined,
            });
            await refresh();
        });
    });
    requestsBody.querySelectorAll('button[data-reject]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const reason = prompt('Rejection reason (optional):', '');
            if (reason === null) return;
            await rejectPublishRequest(btn.dataset.reject, { reason: reason.trim() });
            await refresh();
        });
    });
}

async function refresh({ soft = false } = {}) {
    try {
        const [backends, catalog, requestsPayload, reposPayload] = await Promise.all([
            fetchJson('/api/backends'),
            fetchCatalogPins(),
            fetchPublishRequests(),
            fetchControlRepos().catch(() => ({ repos: [] })),
        ]);
        allPins = catalog.pins || [];
        repoMetaById = new Map(
            (reposPayload.repos || []).map((r) => [r.repo_id, r]),
        );
        renderBackends(backends);
        renderRequests(requestsPayload.requests || []);
        if (soft && selectedBranch) {
            // Stay on the graph; only refresh pin metadata for the open node.
            if (selectedCommitId) {
                selectedPin = pinByCommit(selectedRepoId, selectedCommitId);
                renderPinDetail(selectedPin, selectedCommitId);
                updateGraphHint(graphNodes);
            }
            return;
        }
        renderBrowse();
    } catch (err) {
        backendsPanel.textContent = `Error: ${err.message}`;
    }
}

refresh();
setInterval(() => refresh({ soft: true }), 8000);

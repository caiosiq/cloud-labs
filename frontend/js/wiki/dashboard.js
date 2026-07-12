/**
 * Capability Wiki — live component / measurable documentation hub.
 *
 * Data sources (backend-scoped):
 *   GET /api/backends
 *   GET /api/catalog/library-rows?backend_id=
 *   GET /api/catalog/active-tags?backend_id=
 *   GET /api/platform/registries
 */
import {
    fetchBackends,
    getSelectedBackendId,
    setSelectedBackendId,
    withBackendQuery,
} from '../state/backend-selection.js';
import { normalizeCapabilities } from '../component-state.js';
import { loadPlatformRegistries } from '../lab-capabilities.js';
import {
    connectPreamble,
    measurableHandle,
    torchscriptTermSnippet,
    tunableHandles,
} from './handles.js';

const backendSelect = document.getElementById('backend-select');
const compFilter = document.getElementById('comp-filter');
const compList = document.getElementById('comp-list');
const kernelList = document.getElementById('kernel-list');
const detail = document.getElementById('detail');
const tabComponents = document.getElementById('tab-components');
const tabKernels = document.getElementById('tab-kernels');
const componentsPane = document.getElementById('components-pane');
const kernelsPane = document.getElementById('kernels-pane');

/** @type {object[]} */
let backends = [];
/** @type {Map<string, object>} */
let rowsByTag = new Map();
/** @type {Set<string>} */
let activeTags = new Set();
/** @type {object|null} */
let registries = null;
/** @type {string|null} */
let selectedTag = null;
/** @type {object[]} */
let kernels = [];
/** @type {string|null} */
let selectedKernelId = null;
/** @type {'components'|'kernels'} */
let activeTab = 'components';

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} → ${res.status}`);
    return res.json();
}

async function copyText(text, btn) {
    try {
        await navigator.clipboard.writeText(text);
        if (btn) {
            const prev = btn.textContent;
            btn.textContent = 'Copied';
            btn.classList.add('copied');
            setTimeout(() => {
                btn.textContent = prev;
                btn.classList.remove('copied');
            }, 1200);
        }
    } catch {
        window.prompt('Copy to clipboard:', text);
    }
}

function filteredRows() {
    const q = (compFilter.value || '').trim().toLowerCase();
    const rows = [...rowsByTag.values()].sort((a, b) =>
        String(a.tag_id || '').localeCompare(String(b.tag_id || '')),
    );
    if (!q) return rows;
    return rows.filter((row) => {
        const hay = [row.tag_id, row.name, row.type, row.id]
            .map((x) => String(x || '').toLowerCase())
            .join(' ');
        return hay.includes(q);
    });
}

function renderSidebar() {
    const rows = filteredRows();
    if (!rows.length) {
        compList.innerHTML = '<div class="empty">No components match.</div>';
        return;
    }
    compList.innerHTML = rows
        .map((row) => {
            const tag = row.tag_id;
            const onBench = activeTags.has(tag);
            const active = tag === selectedTag ? 'active' : '';
            const badge = onBench
                ? '<span class="badge on-bench">on bench</span>'
                : '<span class="badge library">library</span>';
            return `
                <button type="button" class="comp-item ${active}" data-tag="${escapeHtml(tag)}">
                    <span class="name">${escapeHtml(row.name || tag)}${badge}</span>
                    <span class="meta">${escapeHtml(tag)} · ${escapeHtml(row.type || '—')}</span>
                </button>`;
        })
        .join('');

    compList.querySelectorAll('button[data-tag]').forEach((btn) => {
        btn.addEventListener('click', () => {
            selectedTag = btn.dataset.tag;
            renderSidebar();
            renderDetail();
        });
    });
}

function parameterRows(row) {
    const out = [];
    const props = row.properties && typeof row.properties === 'object' ? row.properties : {};
    for (const [key, value] of Object.entries(props)) {
        out.push({ key: `properties.${key}`, value });
    }
    const extras = [
        ['motor_ids', row.motor_ids],
        ['motor_controller', row.motor_controller],
        ['height_mm', row.height_mm],
        ['size', row.size],
        ['id', row.id],
        ['type', row.type],
    ];
    for (const [key, value] of extras) {
        if (value == null || value === '') continue;
        if (key in props) continue;
        out.push({ key, value });
    }
    return out;
}

function enrichTunableDecl(field, decl) {
    const reg = registries?.tunables?.[field];
    if (!reg) return decl || {};
    return {
        ...decl,
        widget: decl?.widget || reg.widget,
        write_primitive: decl?.write_primitive || reg.write_primitive,
    };
}

function renderDetail() {
    const backendId = getSelectedBackendId();
    if (!backendId) {
        detail.innerHTML = '<div class="empty">Choose a backend to load its capability catalog.</div>';
        return;
    }
    if (!selectedTag || !rowsByTag.has(selectedTag)) {
        detail.innerHTML = `
            <p class="connect-hint">Connect snippet for this backend:</p>
            <pre class="snippet">${escapeHtml(connectPreamble(backendId))}</pre>
            <p class="empty" style="margin-top:16px">Select a component from the left.</p>`;
        return;
    }

    const row = rowsByTag.get(selectedTag);
    const caps = normalizeCapabilities(row.capabilities);
    const tunables = caps.statecontrol?.tunables || {};
    const measurables = caps.statecontrol?.measurables || {};
    const teleop = caps.telemetry?.teleop || {};
    const liveFeed = caps.telemetry?.live_feed || {};
    const primitives = Array.isArray(caps.primitives) ? caps.primitives : [];
    const onBench = activeTags.has(selectedTag);

    const tunableRows = [];
    for (const [field, decl] of Object.entries(tunables)) {
        tunableRows.push(...tunableHandles(selectedTag, field, enrichTunableDecl(field, decl), row));
    }
    const measurableRows = Object.entries(measurables).map(([field, decl]) =>
        measurableHandle(selectedTag, field, decl || {}),
    );
    const params = parameterRows(row);

    detail.innerHTML = `
        <div class="detail-header">
            <h2>${escapeHtml(row.name || selectedTag)}</h2>
            <code>${escapeHtml(selectedTag)}</code>
            <span class="badge ${onBench ? 'on-bench' : 'library'}">${onBench ? 'on bench' : 'library only'}</span>
            <code>${escapeHtml(row.type || '—')}</code>
        </div>
        <p class="connect-hint">
            Backend <code>${escapeHtml(backendId)}</code> ·
            <button type="button" class="copy" data-copy-connect>Copy connect()</button>
        </p>

        <section class="section">
            <h3>Tunables (${tunableRows.length})</h3>
            ${
                tunableRows.length
                    ? `<table>
                        <thead>
                            <tr>
                                <th>Path</th>
                                <th>Widget</th>
                                <th>Unit</th>
                                <th>Bounds</th>
                                <th>Script handle</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody>
                            ${tunableRows
                                .map(
                                    (t, i) => `
                                <tr>
                                    <td class="mono">${escapeHtml(t.path)}</td>
                                    <td>${escapeHtml(t.widget || '—')}</td>
                                    <td>${escapeHtml(t.unit || '—')}</td>
                                    <td class="mono">${escapeHtml(t.bounds || '—')}</td>
                                    <td><pre class="snippet">${escapeHtml(t.snippet)}</pre>${
                                        t.note ? `<div class="empty">${escapeHtml(t.note)}</div>` : ''
                                    }</td>
                                    <td><button type="button" class="copy" data-copy-tunable="${i}">Copy</button></td>
                                </tr>`,
                                )
                                .join('')}
                        </tbody>
                    </table>`
                    : '<div class="empty">No tunables declared.</div>'
            }
        </section>

        <section class="section">
            <h3>Measurables (${measurableRows.length})</h3>
            ${
                measurableRows.length
                    ? `<table>
                        <thead>
                            <tr>
                                <th>Path</th>
                                <th>Widget</th>
                                <th>Format / shape</th>
                                <th>Domain</th>
                                <th>Script handle</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody>
                            ${measurableRows
                                .map(
                                    (m, i) => `
                                <tr>
                                    <td class="mono">${escapeHtml(m.path)}</td>
                                    <td>${escapeHtml(m.widget)}</td>
                                    <td class="mono">${escapeHtml(m.format)} / ${escapeHtml(m.shape)}</td>
                                    <td>${escapeHtml(m.domain)}</td>
                                    <td><pre class="snippet">${escapeHtml(m.snippet)}</pre></td>
                                    <td><button type="button" class="copy" data-copy-measurable="${i}">Copy</button></td>
                                </tr>`,
                                )
                                .join('')}
                        </tbody>
                    </table>`
                    : '<div class="empty">No measurables declared.</div>'
            }
        </section>

        <section class="section">
            <h3>Parameters (${params.length})</h3>
            ${
                params.length
                    ? `<table>
                        <thead><tr><th>Key</th><th>Value</th></tr></thead>
                        <tbody>
                            ${params
                                .map(
                                    (p) => `
                                <tr>
                                    <td class="mono">${escapeHtml(p.key)}</td>
                                    <td class="mono">${escapeHtml(
                                        typeof p.value === 'string'
                                            ? p.value
                                            : JSON.stringify(p.value),
                                    )}</td>
                                </tr>`,
                                )
                                .join('')}
                        </tbody>
                    </table>`
                    : '<div class="empty">No parameters / properties on this row.</div>'
            }
        </section>

        <section class="section">
            <h3>Primitives (${primitives.length})</h3>
            ${
                primitives.length
                    ? `<div class="pill-row">${primitives
                          .map((pid) => {
                              const meta = registries?.primitives?.[pid];
                              const ro = meta?.read_only ? ' ro' : '';
                              const title = meta
                                  ? `kind=${meta.kind || '—'}; handler=${meta.handler || '—'}`
                                  : '';
                              return `<span class="pill${ro}" title="${escapeHtml(title)}">${escapeHtml(pid)}</span>`;
                          })
                          .join('')}</div>`
                    : '<div class="empty">No primitives list on this component.</div>'
            }
        </section>

        <section class="section">
            <h3>Telemetry</h3>
            <table>
                <thead><tr><th>Channel</th><th>Keys</th></tr></thead>
                <tbody>
                    <tr>
                        <td>teleop</td>
                        <td class="mono">${escapeHtml(Object.keys(teleop).join(', ') || '—')}</td>
                    </tr>
                    <tr>
                        <td>live_feed</td>
                        <td class="mono">${escapeHtml(Object.keys(liveFeed).join(', ') || '—')}</td>
                    </tr>
                </tbody>
            </table>
        </section>
    `;

    detail.querySelector('[data-copy-connect]')?.addEventListener('click', (ev) => {
        copyText(connectPreamble(backendId), ev.currentTarget);
    });

    detail.querySelectorAll('[data-copy-tunable]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const i = Number(btn.dataset.copyTunable);
            const rowHandle = tunableRows[i];
            if (rowHandle) copyText(rowHandle.snippet, btn);
        });
    });

    detail.querySelectorAll('[data-copy-measurable]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const i = Number(btn.dataset.copyMeasurable);
            const rowHandle = measurableRows[i];
            if (rowHandle) copyText(rowHandle.snippet, btn);
        });
    });
}

function setActiveTab(tab) {
    activeTab = tab;
    const isComp = tab === 'components';
    tabComponents?.classList.toggle('active', isComp);
    tabKernels?.classList.toggle('active', !isComp);
    if (componentsPane) componentsPane.hidden = !isComp;
    if (kernelsPane) kernelsPane.hidden = isComp;
    if (isComp) {
        renderSidebar();
        renderDetail();
    } else {
        renderKernelSidebar();
        renderKernelDetail();
    }
}

function renderKernelSidebar() {
    if (!kernelList) return;
    if (!kernels.length) {
        kernelList.innerHTML = '<div class="empty">No kernels registered.</div>';
        return;
    }
    kernelList.innerHTML = kernels
        .map((k) => {
            const active = k.id === selectedKernelId ? 'active' : '';
            const runtime = k.runtime || 'builtin';
            const scope = k.scope || (String(k.id || '').startsWith('session.') ? 'session' : 'catalog');
            const badge =
                scope === 'session'
                    ? '<span class="badge on-bench">session</span>'
                    : runtime === 'torchscript'
                      ? '<span class="badge on-bench">torchscript</span>'
                      : `<span class="badge library">${escapeHtml(runtime)}</span>`;
            return `
                <button type="button" class="comp-item ${active}" data-kernel="${escapeHtml(k.id)}">
                    <span class="name">${escapeHtml(k.label || k.id)}${badge}</span>
                    <span class="meta">${escapeHtml(k.id)} · ${escapeHtml(k.backend || 'any')}</span>
                </button>`;
        })
        .join('');
    kernelList.querySelectorAll('button[data-kernel]').forEach((btn) => {
        btn.addEventListener('click', () => {
            selectedKernelId = btn.dataset.kernel;
            renderKernelSidebar();
            renderKernelDetail();
        });
    });
}

function renderKernelDetail() {
    const backendId = getSelectedBackendId();
    const row = kernels.find((k) => k.id === selectedKernelId);
    if (!row) {
        detail.innerHTML = '<div class="empty">Select a kernel from the left.</div>';
        return;
    }
    const snip = torchscriptTermSnippet(row.id, 'tag_22');
    const inputs = Array.isArray(row.inputs) ? row.inputs : [];
    const outputs = Array.isArray(row.outputs) ? row.outputs : [];
    const present =
        row.artifact_present == null
            ? '—'
            : row.artifact_present
              ? 'yes'
              : 'missing';
    detail.innerHTML = `
        <div class="detail-header">
            <h2>${escapeHtml(row.label || row.id)}</h2>
            <code>${escapeHtml(row.id)}</code>
            <span class="badge ${row.runtime === 'torchscript' ? 'on-bench' : 'library'}">${escapeHtml(
                row.runtime || 'builtin',
            )}</span>
        </div>
        <p class="connect-hint">${escapeHtml(row.description || '')}</p>
        <section class="section">
            <h3>Metadata</h3>
            <table>
                <tbody>
                    <tr><td>Backend</td><td class="mono">${escapeHtml(row.backend || '—')}</td></tr>
                    <tr><td>Phase</td><td class="mono">${escapeHtml(row.phase || '—')}</td></tr>
                    <tr><td>Artifact</td><td class="mono">${escapeHtml(row.artifact || '—')}</td></tr>
                    <tr><td>Artifact present</td><td>${escapeHtml(present)}</td></tr>
                    <tr><td>Scope</td><td class="mono">${escapeHtml(row.scope || 'catalog')}</td></tr>
                    <tr><td>Output kind</td><td class="mono">${escapeHtml(row.output_kind || '—')}</td></tr>
                    <tr><td>Feature names</td><td class="mono">${escapeHtml(
                        (row.feature_names || []).join(', ') || '—',
                    )}</td></tr>
                    <tr><td>Hooks</td><td class="mono">${escapeHtml((row.hooks || []).join(', ') || '—')}</td></tr>
                </tbody>
            </table>
        </section>
        <section class="section">
            <h3>Inputs / outputs</h3>
            <pre class="snippet">${escapeHtml(JSON.stringify({ inputs, outputs }, null, 2))}</pre>
        </section>
        ${
            row.runtime === 'torchscript'
                ? `<section class="section">
            <h3>Objective term (copy)</h3>
            <pre class="snippet">${escapeHtml(snip.termJson)}</pre>
            <p style="margin-top:8px">
                <button type="button" class="copy" data-copy-term>Copy term JSON</button>
                <button type="button" class="copy" data-copy-sdk>Copy SDK snippet</button>
            </p>
            <p class="empty" style="margin-top:8px">Also pass <code>${escapeHtml(snip.kernelsLine)}</code> on the job / OPTIMIZE payload. Backend: <code>${escapeHtml(backendId || '')}</code></p>
            <pre class="snippet">${escapeHtml(snip.sdkSnippet)}</pre>
        </section>`
                : `<section class="section"><h3>Usage</h3><div class="empty">Builtin hook — reference via <code>kernels=["${escapeHtml(row.id)}"]</code> on closed-loop jobs.</div></section>`
        }
    `;
    detail.querySelector('[data-copy-term]')?.addEventListener('click', (ev) => {
        copyText(snip.termJson, ev.currentTarget);
    });
    detail.querySelector('[data-copy-sdk]')?.addEventListener('click', (ev) => {
        copyText(snip.sdkSnippet, ev.currentTarget);
    });
}

async function loadKernels() {
    try {
        const data = await fetchJson('/api/kernels');
        kernels = Array.isArray(data.kernels) ? data.kernels : [];
        if (!selectedKernelId || !kernels.some((k) => k.id === selectedKernelId)) {
            selectedKernelId = kernels.find((k) => k.runtime === 'torchscript')?.id
                || kernels[0]?.id
                || null;
        }
    } catch (err) {
        kernels = [];
        if (kernelList) {
            kernelList.innerHTML = `<div class="empty">Kernels load failed: ${escapeHtml(err.message)}</div>`;
        }
    }
}

function fillBackendSelect() {
    const ready = backends.filter((b) => b.availability === 'ready');
    const pool = ready.length ? ready : backends;
    backendSelect.innerHTML = pool
        .map((b) => {
            const label = `${b.backend_id} · ${b.communicator || b.lab_mode || '?'}${
                b.availability !== 'ready' ? ' (unavailable)' : ''
            }`;
            return `<option value="${escapeHtml(b.backend_id)}">${escapeHtml(label)}</option>`;
        })
        .join('');

    let current = getSelectedBackendId();
    if (!current || !pool.some((b) => b.backend_id === current)) {
        current = pool[0]?.backend_id || null;
        if (current) setSelectedBackendId(current);
    }
    if (current) backendSelect.value = current;
}

async function loadCatalogForBackend() {
    const backendId = getSelectedBackendId();
    rowsByTag = new Map();
    activeTags = new Set();
    selectedTag = null;

    if (!backendId) {
        renderSidebar();
        renderDetail();
        return;
    }

    compList.innerHTML = '<div class="empty">Loading catalog…</div>';
    detail.innerHTML = '<div class="empty">Loading…</div>';

    const [libraryRows, active] = await Promise.all([
        fetchJson(withBackendQuery('/api/catalog/library-rows')),
        fetchJson(withBackendQuery('/api/catalog/active-tags')),
    ]);

    (Array.isArray(libraryRows) ? libraryRows : []).forEach((row) => {
        if (row?.tag_id) rowsByTag.set(row.tag_id, row);
    });
    (Array.isArray(active?.tag_ids) ? active.tag_ids : []).forEach((t) => activeTags.add(t));

    // Prefer first on-bench component if present
    const firstActive = [...activeTags].find((t) => rowsByTag.has(t));
    selectedTag = firstActive || [...rowsByTag.keys()][0] || null;

    renderSidebar();
    renderDetail();
}

async function boot() {
    try {
        backends = await fetchBackends();
        registries = await loadPlatformRegistries();
        fillBackendSelect();
        await Promise.all([loadCatalogForBackend(), loadKernels()]);
        if (activeTab === 'kernels') {
            renderKernelSidebar();
            renderKernelDetail();
        }
    } catch (err) {
        detail.innerHTML = `<div class="empty">Failed to load wiki: ${escapeHtml(err.message)}</div>`;
        if (compList) compList.innerHTML = '<div class="empty">Error</div>';
    }
}

backendSelect.addEventListener('change', async () => {
    setSelectedBackendId(backendSelect.value);
    await loadCatalogForBackend();
    if (activeTab === 'kernels') {
        renderKernelSidebar();
        renderKernelDetail();
    }
});

compFilter?.addEventListener('input', () => renderSidebar());
tabComponents?.addEventListener('click', () => setActiveTab('components'));
tabKernels?.addEventListener('click', () => setActiveTab('kernels'));

boot();

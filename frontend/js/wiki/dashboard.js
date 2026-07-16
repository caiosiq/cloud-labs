/**
 * Cloud Labs Wiki — Learn curriculum + live Backends hub.
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
    componentPhysicalInterpretation,
    kernelPhysicalInterpretation,
    measurableHandle,
    measurablePhysicalInterpretation,
    torchscriptTermSnippet,
    tunableHandles,
} from './handles.js';
import { renderMarkdown } from './markdown.js';
import {
    getCatalogHostNodes,
    hideBackendsHub,
    initBackendsHub,
    parseBackendsHash,
    showBackendsHub,
} from './backends-hub.js';

const GUIDES_BASE = '/static/wiki/guides';

const sectionBar = document.getElementById('section-bar');
const backendSelect = document.getElementById('backend-select');
const catalogControls = document.getElementById('catalog-controls');
const guidePane = document.getElementById('guide-pane');
const guideList = document.getElementById('guide-list');
const guideListLabel = document.getElementById('guide-list-label');
const compFilter = document.getElementById('comp-filter');
const compList = document.getElementById('comp-list');
const kernelList = document.getElementById('kernel-list');
const detailMain = document.getElementById('detail');
/** @type {HTMLElement|null} */
let detail = detailMain;
const tabComponents = document.getElementById('tab-components');
const tabKernels = document.getElementById('tab-kernels');
const componentsPane = document.getElementById('components-pane');
const kernelsPane = document.getElementById('kernels-pane');

/** @type {object|null} */
let manifest = null;
/** @type {string} */
let activeSectionId = 'learn';
/** @type {string|null} */
let activeChapterId = null;
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
let catalogTab = 'components';
let catalogLoaded = false;

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

async function fetchText(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} → ${res.status}`);
    return res.text();
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

function normalizeSectionId(sectionId) {
    // Legacy hashes → Backends hub.
    if (sectionId === 'catalog' || sectionId === 'capabilities') return 'backends';
    return sectionId;
}

function currentSection() {
    return (manifest?.sections || []).find((s) => s.id === activeSectionId) || null;
}

function sectionChapters(section) {
    return Array.isArray(section?.chapters) ? section.chapters : [];
}

function readHash() {
    const raw = (window.location.hash || '').replace(/^#/, '');
    if (!raw) return null;
    const parts = raw.split('/');
    const section = parts[0] || null;
    const rest = parts.slice(1).join('/') || null;
    const chapter = parts[1] || null;
    return { section, chapter, rest };
}

function writeHash() {
    if (!activeSectionId) return;
    const sec = currentSection();
    if (sec?.mode === 'backends') {
        return;
    }
    if (activeChapterId) {
        window.history.replaceState(null, '', `#${activeSectionId}/${activeChapterId}`);
    } else {
        window.history.replaceState(null, '', `#${activeSectionId}`);
    }
}

function renderSectionBar() {
    if (!sectionBar || !manifest) return;
    const sections = manifest.sections || [];
    sectionBar.innerHTML = sections
        .map((s) => {
            const active = s.id === activeSectionId ? 'active' : '';
            return `<button type="button" class="section-btn ${active}" data-section="${escapeHtml(s.id)}">${escapeHtml(s.label)}</button>`;
        })
        .join('');
    sectionBar.querySelectorAll('[data-section]').forEach((btn) => {
        btn.addEventListener('click', () => setWikiSection(btn.dataset.section));
    });
}

function setCatalogChrome(visible) {
    if (catalogControls) catalogControls.hidden = true; // picker lives in Backends hub now
    if (guidePane) guidePane.hidden = visible;
    if (!visible) {
        if (componentsPane) componentsPane.hidden = true;
        if (kernelsPane) kernelsPane.hidden = true;
    }
}

function leaveCatalogFromHub() {
    detail = detailMain;
    const sidebar = document.querySelector('.sidebar');
    if (sidebar && componentsPane && componentsPane.parentElement !== sidebar) {
        sidebar.appendChild(componentsPane);
    }
    if (sidebar && kernelsPane && kernelsPane.parentElement !== sidebar) {
        sidebar.appendChild(kernelsPane);
    }
    if (componentsPane) componentsPane.hidden = true;
    if (kernelsPane) kernelsPane.hidden = true;
}

async function mountCatalogIntoHub(tab) {
    const { side, detail: hostDetail } = getCatalogHostNodes();
    if (!side || !hostDetail) return;

    detail = hostDetail;
    // Reparent list panes into hub sidebar host
    side.innerHTML = '';
    if (componentsPane) side.appendChild(componentsPane);
    if (kernelsPane) side.appendChild(kernelsPane);

    catalogTab = tab === 'kernels' ? 'kernels' : 'components';
    if (!catalogLoaded) {
        await ensureCatalog();
    } else {
        await loadCatalogForBackend();
        await loadKernels();
    }
    setCatalogTab(catalogTab);
}

async function setWikiSection(sectionId, chapterId = null, hashRest = null) {
    const sections = manifest?.sections || [];
    const normalized = normalizeSectionId(sectionId);
    const sec = sections.find((s) => s.id === normalized) || sections[0];
    if (!sec) return;
    activeSectionId = sec.id;

    if (sec.mode === 'backends') {
        activeChapterId = null;
        setCatalogChrome(false);
        if (guidePane) guidePane.hidden = true;
        renderSectionBar();
        const route = parseBackendsHash(sectionId, hashRest);
        await showBackendsHub(route || { backendId: null, tab: 'overview' });
        return;
    }

    hideBackendsHub();
    leaveCatalogFromHub();
    setCatalogChrome(false);
    if (guidePane) guidePane.hidden = false;
    const layout = document.querySelector('.layout');
    if (layout) layout.hidden = false;

    const chapters = sectionChapters(sec);
    if (!chapterId || !chapters.some((c) => c.id === chapterId)) {
        chapterId = chapters[0]?.id || null;
    }
    activeChapterId = chapterId;
    if (guideListLabel) {
        guideListLabel.textContent = sec.label === 'Learn' ? 'Chapters' : sec.label;
    }
    renderSectionBar();
    renderGuideSidebar();
    writeHash();
    await renderGuideArticle();
}

function renderGuideSidebar() {
    const sec = currentSection();
    const chapters = sectionChapters(sec);
    if (!guideList) return;
    if (!chapters.length) {
        guideList.innerHTML = '<div class="empty">No chapters.</div>';
        return;
    }
    guideList.innerHTML = chapters
        .map((ch, idx) => {
            const active = ch.id === activeChapterId ? 'active' : '';
            return `
                <button type="button" class="guide-item ${active}" data-chapter="${escapeHtml(ch.id)}" style="animation-delay:${idx * 35}ms">
                    ${escapeHtml(`${idx + 1}. ${ch.title}`)}
                </button>`;
        })
        .join('');
    guideList.querySelectorAll('[data-chapter]').forEach((btn) => {
        btn.addEventListener('click', () => {
            activeChapterId = btn.dataset.chapter;
            renderGuideSidebar();
            writeHash();
            renderGuideArticle();
        });
    });
}

async function renderGuideArticle() {
    const sec = currentSection();
    const chapters = sectionChapters(sec);
    const ch = chapters.find((c) => c.id === activeChapterId);
    if (!ch) {
        detail.innerHTML = '<div class="empty">Select a chapter.</div>';
        return;
    }
    detail.innerHTML = '<div class="empty">Loading chapter…</div>';
    try {
        const md = await fetchText(`${GUIDES_BASE}/${ch.file}`);
        const idx = chapters.findIndex((c) => c.id === ch.id);
        const prev = idx > 0 ? chapters[idx - 1] : null;
        const next = idx >= 0 && idx < chapters.length - 1 ? chapters[idx + 1] : null;
        detail.innerHTML = `
            <article class="guide-article">
                ${renderMarkdown(md)}
                <div class="guide-nav">
                    <button type="button" data-nav-prev ${prev ? '' : 'disabled'}>
                        ${prev ? `← ${escapeHtml(prev.title)}` : '←'}
                    </button>
                    <button type="button" data-nav-next ${next ? '' : 'disabled'}>
                        ${next ? `${escapeHtml(next.title)} →` : '→'}
                    </button>
                </div>
            </article>`;
        await inlineGuideFigures(detail.querySelector('.guide-article'));
        detail.querySelector('[data-nav-prev]')?.addEventListener('click', () => {
            if (prev) {
                activeChapterId = prev.id;
                renderGuideSidebar();
                writeHash();
                renderGuideArticle();
            }
        });
        detail.querySelector('[data-nav-next]')?.addEventListener('click', () => {
            if (next) {
                activeChapterId = next.id;
                renderGuideSidebar();
                writeHash();
                renderGuideArticle();
            }
        });
    } catch (err) {
        detail.innerHTML = `<div class="empty">Failed to load chapter: ${escapeHtml(err.message)}</div>`;
    }
}

/**
 * Replace guide figure &lt;img&gt; nodes with inlined &lt;svg&gt; in the live DOM.
 * External SVG &lt;img&gt; tags were failing to paint inside the wiki scroll pane.
 * @param {Element | null} root
 */
async function inlineGuideFigures(root) {
    if (!root) return;
    const imgs = [...root.querySelectorAll('img.guide-figure')];
    if (!imgs.length) return;
    await Promise.all(
        imgs.map(async (img) => {
            const src = img.getAttribute('src');
            if (!src) return;
            try {
                const svgText = (await fetchText(src)).trim();
                if (!svgText.startsWith('<svg')) {
                    throw new Error('response is not SVG');
                }
                const holder = document.createElement('div');
                holder.innerHTML = svgText;
                const svg = holder.querySelector('svg');
                if (!svg) throw new Error('no <svg> root');
                svg.classList.add('guide-figure');
                svg.setAttribute('role', 'img');
                if (img.alt) svg.setAttribute('aria-label', img.alt);
                img.replaceWith(svg);
            } catch (err) {
                console.warn('Wiki figure failed to inline:', src, err);
            }
        }),
    );
}

function setCatalogTab(tab) {
    catalogTab = tab;
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

function filteredRows() {
    const q = (compFilter?.value || '').trim().toLowerCase();
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
    if (!compList) return;
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
        detail.innerHTML = '<div class="empty">Choose a backend to load its capabilities.</div>';
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
    const primitives = Array.isArray(caps.primitives) ? caps.primitives : [];
    const onBench = activeTags.has(selectedTag);

    const tunableRows = [];
    for (const [field, decl] of Object.entries(tunables)) {
        tunableRows.push(...tunableHandles(selectedTag, field, enrichTunableDecl(field, decl), row));
    }
    const measurableRows = Object.entries(measurables)
        .map(([field, decl]) => measurableHandle(selectedTag, field, decl || {}))
        .filter(Boolean);
    const params = parameterRows(row);

    detail.innerHTML = `
        <div class="detail-header">
            <h2>${escapeHtml(row.name || selectedTag)}</h2>
            <code>${escapeHtml(selectedTag)}</code>
            <span class="badge ${onBench ? 'on-bench' : 'library'}">${onBench ? 'on bench' : 'library only'}</span>
            <code>${escapeHtml(row.type || '—')}</code>
        </div>
        <section class="section">
            <h3>Physical interpretation</h3>
            <p class="connect-hint">${escapeHtml(componentPhysicalInterpretation(row))}</p>
        </section>
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
                                <th>What it measures</th>
                                <th>Tensor (dtype · layout · axes)</th>
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
                                    <td>${escapeHtml(
                                        measurablePhysicalInterpretation(m.field, {
                                            description: m.description,
                                            domain: m.domain,
                                            physical_interpretation: m.physical_interpretation,
                                            layout: m.tensor?.layout,
                                        }),
                                    )}</td>
                                    <td class="mono" style="white-space:pre-line;font-size:11px;line-height:1.45">${escapeHtml(m.tensorText || '—')}</td>
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
            <p class="connect-hint">
                Declared actions for this component (catalog subset of the platform inventory).
                See Learn → <em>Primitives</em> for the full Cloud Labs verb set.
            </p>
            ${
                primitives.length
                    ? `<div class="primitive-chip-grid">${primitives
                          .map(
                              (p) =>
                                  `<code class="primitive-chip">${escapeHtml(String(p))}</code>`,
                          )
                          .join('')}</div>`
                    : '<div class="empty">No primitives declared on this row.</div>'
            }
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
        <section class="section">
            <h3>Physical interpretation</h3>
            <p class="connect-hint">${escapeHtml(kernelPhysicalInterpretation(row))}</p>
        </section>
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
            selectedKernelId =
                kernels.find((k) => k.runtime === 'torchscript')?.id || kernels[0]?.id || null;
        }
    } catch (err) {
        kernels = [];
        if (kernelList) {
            kernelList.innerHTML = `<div class="empty">Kernels load failed: ${escapeHtml(err.message)}</div>`;
        }
    }
}

function fillBackendSelect() {
    if (!backendSelect) return;
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

    if (!backendId) return;

    const [libraryRows, active] = await Promise.all([
        fetchJson(withBackendQuery('/api/catalog/library-rows')),
        fetchJson(withBackendQuery('/api/catalog/active-tags')),
    ]);

    (Array.isArray(libraryRows) ? libraryRows : []).forEach((row) => {
        if (row?.tag_id) rowsByTag.set(row.tag_id, row);
    });
    (Array.isArray(active?.tag_ids) ? active.tag_ids : []).forEach((t) => activeTags.add(t));

    const firstActive = [...activeTags].find((t) => rowsByTag.has(t));
    selectedTag = firstActive || [...rowsByTag.keys()][0] || null;
}

async function ensureCatalog() {
    if (catalogLoaded) return;
    backends = await fetchBackends();
    registries = await loadPlatformRegistries();
    fillBackendSelect();
    await Promise.all([loadCatalogForBackend(), loadKernels()]);
    catalogLoaded = true;
}

async function boot() {
    try {
        initBackendsHub({
            onCatalogTab: (tab) => mountCatalogIntoHub(tab),
            onLeaveCatalog: () => leaveCatalogFromHub(),
        });
        manifest = await fetchJson(`${GUIDES_BASE}/manifest.json`);
        const hash = readHash();
        const defaultSection = hash?.section || manifest.defaultSection || 'learn';
        const defaultChapter = hash?.chapter || manifest.defaultChapter || null;
        await setWikiSection(defaultSection, defaultChapter, hash?.rest || null);
    } catch (err) {
        const host = detail || detailMain;
        if (host) {
            host.innerHTML = `<div class="empty">Failed to load Wiki: ${escapeHtml(err.message)}</div>`;
        }
    }
}

backendSelect?.addEventListener('change', async () => {
    setSelectedBackendId(backendSelect.value);
    catalogLoaded = false;
    await ensureCatalog();
    if (currentSection()?.mode === 'backends') {
        setCatalogTab(catalogTab);
    }
});

compFilter?.addEventListener('input', () => renderSidebar());
tabComponents?.addEventListener('click', () => setCatalogTab('components'));
tabKernels?.addEventListener('click', () => setCatalogTab('kernels'));

window.addEventListener('hashchange', () => {
    const hash = readHash();
    if (!hash?.section) return;
    void setWikiSection(hash.section, hash.chapter, hash.rest);
});

boot();

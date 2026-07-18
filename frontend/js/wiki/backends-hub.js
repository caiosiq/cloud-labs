/**
 * Wiki → Backends hub: lively gallery + per-backend drill-in
 * (overview / components / kernels / snapshots).
 */
import {
    fetchBackends,
    setSelectedBackendId,
    getSelectedBackendId,
} from '../state/backend-selection.js';
import { mountSnapshotsPanel } from './snapshots-panel.js';

const TABS = ['overview', 'components', 'kernels', 'snapshots'];

/** @type {object[]} */
let backends = [];
/** @type {string|null} */
let activeBackendId = null;
/** @type {string} */
let activeTab = 'overview';
/** @type {{ destroy: () => void } | null} */
let snapshotsMount = null;

/** @type {{
 *   onCatalogTab?: (tab: 'components'|'kernels') => Promise<void>|void,
 *   onLeaveCatalog?: () => void,
 * } | null} */
let hooks = null;

const hub = () => document.getElementById('backends-hub');
const galleryEl = () => document.getElementById('backends-gallery');
const detailEl = () => document.getElementById('backends-detail');
const learnLayout = () => document.querySelector('.layout');

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function writeHash() {
    if (!activeBackendId) {
        window.history.replaceState(null, '', '#backends');
        return;
    }
    window.history.replaceState(null, '', `#backends/${activeBackendId}/${activeTab}`);
}

/**
 * @param {string|null|undefined} section
 * @param {string|null|undefined} rest  e.g. "mock.default/components"
 */
export function parseBackendsHash(section, rest) {
    if (section !== 'backends' && section !== 'capabilities' && section !== 'catalog') {
        return null;
    }
    if (section === 'capabilities' || section === 'catalog') {
        const id = getSelectedBackendId() || backends[0]?.backend_id || null;
        return { backendId: id, tab: 'components' };
    }
    if (!rest) return { backendId: null, tab: 'overview' };
    const [backendId, tabRaw] = rest.split('/');
    const tab = TABS.includes(tabRaw) ? tabRaw : 'overview';
    return { backendId: backendId || null, tab };
}

export function initBackendsHub(deps = {}) {
    hooks = deps;
}

export async function showBackendsHub(route = { backendId: null, tab: 'overview' }) {
    const root = hub();
    if (!root) return;
    root.hidden = false;
    const layout = learnLayout();
    if (layout) layout.hidden = true;

    backends = await fetchBackends();
    activeBackendId = route.backendId;
    activeTab = route.tab || 'overview';

    if (activeBackendId && !backends.some((b) => b.backend_id === activeBackendId)) {
        activeBackendId = null;
        activeTab = 'overview';
    }

    if (!activeBackendId) {
        teardownSnapshots();
        hooks?.onLeaveCatalog?.();
        renderGallery();
        writeHash();
        return;
    }

    setSelectedBackendId(activeBackendId);
    await openBackendDetail(activeBackendId, activeTab);
}

export function hideBackendsHub() {
    const root = hub();
    if (root) root.hidden = true;
    const layout = learnLayout();
    if (layout) layout.hidden = false;
    teardownSnapshots();
    hooks?.onLeaveCatalog?.();
    activeBackendId = null;
}

function teardownSnapshots() {
    if (snapshotsMount) {
        snapshotsMount.destroy();
        snapshotsMount = null;
    }
}

function availabilityClass(b) {
    const a = b.availability || 'unknown';
    if (a === 'ready') return 'ready';
    if (a === 'unavailable') return 'unavailable';
    return 'warn';
}

function heroTone(backendId) {
    return String(backendId || '').startsWith('real.') ? 'real' : 'mock';
}

function resolveHeroSrc(backendId, image) {
    const tone = heroTone(backendId);
    const fallback =
        tone === 'real'
            ? '/static/wiki/backends/real-default.png'
            : '/static/wiki/backends/mock-default.png';
    let src = String(image || '').trim() || fallback;
    // Prefer PNG when registry still points at retired SVG heroes.
    if (/\.svg$/i.test(src)) src = src.replace(/\.svg$/i, '.png');
    return src;
}

/** Bind img error → CSS hero so cards never show a broken icon. */
function wireHeroImages(root) {
    if (!root) return;
    root.querySelectorAll('img[data-hero]').forEach((img) => {
        const applyFallback = () => {
            const media = img.closest('.backend-card-media, .backend-hero-media');
            if (media) {
                media.classList.add('hero-fallback');
                media.classList.add(`hero-${img.getAttribute('data-hero') || 'mock'}`);
            }
            img.hidden = true;
        };
        img.addEventListener('error', applyFallback, { once: true });
        if (img.complete && img.naturalWidth === 0) applyFallback();
    });
}

function renderGallery() {
    const g = galleryEl();
    const d = detailEl();
    if (d) d.hidden = true;
    if (!g) return;
    g.hidden = false;

    const cards = backends
        .map((b, i) => {
            const tone = heroTone(b.backend_id);
            const img = resolveHeroSrc(b.backend_id, b.image);
            const desc =
                b.description ||
                b.notes ||
                (b.communicator ? `${b.communicator} communicator` : 'Lab backend');
            const status = b.availability || 'unknown';
            const busy = b.active_job_id
                ? 'Job running'
                : b.session_lease
                  ? `Lease: ${b.session_lease.holder}`
                  : status === 'ready'
                    ? 'Idle — available'
                    : b.unavailable_reason || status;
            return `
            <button type="button" class="backend-card" data-backend-id="${escapeHtml(b.backend_id)}"
                style="animation-delay:${i * 70}ms">
                <div class="backend-card-media hero-${tone}">
                    <img src="${escapeHtml(img)}" alt="" loading="lazy" data-hero="${tone}" />
                    <span class="backend-card-glow" aria-hidden="true"></span>
                </div>
                <div class="backend-card-body">
                    <div class="backend-card-top">
                        <h3>${escapeHtml(b.label || b.backend_id)}</h3>
                        <span class="backend-pill ${availabilityClass(b)}">${escapeHtml(status)}</span>
                    </div>
                    <code class="backend-id">${escapeHtml(b.backend_id)}</code>
                    <p class="backend-desc">${escapeHtml(desc)}</p>
                    <p class="backend-meta">${escapeHtml(busy)}</p>
                </div>
            </button>`;
        })
        .join('');

    g.innerHTML = `
        <div class="backends-gallery-intro">
            <p class="eyebrow">Live labs</p>
            <h2>Backends</h2>
            <p class="lede">Pick a lab to explore components, kernels, and frozen snapshots. Twin and scripts connect to the same ids.</p>
        </div>
        <div class="backend-card-grid">${cards || '<p class="empty">No backends registered.</p>'}</div>`;

    wireHeroImages(g);

    g.querySelectorAll('[data-backend-id]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const id = btn.getAttribute('data-backend-id');
            void openBackendDetail(id, 'overview');
        });
    });
}

async function openBackendDetail(backendId, tab) {
    activeBackendId = backendId;
    activeTab = TABS.includes(tab) ? tab : 'overview';
    setSelectedBackendId(backendId);

    const g = galleryEl();
    const d = detailEl();
    if (g) g.hidden = true;
    if (!d) return;

    teardownSnapshots();
    hooks?.onLeaveCatalog?.();
    d.hidden = false;

    const b = backends.find((x) => x.backend_id === backendId);
    if (!b) {
        d.innerHTML = `<p class="empty">Unknown backend.</p>`;
        return;
    }

    const tone = heroTone(b.backend_id);
    const img = resolveHeroSrc(b.backend_id, b.image);
    d.innerHTML = `
        <button type="button" class="backend-back" data-action="back">← All backends</button>
        <header class="backend-hero">
            <div class="backend-hero-media hero-${tone}">
                <img src="${escapeHtml(img)}" alt="" data-hero="${tone}" />
                <span class="backend-card-glow" aria-hidden="true"></span>
            </div>
            <div class="backend-hero-copy">
                <span class="backend-pill ${availabilityClass(b)}">${escapeHtml(b.availability || 'unknown')}</span>
                <h2>${escapeHtml(b.label || b.backend_id)}</h2>
                <code>${escapeHtml(b.backend_id)}</code>
                <p>${escapeHtml(b.description || b.notes || '')}</p>
            </div>
        </header>
        <div class="backend-tabs" role="tablist">
            ${TABS.map(
                (t) =>
                    `<button type="button" role="tab" data-tab="${t}" class="${t === activeTab ? 'active' : ''}">${labelForTab(t)}</button>`,
            ).join('')}
        </div>
        <div class="backend-tab-panels">
            <div id="backend-panel-overview" class="backend-panel" ${activeTab === 'overview' ? '' : 'hidden'}></div>
            <div id="backend-panel-catalog" class="backend-panel backend-panel-catalog" ${activeTab === 'components' || activeTab === 'kernels' ? '' : 'hidden'}></div>
            <div id="backend-panel-snapshots" class="backend-panel" ${activeTab === 'snapshots' ? '' : 'hidden'}></div>
        </div>`;

    wireHeroImages(d);

    d.querySelector('[data-action="back"]')?.addEventListener('click', () => {
        activeBackendId = null;
        teardownSnapshots();
        hooks?.onLeaveCatalog?.();
        renderGallery();
        writeHash();
    });

    d.querySelectorAll('[data-tab]').forEach((btn) => {
        btn.addEventListener('click', () => {
            void setTab(btn.getAttribute('data-tab'));
        });
    });

    writeHash();
    await renderActiveTab();
}

function labelForTab(t) {
    return { overview: 'Overview', components: 'Components', kernels: 'Kernels', snapshots: 'Snapshots' }[
        t
    ];
}

async function setTab(tab) {
    activeTab = TABS.includes(tab) ? tab : 'overview';
    const d = detailEl();
    d?.querySelectorAll('[data-tab]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === activeTab);
    });
    const ov = document.getElementById('backend-panel-overview');
    const cat = document.getElementById('backend-panel-catalog');
    const snap = document.getElementById('backend-panel-snapshots');
    if (ov) ov.hidden = activeTab !== 'overview';
    if (cat) cat.hidden = !(activeTab === 'components' || activeTab === 'kernels');
    if (snap) snap.hidden = activeTab !== 'snapshots';
    writeHash();
    await renderActiveTab();
}

async function renderActiveTab() {
    const b = backends.find((x) => x.backend_id === activeBackendId);
    if (!b) return;

    if (activeTab === 'overview') {
        teardownSnapshots();
        hooks?.onLeaveCatalog?.();
        const panel = document.getElementById('backend-panel-overview');
        if (!panel) return;
        panel.innerHTML = `
            <dl class="backend-overview-dl">
                <div><dt>Communicator</dt><dd>${escapeHtml(b.communicator || '—')} ${b.lab_mode ? `(${escapeHtml(b.lab_mode)})` : ''}</dd></div>
                <div><dt>Health</dt><dd>${escapeHtml(b.health || b.availability || '—')}</dd></div>
                <div><dt>System</dt><dd>${escapeHtml(b.system_status || '—')}</dd></div>
                <div><dt>Components</dt><dd>${escapeHtml(b.component_count ?? '—')}</dd></div>
                <div><dt>Control repos</dt><dd>${escapeHtml((b.control_repos || []).join(', ') || '—')}</dd></div>
                <div><dt>Edge</dt><dd>${b.edge_attached ? 'Attached' : 'Not attached'}</dd></div>
                <div><dt>Session lease</dt><dd>${b.session_lease ? escapeHtml(b.session_lease.holder) : 'None'}</dd></div>
                <div><dt>Queued jobs</dt><dd>${escapeHtml(b.queued_jobs ?? 0)}</dd></div>
            </dl>
            ${
                b.unavailable_reason
                    ? `<p class="backend-warn">${escapeHtml(b.unavailable_reason)}</p>`
                    : ''
            }
            <p class="backend-cta-row">
                <a class="backend-cta" href="/twin">Open Twin</a>
                <button type="button" class="backend-cta ghost" data-goto="components">Browse components</button>
                <button type="button" class="backend-cta ghost" data-goto="snapshots">Browse snapshots</button>
            </p>`;
        panel.querySelectorAll('[data-goto]').forEach((btn) => {
            btn.addEventListener('click', () => void setTab(btn.getAttribute('data-goto')));
        });
        return;
    }

    if (activeTab === 'snapshots') {
        hooks?.onLeaveCatalog?.();
        const panel = document.getElementById('backend-panel-snapshots');
        if (!panel) return;
        teardownSnapshots();
        snapshotsMount = mountSnapshotsPanel(panel, { backendId: activeBackendId });
        return;
    }

    // components | kernels
    teardownSnapshots();
    hooks?.onLeaveCatalog?.();
    const panel = document.getElementById('backend-panel-catalog');
    if (panel) {
        panel.innerHTML = `
            <div class="backend-catalog-layout">
                <aside class="backend-catalog-side" id="backend-catalog-side"></aside>
                <div class="backend-catalog-detail" id="backend-catalog-detail"></div>
            </div>`;
    }
    await hooks?.onCatalogTab?.(activeTab === 'kernels' ? 'kernels' : 'components');
}

/**
 * Host nodes for dashboard.js to render component/kernel lists + detail into.
 * @returns {{ side: HTMLElement|null, detail: HTMLElement|null }}
 */
export function getCatalogHostNodes() {
    return {
        side: document.getElementById('backend-catalog-side'),
        detail: document.getElementById('backend-catalog-detail'),
    };
}

export function getActiveBackendRoute() {
    return { backendId: activeBackendId, tab: activeTab };
}

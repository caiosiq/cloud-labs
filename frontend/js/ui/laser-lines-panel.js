/**
 * Laser lines dock + edit modal.
 *
 * Each line in `store.laserLinesDoc.lines` becomes a colored "my_location" icon button:
 *   - Single click  → toggle visibility (PATCH /api/laser-lines/{id} { enabled }).
 *   - Double click  → open the geometry edit modal (PATCH … { p1, p2, confirm: true }).
 *
 * Tooltip shows the line's name, snap-line marker, and the next action ("hide"/"show").
 *
 * Also owns:
 *   - `fetchLaserLines` (GET /api/laser-lines) — refreshes the doc + snap-coeffs.
 *   - `coeffsFromLaserLinesDoc` — picks the snap line out of the doc (declared `snap_line_id`
 *     if enabled, else first enabled line) and returns `(a, b)` for the legacy snap formula.
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { twoPointsToLineModel } from '../geometry/lines.js';
import { refreshAlignmentIntersectionCache } from '../canvas/alignment-snap.js';

let _render = () => {};

/**
 * @param {{ render: () => void }} deps
 */
export function initLaserLinesPanelDeps(deps) {
    if (deps && typeof deps.render === 'function') _render = deps.render;
}

function _laserLineAttrEscape(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;');
}

/**
 * Derive `{a, b}` for the snap line (legacy `x = a*y + b` form) from the document.
 * Falls back to no-op coefficients with `loaded: false` if the doc has no usable line.
 */
export function coeffsFromLaserLinesDoc(doc) {
    if (!doc || !Array.isArray(doc.lines)) {
        return { a: 0, b: 0, source: 'none', loaded: false };
    }
    const snapId = doc.snap_line_id;
    const byId = {};
    doc.lines.forEach((ln) => {
        if (ln && ln.id) byId[ln.id] = ln;
    });
    let chosen = null;
    if (snapId && byId[snapId] && byId[snapId].enabled !== false) {
        chosen = byId[snapId];
    }
    if (!chosen) {
        chosen = doc.lines.find((l) => l && l.enabled !== false && l.p1 && l.p2) || null;
    }
    if (!chosen || !chosen.p1 || !chosen.p2) {
        return { a: 0, b: 0, source: 'schema', loaded: false, lab_mode: doc.lab_mode };
    }
    const m = twoPointsToLineModel(chosen.p1, chosen.p2);
    if (!m) {
        return { a: 0, b: 0, source: 'schema', loaded: false, lab_mode: doc.lab_mode };
    }
    if (m.kind === 'ab') {
        return {
            a: m.a,
            b: m.b,
            source: 'schema',
            loaded: true,
            snap_line_id: chosen.id,
            lab_mode: doc.lab_mode,
        };
    }
    if (m.kind === 'vertical') {
        return {
            a: 0,
            b: m.x0,
            source: 'schema',
            loaded: true,
            snap_line_id: chosen.id,
            lab_mode: doc.lab_mode,
        };
    }
    return { a: 0, b: 0, source: 'schema', loaded: false, lab_mode: doc.lab_mode };
}

export async function fetchLaserLines() {
    try {
        const response = await fetch('/api/laser-lines');
        if (response.ok) {
            store.laserLinesDoc = await response.json();
            store.laserLineCoeffs = coeffsFromLaserLinesDoc(store.laserLinesDoc);
            refreshAlignmentIntersectionCache();
            renderLaserLinesPanel();
            _render();
            const n = (store.laserLinesDoc.lines || []).length;
            console.log(
                `Laser lines (${store.laserLinesDoc.lab_mode || '?'}, ${n}):`,
                store.laserLineCoeffs.loaded !== false
                    ? `snap a=${store.laserLineCoeffs.a} b=${store.laserLineCoeffs.b}`
                    : 'no snap line',
            );
        }
    } catch (e) {
        console.error('Laser lines fetch failed', e);
    }
}

export function renderLaserLinesPanel() {
    const root = document.getElementById('laser-lines-list');
    const dock = document.getElementById('laser-lines-dock');
    if (!root) return;
    const doc = store.laserLinesDoc;
    if (dock) dock.style.display = '';
    if (!doc || !Array.isArray(doc.lines) || doc.lines.length === 0) {
        root.innerHTML = '';
        return;
    }
    const snap = doc.snap_line_id;
    root.innerHTML = doc.lines
        .map((line) => {
            const id = line.id || '';
            const name = (line.name || id).replace(/</g, '\u003c');
            const en = line.enabled !== false;
            const rawC = (line.color && String(line.color).trim()) || '#ff3b3b';
            const c = /^#[0-9A-Fa-f]{3,8}$/i.test(rawC) ? rawC : '#ff3b3b';
            const idA = _laserLineAttrEscape(id);
            const isSnap = id === snap;
            // Tooltip carries name + behavior hint + snap marker (no on-screen label
            // because the dock is icons-only; hover surfaces the label cheaply).
            const tip =
                `${name}${isSnap ? ' (snap)' : ''} — click to ${en ? 'hide' : 'show'}, double-click to edit`;
            return (
                `<button type="button" class="laser-line-icon${en ? ' is-enabled' : ''}${isSnap ? ' is-snap' : ''}" ` +
                `data-line-id="${idA}" ` +
                `style="--laser-line-color:${c}" ` +
                `title="${tip}" ` +
                `aria-pressed="${en ? 'true' : 'false'}" ` +
                `aria-label="${name}${isSnap ? ' (snap line)' : ''}">` +
                `<span class="material-icons-round" aria-hidden="true">my_location</span>` +
                `</button>`
            );
        })
        .join('');
}

function closeLaserLineEditModal() {
    const m = document.getElementById('laser-line-edit-modal');
    if (m) {
        m.style.display = 'none';
        m.setAttribute('aria-hidden', 'true');
    }
    const ch = document.getElementById('laser-line-edit-confirm');
    if (ch) ch.checked = false;
}

function openLaserLineEditModal(lineId) {
    const doc = store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines)) return;
    const line = doc.lines.find((l) => l && l.id === lineId);
    if (!line || !line.p1 || !line.p2) return;
    const modal = document.getElementById('laser-line-edit-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    modal.dataset.lineId = lineId;
    const title = document.getElementById('laser-line-edit-title');
    if (title) title.textContent = `Edit line: ${line.name || lineId}`;
    const setNum = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.value = Number(v);
    };
    setNum('laser-edit-p1x', line.p1.x);
    setNum('laser-edit-p1y', line.p1.y);
    setNum('laser-edit-p2x', line.p2.x);
    setNum('laser-edit-p2y', line.p2.y);
    const ch = document.getElementById('laser-line-edit-confirm');
    if (ch) ch.checked = false;
}

async function applyLaserLineGeometryEdit() {
    const modal = document.getElementById('laser-line-edit-modal');
    const confirmEl = document.getElementById('laser-line-edit-confirm');
    const lineId = modal && modal.dataset.lineId;
    if (!lineId) return;
    if (!confirmEl || !confirmEl.checked) {
        log('Check "I confirm" to apply reference point changes.', 'warn');
        return;
    }
    const read = (id) => {
        const el = document.getElementById(id);
        return el ? parseFloat(el.value) : NaN;
    };
    const p1 = { x: read('laser-edit-p1x'), y: read('laser-edit-p1y') };
    const p2 = { x: read('laser-edit-p2x'), y: read('laser-edit-p2y') };
    if (![p1.x, p1.y, p2.x, p2.y].every(Number.isFinite)) {
        log('Enter valid numbers for all coordinates.', 'error');
        return;
    }
    try {
        const res = await fetch(`/api/laser-lines/${encodeURIComponent(lineId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ p1, p2, confirm: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        closeLaserLineEditModal();
        await fetchLaserLines();
        _render();
        log(`Laser line "${lineId}" geometry updated.`, 'info');
    } catch (e) {
        console.error(e);
        log(`Laser line update failed: ${e.message || e}`, 'error');
    }
}

async function toggleLaserLineEnabled(id, nextEnabled) {
    try {
        const res = await fetch(`/api/laser-lines/${encodeURIComponent(id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: !!nextEnabled }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        store.laserLinesDoc = data;
        store.laserLineCoeffs = coeffsFromLaserLinesDoc(store.laserLinesDoc);
        renderLaserLinesPanel();
        _render();
        log(`Laser line "${id}" ${nextEnabled ? 'shown' : 'hidden'}.`, 'info');
    } catch (err) {
        console.error(err);
        log(`Toggle failed: ${err.message || err}`, 'error');
    }
}

/**
 * Wire dock click / dblclick + modal apply / cancel handlers. Idempotent across reloads
 * only because the DOM is created once at boot; do not call twice.
 */
export function initLaserLinesPanel() {
    const list = document.getElementById('laser-lines-list');
    if (!list) return;

    // Click on icon = toggle visibility; double-click = open edit modal.
    // Browsers fire two `click` events before a `dblclick`, so we defer the
    // single-click toggle behind a short timer and cancel it if `dblclick`
    // arrives — otherwise every edit-open would also flip the visibility.
    let pendingClickTimer = null;
    const DOUBLE_CLICK_GUARD_MS = 220;

    list.addEventListener('click', (e) => {
        const icon = e.target.closest('.laser-line-icon');
        if (!icon) return;
        const id = icon.getAttribute('data-line-id');
        if (!id) return;
        const wasEnabled = icon.classList.contains('is-enabled');
        if (pendingClickTimer) {
            clearTimeout(pendingClickTimer);
            pendingClickTimer = null;
            return;
        }
        pendingClickTimer = setTimeout(() => {
            pendingClickTimer = null;
            toggleLaserLineEnabled(id, !wasEnabled);
        }, DOUBLE_CLICK_GUARD_MS);
    });

    list.addEventListener('dblclick', (e) => {
        const icon = e.target.closest('.laser-line-icon');
        if (!icon) return;
        if (pendingClickTimer) {
            clearTimeout(pendingClickTimer);
            pendingClickTimer = null;
        }
        const id = icon.getAttribute('data-line-id');
        if (id) openLaserLineEditModal(id);
    });

    const cancel = document.getElementById('laser-line-edit-cancel');
    const apply = document.getElementById('laser-line-edit-apply');
    const modal = document.getElementById('laser-line-edit-modal');
    if (cancel) cancel.addEventListener('click', () => closeLaserLineEditModal());
    if (apply) apply.addEventListener('click', () => applyLaserLineGeometryEdit());
    if (modal) {
        modal.addEventListener('click', (ev) => {
            if (ev.target === modal) closeLaserLineEditModal();
        });
    }
    renderLaserLinesPanel();
}

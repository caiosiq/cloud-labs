/**
 * Boot gate: choose a lab backend before any Twin UI loads.
 *
 * Flow: GET /api/backends → user picks a ready backend → load lab-layout → import app-main.
 */
import { applyLabLayoutFromApiDoc } from './config.js';
import {
    fetchBackends,
    setSelectedBackendId,
    withBackendQuery,
} from './state/backend-selection.js';
import { store } from './state/store.js';

function showGate(backends) {
    return new Promise((resolve) => {
        document.body.querySelectorAll('#backend-boot-gate, #boot-error').forEach((el) => el.remove());

        const overlay = document.createElement('div');
        overlay.id = 'backend-boot-gate';
        overlay.style.cssText = [
            'position:fixed',
            'inset:0',
            'z-index:10000',
            'display:flex',
            'align-items:center',
            'justify-content:center',
            'background:radial-gradient(ellipse at 30% 20%, #1e293b 0%, #0b1220 55%, #020617 100%)',
            'font-family:ui-sans-serif,system-ui,sans-serif',
            'color:#e2e8f0',
            'padding:24px',
        ].join(';');

        const cards = backends
            .map((b) => {
                const ready = b.availability === 'ready';
                const busy = ready
                    ? (b.active_job_id
                        ? `Job running: ${String(b.active_job_id).slice(0, 16)}…`
                        : b.queued_jobs
                          ? `${b.queued_jobs} job(s) queued`
                          : b.session_lease
                            ? `Lease held by ${b.session_lease.holder}`
                            : 'Idle — available')
                    : (b.availability === 'error'
                        ? 'Host failed to initialize'
                        : 'Unavailable on this server');
                const repos = (b.control_repos || []).join(', ') || 'no local repos yet';
                const detail = b.unavailable_reason || b.init_error || '';
                const reason = detail
                    ? `<p style="margin:8px 0 0;font-size: var(--text-sm);color:#fca5a5;line-height:1.4">${escapeHtml(detail)}</p>`
                    : '';
                return `
                <button type="button"
                    data-backend-id="${escapeHtml(b.backend_id)}"
                    ${ready ? '' : 'disabled'}
                    style="
                        text-align:left;width:100%;max-width:420px;padding:18px 20px;margin:0 0 12px;
                        border-radius:12px;border:1px solid ${ready ? 'rgba(56,189,248,0.35)' : 'rgba(148,163,184,0.2)'};
                        background:${ready ? 'rgba(15,23,42,0.9)' : 'rgba(15,23,42,0.45)'};
                        color:#e2e8f0;cursor:${ready ? 'pointer' : 'not-allowed'};opacity:${ready ? '1' : '0.55'};
                    ">
                    <div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline">
                        <strong style="font-size:16px">${escapeHtml(b.label || b.backend_id)}</strong>
                        <span style="font-size: var(--text-sm);letter-spacing:0.04em;text-transform:uppercase;color:${ready ? '#67e8f9' : '#94a3b8'}">${escapeHtml(b.availability)}</span>
                    </div>
                    <code style="display:block;margin-top:6px;font-size: var(--text-sm);color:#94a3b8">${escapeHtml(b.backend_id)}</code>
                    <p style="margin:10px 0 0;font-size: var(--text-base);color:#cbd5e1;line-height:1.45">${escapeHtml(busy)}</p>
                    <p style="margin:6px 0 0;font-size: var(--text-sm);color:#94a3b8">Repos: ${escapeHtml(repos)}</p>
                    ${reason}
                </button>`;
            })
            .join('');

        overlay.innerHTML = `
            <div style="width:min(480px,100%)">
                <p style="margin:0 0 6px;font-size: var(--text-sm);letter-spacing:0.08em;text-transform:uppercase;color:#67e8f9">Cloud Labs</p>
                <h1 style="margin:0 0 8px;font-size:28px;font-weight:650;letter-spacing:-0.02em">Choose a lab backend</h1>
                <p style="margin:0 0 22px;font-size: var(--text-base);line-height:1.5;color:#94a3b8">
                    Select which communicator and control tree you want to use.
                    The twin UI will not load until you pick an available backend.
                </p>
                <div id="backend-gate-cards">${cards || '<p style="color:#fca5a5">No backends registered. Check schemas/backends.json.</p>'}</div>
            </div>`;

        document.body.appendChild(overlay);
        overlay.querySelectorAll('button[data-backend-id]:not([disabled])').forEach((btn) => {
            btn.addEventListener('click', () => {
                const id = btn.getAttribute('data-backend-id');
                overlay.remove();
                resolve(id);
            });
        });
    });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function showBootError(err) {
    document.body.insertAdjacentHTML(
        'beforeend',
        `<pre id="boot-error" style="padding:2rem;color:#fca5a5;background:#450a0a;font-family:ui-monospace,monospace;margin:2rem;border-radius:8px;position:relative;z-index:10001">`
            + `<strong>Failed to start Twin UI.</strong>\n\n${err}</pre>`,
    );
}

async function start() {
    // Always ask — do not auto-enter a backend from localStorage on cold boot.
    const backends = await fetchBackends();
    const chosen = await showGate(backends);
    setSelectedBackendId(chosen);

    const r = await fetch(withBackendQuery('/api/lab-layout'));
    if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const detail = body.detail;
        const msg =
            typeof detail === 'string'
                ? detail
                : detail
                  ? JSON.stringify(detail)
                  : `HTTP ${r.status}`;
        throw new Error(msg);
    }
    const layoutDoc = await r.json();
    applyLabLayoutFromApiDoc(layoutDoc);
    if (layoutDoc && layoutDoc.storage_grid && typeof layoutDoc.storage_grid === 'object') {
        store.storageGridSpec = layoutDoc.storage_grid;
    }
    const buildV =
        new URL(import.meta.url).searchParams.get('v') ?? String(Date.now());
    await import(`./app-main.js?v=${buildV}`);
}

start().catch((e) => {
    console.error(e);
    showBootError(e);
});

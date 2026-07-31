/**
 * Blocking "Initializing lab…" overlay over Twin canvas + sidebar controls.
 *
 * Driven by composed ``lab_initialization`` (or client-derived phase) from
 * lab-state polls. Orthogonal to lease / BUSY / OPTIMIZING.
 */

import { withBackendQuery } from '../state/backend-selection.js';
import { leaseHeaders } from '../api/session-lease.js';

const OVERLAY_ID = 'lab-init-overlay';

function _subtitleForPhase(phase) {
    switch (String(phase || '')) {
        case 'measuring_inventory':
            return 'Measuring inventory…';
        case 'waiting_for_edge':
            return 'Connecting to edge…';
        case 'failed':
            return 'Initialization failed';
        case 'missing_runtime_sync':
            return 'Waiting for edge readiness…';
        case 'ready':
            return '';
        default:
            return 'Starting…';
    }
}

function _ensureOverlay() {
    let el = document.getElementById(OVERLAY_ID);
    if (el) return el;
    el = document.createElement('div');
    el.id = OVERLAY_ID;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.style.cssText = [
        'position:absolute',
        'inset:0',
        'z-index:9000',
        'display:none',
        'align-items:center',
        'justify-content:center',
        'pointer-events:auto',
        'background:radial-gradient(ellipse at 40% 30%, rgba(30,41,59,0.92) 0%, rgba(11,18,32,0.96) 55%, rgba(2,6,23,0.98) 100%)',
        'font-family:ui-sans-serif,system-ui,sans-serif',
        'color:#e2e8f0',
        'padding:24px',
        'transition:opacity 220ms ease',
    ].join(';');
    el.innerHTML = `
      <div style="width:min(420px,100%);text-align:center">
        <p style="margin:0 0 6px;font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:#67e8f9">Cloud Labs</p>
        <h2 id="lab-init-title" style="margin:0 0 10px;font-size:26px;font-weight:650;letter-spacing:-0.02em">Initializing lab…</h2>
        <p id="lab-init-subtitle" style="margin:0;font-size:14px;line-height:1.5;color:#94a3b8"></p>
        <div id="lab-init-errors" style="display:none;margin:14px 0 0;text-align:left;font-size:12px;line-height:1.45;color:#fca5a5;max-height:120px;overflow:auto"></div>
        <button type="button" id="lab-init-retry" style="display:none;margin-top:18px;padding:10px 16px;border-radius:8px;border:1px solid rgba(56,189,248,0.4);background:rgba(15,23,42,0.9);color:#e2e8f0;cursor:pointer;font-size:13px">
          Retry SYNC_RUNTIME
        </button>
      </div>`;
    const shell = document.querySelector('.app-shell');
    if (shell) {
        if (getComputedStyle(shell).position === 'static') {
            shell.style.position = 'relative';
        }
        shell.appendChild(el);
    } else {
        document.body.appendChild(el);
        el.style.position = 'fixed';
    }
    const retry = el.querySelector('#lab-init-retry');
    if (retry) {
        retry.addEventListener('click', () => {
            void _retrySyncRuntime(retry);
        });
    }
    return el;
}

async function _retrySyncRuntime(btn) {
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Retrying…';
    }
    try {
        const headers = {
            'Content-Type': 'application/json',
            ...(leaseHeaders() || {}),
        };
        const res = await fetch(withBackendQuery('/api/command'), {
            method: 'POST',
            headers,
            body: JSON.stringify({ action: 'SYNC_RUNTIME' }),
        });
        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try {
                const body = await res.json();
                if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
            } catch {
                /* ignore */
            }
            console.warn('[lab-init] retry SYNC_RUNTIME failed', detail);
        }
    } catch (err) {
        console.warn('[lab-init] retry SYNC_RUNTIME error', err);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Retry SYNC_RUNTIME';
        }
    }
}

/**
 * @param {{ ready?: boolean, phase?: string, errors?: unknown[] } | null | undefined} init
 */
export function updateLabInitOverlay(init) {
    const el = _ensureOverlay();
    const ready = !!(init && init.ready);
    const phase = init && init.phase != null ? String(init.phase) : 'starting';
    const errors = Array.isArray(init?.errors) ? init.errors : [];

    if (ready) {
        if (el.style.display !== 'none') {
            el.style.opacity = '0';
            window.setTimeout(() => {
                el.style.display = 'none';
                el.style.opacity = '1';
            }, 220);
        }
        return;
    }

    el.style.display = 'flex';
    el.style.opacity = '1';
    const title = el.querySelector('#lab-init-title');
    const subtitle = el.querySelector('#lab-init-subtitle');
    const errBox = el.querySelector('#lab-init-errors');
    const retry = el.querySelector('#lab-init-retry');
    if (title) {
        title.textContent = phase === 'failed' ? 'Lab not ready' : 'Initializing lab…';
    }
    if (subtitle) {
        subtitle.textContent = _subtitleForPhase(phase);
    }
    if (errBox) {
        if (errors.length) {
            errBox.style.display = 'block';
            errBox.textContent = errors.map((e) => String(e)).join('\n');
        } else {
            errBox.style.display = 'none';
            errBox.textContent = '';
        }
    }
    if (retry) {
        retry.style.display = phase === 'failed' ? 'inline-block' : 'none';
    }
}

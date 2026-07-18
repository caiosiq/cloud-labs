/**
 * Live digital twin for /operations — reuses the same canvas + lab-state stack as Twin UI.
 */
import { applyLabLayoutFromApiDoc, CANVAS_WIDTH, CANVAS_HEIGHT } from '../config.js';
import { mmToPx, pxToMm } from '../canvas/coordinates.js';
import { initGuides } from '../canvas/guides.js';
import { initRender, render } from '../canvas/render.js';
import { fetchCatalogMap, fetchStorageGridSpec } from '../api/fetchers.js';
import { fetchLabState, initLabState, startLabStatePolling } from '../state/lab-state.js';
import { store } from '../state/store.js';
import { getHolding } from '../component-model.js';
import { ensureBackendSelected, setSelectedBackendId, withBackendQuery } from '../state/backend-selection.js';
import { placementUiLabel } from '../ui/context-panel.js';

function updateOperationsTwinUi() {
    if (!store.labState) return;

    const statusBadge = document.getElementById('system-status-badge');
    const status = store.labState.system_status;
    let badgeClass = 'active';
    let badgeColor = 'placed';
    let badgeStyle = '';
    let badgeSuffix = '';

    if (status === 'BUSY') {
        badgeClass = '';
        badgeColor = 'inventory';
        badgeStyle =
            'background-color: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.4);';
    } else if (status === 'OPTIMIZING') {
        badgeClass = '';
        badgeColor = 'placed';
        badgeStyle =
            'background-color: #10b981; box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);';
    } else if (status === 'HOLDING') {
        badgeClass = '';
        badgeColor = '';
        const hld = getHolding(store.labState);
        if (hld.requires_operator_confirm) {
            badgeStyle =
                'background-color: #ef4444; box-shadow: 0 0 8px rgba(239, 68, 68, 0.5);';
            badgeSuffix = ' · UNCONFIRMED';
        } else {
            badgeStyle =
                'background-color: #a855f7; box-shadow: 0 0 8px rgba(168, 85, 247, 0.45);';
            badgeSuffix = hld.tag_id ? ` · ${hld.tag_id}` : '';
        }
    }

    if (statusBadge) {
        statusBadge.className = `system-status ${badgeClass}`;
        statusBadge.innerHTML = `<span class="status-dot ${badgeColor}" style="${badgeStyle}"></span> ${status}${badgeSuffix}`;
    }

    const leaseEl = document.getElementById('twin-lease-holder');
    if (leaseEl) {
        const lease = store.labState.session_lease;
        const jobId = store.labState.active_job_id;
        const bits = [];
        if (lease?.holder) bits.push(`lease: ${lease.holder}`);
        if (jobId) bits.push(`job: ${jobId.slice(0, 14)}…`);
        leaseEl.textContent = bits.length ? bits.join(' · ') : 'No active lease';
    }

    render();
}

async function bootTwinViewer() {
    const res = await fetch('/api/backends');
    if (!res.ok) throw new Error(`/api/backends → ${res.status}`);
    const data = await res.json();
    const rows = Array.isArray(data.backends) ? data.backends : [];
    const readyRows = rows.filter((b) => b.availability === 'ready');

    let current = null;
    try {
        current = await ensureBackendSelected();
    } catch (_) {
        current = null;
    }
    const currentReady = readyRows.some((b) => b.backend_id === current);
    if (!currentReady) {
        const fallback = readyRows[0]?.backend_id;
        if (!fallback) {
            throw new Error(
                'No ready backends available. Select a working backend (e.g. mock.default) once one is registered.',
            );
        }
        setSelectedBackendId(fallback);
    }

    const layoutRes = await fetch(withBackendQuery('/api/lab-layout'));
    if (!layoutRes.ok) {
        const detail =
            layoutRes.status === 503
                ? 'backend unavailable (503) — switch to a ready backend in the left sidebar'
                : `lab-layout HTTP ${layoutRes.status}`;
        throw new Error(detail);
    }
    applyLabLayoutFromApiDoc(await layoutRes.json());

    const canvas = document.getElementById('optical-table');
    if (!canvas) {
        throw new Error('Missing #optical-table — twin viewer requires the same canvas id as Twin UI');
    }
    const ctx = canvas.getContext('2d');
    if (!initRender()) {
        throw new Error('Failed to init canvas render context');
    }

    initGuides({
        canvas,
        ctx,
        canvasWidth: CANVAS_WIDTH,
        canvasHeight: CANVAS_HEIGHT,
        mmToPx,
        pxToMm,
        render,
    });

    initLabState({
        placementUiLabel,
        updateContextPanel: () => {},
        updateMotorAngleLabels: () => {},
        updateUI: updateOperationsTwinUi,
        refreshControlWorkingState: async () => {},
    });

    await Promise.all([fetchCatalogMap(), fetchStorageGridSpec()]);
    await fetchLabState();
    startLabStatePolling();

    const shield = document.getElementById('canvas-view-shield');
    if (shield) {
        shield.hidden = false;
        shield.title = 'Read-only monitor — same view as Twin UI';
    }

    updateOperationsTwinUi();
}

bootTwinViewer().catch((err) => {
    const panel = document.getElementById('twin-boot-error');
    if (panel) {
        panel.hidden = false;
        panel.textContent = `Twin viewer failed: ${err.message || String(err)}`;
    }
    console.error('[operations-twin]', err);
});

/**
 * Read-only twin canvas for Catalog pin/node preview.
 * Same renderer as Twin UI / Operations; never mutates the bench.
 */
import { applyLabLayoutFromApiDoc, CANVAS_WIDTH, CANVAS_HEIGHT } from '../config.js';
import { mmToPx, pxToMm } from '../canvas/coordinates.js';
import { initGuides } from '../canvas/guides.js';
import { initRender, render } from '../canvas/render.js';
import { fetchCatalogMap, fetchStorageGridSpec } from '../api/fetchers.js';
import { fetchLabState, initLabState, startLabStatePolling } from '../state/lab-state.js';
import { store } from '../state/store.js';
import {
    clearPreviewOverlay,
    setPreviewOverlay,
    setViewingCommit,
} from '../control/control-state.js';
import { fetchConfigurationDocument } from '../api/control.js';
import { ensureBackendSelected, setSelectedBackendId, withBackendQuery } from '../state/backend-selection.js';
import { placementUiLabel } from '../ui/context-panel.js';

let booted = false;
let bootPromise = null;

function updateCatalogTwinUi() {
    render();
}

async function bootOnce() {
    if (booted) return;
    if (bootPromise) return bootPromise;

    bootPromise = (async () => {
        try {
            await ensureBackendSelected();
        } catch (_) {
            const res = await fetch('/api/backends');
            if (!res.ok) throw new Error(`/api/backends → ${res.status}`);
            const data = await res.json();
            const rows = Array.isArray(data.backends) ? data.backends : [];
            const ready = rows.find((b) => b.availability === 'ready') || rows[0];
            if (!ready?.backend_id) throw new Error('No backends available for twin preview');
            setSelectedBackendId(ready.backend_id);
        }

        const layoutRes = await fetch(withBackendQuery('/api/lab-layout'));
        if (!layoutRes.ok) {
            throw new Error(`lab-layout HTTP ${layoutRes.status}`);
        }
        applyLabLayoutFromApiDoc(await layoutRes.json());

        const canvas = document.getElementById('optical-table');
        if (!canvas) throw new Error('Missing #optical-table');
        if (!initRender()) throw new Error('Failed to init canvas render');

        initGuides({
            canvas,
            ctx: canvas.getContext('2d'),
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
            updateUI: updateCatalogTwinUi,
            refreshControlWorkingState: async () => {},
        });

        await Promise.all([fetchCatalogMap(), fetchStorageGridSpec()]);
        await fetchLabState();
        startLabStatePolling();

        const shield = document.getElementById('canvas-view-shield');
        if (shield) {
            shield.hidden = false;
            shield.title = 'Read-only catalog preview — not live control';
        }

        booted = true;
        updateCatalogTwinUi();
    })();

    try {
        await bootPromise;
    } catch (err) {
        bootPromise = null;
        throw err;
    }
}

/**
 * Load a control-repo commit into the read-only twin overlay.
 * @param {string} repoId
 * @param {string} configurationId
 */
export async function previewConfiguration(repoId, configurationId) {
    const errorEl = document.getElementById('catalog-twin-error');
    const emptyEl = document.getElementById('catalog-twin-empty');
    try {
        await bootOnce();
        if (errorEl) {
            errorEl.hidden = true;
            errorEl.textContent = '';
        }
        const doc = await fetchConfigurationDocument(repoId, configurationId);
        setPreviewOverlay(doc.configuration || null, doc.metadata || null);
        setViewingCommit(configurationId);
        store.control.repoId = repoId;
        if (emptyEl) emptyEl.hidden = true;
        render();
        return doc;
    } catch (err) {
        clearPreviewOverlay();
        if (errorEl) {
            errorEl.hidden = false;
            errorEl.textContent = `Twin preview failed: ${err.message || String(err)}`;
        }
        throw err;
    }
}

export function clearCatalogPreview() {
    clearPreviewOverlay();
    store.control.viewingCommitId = null;
    const emptyEl = document.getElementById('catalog-twin-empty');
    if (emptyEl) emptyEl.hidden = false;
    if (booted) render();
}

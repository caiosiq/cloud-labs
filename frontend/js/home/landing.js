/**
 * Landing atmosphere — read-only live twin behind the Cloud Labs hero.
 * On failure, CSS sheen alone keeps the page moving.
 */
import { applyLabLayoutFromApiDoc, CANVAS_WIDTH, CANVAS_HEIGHT } from '../config.js';
import { mmToPx, pxToMm } from '../canvas/coordinates.js';
import { initGuides } from '../canvas/guides.js';
import { initRender, render } from '../canvas/render.js';
import { fetchCatalogMap, fetchStorageGridSpec } from '../api/fetchers.js';
import { fetchLabState, initLabState, startLabStatePolling } from '../state/lab-state.js';
import { ensureBackendSelected, setSelectedBackendId, withBackendQuery } from '../state/backend-selection.js';
import { placementUiLabel } from '../ui/context-panel.js';

const wrap = document.getElementById('landing-canvas-wrap');
const hint = document.getElementById('landing-boot-hint');

function setHint(text) {
    if (hint) hint.textContent = text || '';
}

async function bootAtmosphere() {
    try {
        try {
            await ensureBackendSelected();
        } catch (_) {
            const res = await fetch('/api/backends');
            if (!res.ok) throw new Error(`/api/backends → ${res.status}`);
            const data = await res.json();
            const rows = Array.isArray(data.backends) ? data.backends : [];
            const ready = rows.find((b) => b.availability === 'ready') || rows[0];
            if (!ready?.backend_id) throw new Error('No backends');
            setSelectedBackendId(ready.backend_id);
        }

        const layoutRes = await fetch(withBackendQuery('/api/lab-layout'));
        if (!layoutRes.ok) throw new Error(`lab-layout ${layoutRes.status}`);
        applyLabLayoutFromApiDoc(await layoutRes.json());

        const canvas = document.getElementById('optical-table');
        if (!canvas || !initRender()) throw new Error('canvas init failed');

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
            updateUI: () => {
                render();
            },
            refreshControlWorkingState: async () => {},
        });

        await Promise.all([fetchCatalogMap(), fetchStorageGridSpec()]);
        await fetchLabState();
        startLabStatePolling();
        render();

        wrap?.classList.add('is-ready');
        setHint('Live twin · mock atmosphere');
    } catch (err) {
        console.warn('[landing] twin atmosphere unavailable', err);
        wrap?.classList.add('is-failed');
        setHint('Atmosphere offline · motion continues');
    }
}

bootAtmosphere();

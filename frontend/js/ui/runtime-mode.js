import { store } from '../state/store.js';
import { backendHeaders, withBackendQuery } from '../state/backend-selection.js';

let _deps = {
    fetchCatalogMap: async () => {},
    fetchLabState: async () => {},
    log: () => {},
    showErrorModal: () => {},
};

function detailFromResponse(data, fallback) {
    if (!data || typeof data !== 'object') return fallback;
    if (typeof data.detail === 'string') return data.detail;
    return fallback;
}

function renderRuntimeMode(info) {
    const button = document.getElementById('runtime-mode-refresh-btn');
    const status = document.getElementById('runtime-mode-status');
    if (!button) return;

    store.runtimeMode = info;

    const simulator = info.simulator || {};
    const mujocoMode = (info.available_modes || []).find((option) => option.id === 'mujoco');
    const canRefresh =
        info.active_mode === 'mujoco' ||
        Boolean(simulator.running) ||
        String(simulator.backend || '').toLowerCase().startsWith('mujoco') ||
        Boolean(mujocoMode && mujocoMode.enabled);
    button.disabled = !canRefresh;
    button.title = canRefresh
        ? 'Restart the MuJoCo viewer from the current lab state'
        : (mujocoMode && mujocoMode.reason) || 'MuJoCo is unavailable for this backend';

    if (status) {
        status.className = 'runtime-mode-status';
        if (info.active_mode === 'mujoco' && simulator.running) {
            status.classList.add('runtime-mode-status--ready');
            status.title = `MuJoCo running (PID ${simulator.pid || 'unknown'})`;
        } else if (info.active_mode === 'mujoco') {
            status.classList.add('runtime-mode-status--error');
            status.title =
                simulator.last_error || info.last_simulator_error || 'MuJoCo unavailable';
        } else if (info.active_mode === 'physical') {
            status.classList.add('runtime-mode-status--physical');
            status.title = 'Physical backend armed at server startup';
        } else {
            status.title = 'Mock UI mode';
        }
    }
}

async function fetchRuntimeMode() {
    const response = await fetch(withBackendQuery('/api/runtime-mode'), {
        headers: backendHeaders(),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(detailFromResponse(data, `HTTP ${response.status}`));
    }
    renderRuntimeMode(data);
    return data;
}

export async function refreshMujoco() {
    const button = document.getElementById('runtime-mode-refresh-btn');
    if (button) button.disabled = true;
    try {
        const response = await fetch(withBackendQuery('/api/runtime-mode/refresh-mujoco'), {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(detailFromResponse(data, `HTTP ${response.status}`));
        }
        renderRuntimeMode(data);
        store.forceGhostSync = true;
        await _deps.fetchCatalogMap();
        await _deps.fetchLabState();
        const simulator = data.simulator || {};
        _deps.log(
            `MuJoCo refreshed${simulator.pid ? ` (PID ${simulator.pid})` : ''}.`,
            'info',
        );
        return data;
    } catch (error) {
        const latest = await fetchRuntimeMode().catch(() => store.runtimeMode);
        if (latest) renderRuntimeMode(latest);
        _deps.log(`Refresh MuJoCo failed: ${error.message || error}`, 'error');
        _deps.showErrorModal(
            'Refresh MuJoCo Failed',
            error.message || String(error),
        );
        return null;
    } finally {
        if (store.runtimeMode) renderRuntimeMode(store.runtimeMode);
    }
}

export async function initRuntimeMode(deps = {}) {
    _deps = { ..._deps, ...deps };
    const button = document.getElementById('runtime-mode-refresh-btn');
    if (!button) return;
    button.addEventListener('click', () => {
        void refreshMujoco();
    });
    try {
        await fetchRuntimeMode();
    } catch (error) {
        button.disabled = true;
        _deps.log(`Runtime mode unavailable: ${error.message || error}`, 'error');
    }
}

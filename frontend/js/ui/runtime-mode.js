import { store } from '../state/store.js';

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
    const select = document.getElementById('runtime-mode-select');
    const status = document.getElementById('runtime-mode-status');
    if (!select) return;

    store.runtimeMode = info;
    select.innerHTML = '';
    (info.available_modes || []).forEach((option) => {
        const element = document.createElement('option');
        element.value = option.id;
        element.textContent = option.label;
        element.disabled = !option.enabled && option.id !== info.active_mode;
        element.title = option.reason || '';
        select.appendChild(element);
    });
    select.value = info.active_mode;
    select.disabled = Boolean(info.locked);

    const simulator = info.simulator || {};
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
    const response = await fetch('/api/runtime-mode');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(detailFromResponse(data, `HTTP ${response.status}`));
    }
    renderRuntimeMode(data);
    return data;
}

export async function switchRuntimeMode(mode) {
    const select = document.getElementById('runtime-mode-select');
    if (select) select.disabled = true;
    try {
        const response = await fetch('/api/runtime-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(detailFromResponse(data, `HTTP ${response.status}`));
        }
        renderRuntimeMode(data);
        store.forceGhostSync = true;
        await _deps.fetchCatalogMap();
        await _deps.fetchLabState();
        _deps.log(`Runtime mode changed to ${data.active_mode}.`, 'info');
        return data;
    } catch (error) {
        const latest = await fetchRuntimeMode().catch(() => store.runtimeMode);
        if (latest) renderRuntimeMode(latest);
        _deps.log(`Runtime mode switch failed: ${error.message || error}`, 'error');
        _deps.showErrorModal(
            'Runtime Mode Switch Failed',
            error.message || String(error),
        );
        return null;
    } finally {
        if (select && store.runtimeMode) {
            select.disabled = Boolean(store.runtimeMode.locked);
        }
    }
}

export async function initRuntimeMode(deps = {}) {
    _deps = { ..._deps, ...deps };
    const select = document.getElementById('runtime-mode-select');
    if (!select) return;
    select.addEventListener('change', () => {
        void switchRuntimeMode(select.value);
    });
    try {
        await fetchRuntimeMode();
    } catch (error) {
        select.disabled = true;
        _deps.log(`Runtime mode unavailable: ${error.message || error}`, 'error');
    }
}

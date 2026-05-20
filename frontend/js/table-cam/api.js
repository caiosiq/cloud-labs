/**
 * Table-cam HTTP API + payload mirroring.
 *
 * Every endpoint here returns a JSON payload that may include a `camera` or `state` block; the
 * canonical mirror is `applyTableCamServerPayload` which updates `store.tableCam*` so the rest of
 * the UI re-renders consistently from a single source of truth (the server).
 */
import { store } from '../state/store.js';
import { applyTableCamPreviewConfig } from './preview-engine.js';

export function tableCamApiErrorMessage(payload) {
    if (!payload) return 'request failed';
    if (typeof payload.detail === 'string') return payload.detail;
    if (payload.detail && typeof payload.detail === 'object') {
        return payload.detail.detail || JSON.stringify(payload.detail);
    }
    return payload.detail || payload.message || 'request failed';
}

export function applyTableCamServerPayload(data, camId) {
    if (!data || camId == null) return;
    const cam =
        data.camera ||
        (data.state &&
            data.state.cameras &&
            data.state.cameras[String(camId)]);
    if (cam) {
        store.tableCamConnected[camId] = !!cam.connected;
        store.tableCamLive[camId] = !!cam.streaming;
        store.tableCamHardware[camId] = cam.hardware || 'none';
        store.tableCamLastError[camId] = cam.last_error || null;
        if (cam.connected) {
            store.tableCamConnecting[camId] = false;
        }
    } else if (data.ok) {
        // Some endpoints don't echo full camera state — assume the requested op succeeded.
        store.tableCamConnected[camId] = true;
        store.tableCamConnecting[camId] = false;
    }
    if (data.state) {
        store.tableCamRecorderAlive = !!data.state.recorder_alive;
        store.tableCamRecorderVariant = data.state.recorder_variant || '';
    }
    const previewCfg = data.preview_config || (data.state && data.state.preview_config);
    if (previewCfg) applyTableCamPreviewConfig(previewCfg);
}

export async function apiTableCamConnect(camId) {
    const res = await fetch(`/api/table-cam/${camId}/connect`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        applyTableCamServerPayload(data, camId);
        throw new Error(tableCamApiErrorMessage(data) || 'connect failed');
    }
    applyTableCamServerPayload(data, camId);
    return data;
}

export async function apiTableCamDisconnect(camId) {
    const res = await fetch(`/api/table-cam/${camId}/disconnect`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        applyTableCamServerPayload(data, camId);
        throw new Error(tableCamApiErrorMessage(data) || 'disconnect failed');
    }
    applyTableCamServerPayload(data, camId);
    return data;
}

export async function apiTableCamLive(camId, enabled) {
    const res = await fetch(`/api/table-cam/${camId}/live`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !!enabled }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        applyTableCamServerPayload(data, camId);
        throw new Error(tableCamApiErrorMessage(data) || 'live toggle failed');
    }
    applyTableCamServerPayload(data, camId);
    return data;
}

export async function fetchTableCamStatus(camId) {
    try {
        const res = await fetch(`/api/table-cam/status?cam_id=${camId}`);
        if (!res.ok) return;
        const data = await res.json();
        applyTableCamServerPayload({ state: data, camera: data.camera }, camId);
    } catch (e) {
        console.warn('[table-cam] status fetch failed', e);
    }
}

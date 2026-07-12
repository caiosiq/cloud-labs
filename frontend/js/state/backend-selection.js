/**
 * User-selected backend (communicator) for this browser session.
 *
 * The server hosts multiple backends from schemas/backends.json; the UI and
 * scripts must pick one explicitly — it is no longer chosen via .env LAB_VIEW_PATH.
 */
import { store } from './store.js';

const STORAGE_KEY = 'cloudlabs.selectedBackendId';

export function getSelectedBackendId() {
    if (store.selectedBackendId) return store.selectedBackendId;
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
            store.selectedBackendId = saved;
            return saved;
        }
    } catch (_) {
        /* ignore */
    }
    return null;
}

export function setSelectedBackendId(backendId) {
    const id = String(backendId || '').trim();
    store.selectedBackendId = id || null;
    try {
        if (id) localStorage.setItem(STORAGE_KEY, id);
        else localStorage.removeItem(STORAGE_KEY);
    } catch (_) {
        /* ignore */
    }
}

export async function fetchBackends() {
    const res = await fetch('/api/backends');
    if (!res.ok) throw new Error(`/api/backends → ${res.status}`);
    const data = await res.json();
    store.backends = Array.isArray(data.backends) ? data.backends : [];
    return store.backends;
}

export async function ensureBackendSelected() {
    const rows = await fetchBackends();
    let current = getSelectedBackendId();
    if (current && rows.some((b) => b.backend_id === current && b.availability === 'ready')) {
        return current;
    }
    // Do not silently fall back — caller (boot gate / UI) must pick explicitly.
    if (current && rows.some((b) => b.backend_id === current)) {
        return current;
    }
    throw new Error('No backend selected. Choose one at the boot gate.');
}

export function backendQueryParam() {
    const id = getSelectedBackendId();
    return id ? `backend_id=${encodeURIComponent(id)}` : '';
}

export function withBackendQuery(url) {
    const q = backendQueryParam();
    if (!q) return url;
    return url.includes('?') ? `${url}&${q}` : `${url}?${q}`;
}

export function backendHeaders(extra = {}) {
    const id = getSelectedBackendId();
    if (!id) return extra;
    return { ...extra, 'X-CloudLabs-Backend': id };
}

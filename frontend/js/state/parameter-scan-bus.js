/**
 * Cross-tab bus for Parameter Scan session page.
 * Twin (runner) publishes; /parameter-scan-session subscribes.
 * Uses BroadcastChannel + localStorage so a late-opened tab can catch up.
 */
const CHANNEL = 'cloudlabs-parameter-scan';
const STORAGE_PREFIX = 'cloudlabs.parameterScan.';

function storageKey(scanId) {
    return `${STORAGE_PREFIX}${String(scanId || '').trim()}`;
}

/**
 * @param {string} scanId
 * @param {object} snapshot
 */
export function publishParameterScanSnapshot(scanId, snapshot) {
    const id = String(scanId || '').trim();
    if (!id || !snapshot || typeof snapshot !== 'object') return;
    const payload = {
        ...snapshot,
        scanId: id,
        updatedAt: Date.now(),
    };
    try {
        localStorage.setItem(storageKey(id), JSON.stringify(payload));
    } catch (_e) {
        /* quota / private mode */
    }
    try {
        const bc = new BroadcastChannel(CHANNEL);
        bc.postMessage(payload);
        bc.close();
    } catch (_e) {
        /* BroadcastChannel unavailable */
    }
}

/**
 * @param {string} scanId
 * @returns {object|null}
 */
export function readParameterScanSnapshot(scanId) {
    const id = String(scanId || '').trim();
    if (!id) return null;
    try {
        const raw = localStorage.getItem(storageKey(id));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (_e) {
        return null;
    }
}

/**
 * @param {string} scanId
 * @param {(snapshot: object) => void} onUpdate
 * @returns {() => void} unsubscribe
 */
export function subscribeParameterScan(scanId, onUpdate) {
    const id = String(scanId || '').trim();
    if (!id || typeof onUpdate !== 'function') return () => {};

    const handler = (ev) => {
        const data = ev?.data;
        if (!data || data.scanId !== id) return;
        onUpdate(data);
    };

    let bc = null;
    try {
        bc = new BroadcastChannel(CHANNEL);
        bc.addEventListener('message', handler);
    } catch (_e) {
        bc = null;
    }

    const onStorage = (ev) => {
        if (ev.key !== storageKey(id) || !ev.newValue) return;
        try {
            const parsed = JSON.parse(ev.newValue);
            if (parsed && parsed.scanId === id) onUpdate(parsed);
        } catch (_e) {
            /* ignore */
        }
    };
    window.addEventListener('storage', onStorage);

    const cached = readParameterScanSnapshot(id);
    if (cached) onUpdate(cached);

    return () => {
        window.removeEventListener('storage', onStorage);
        try {
            bc?.removeEventListener('message', handler);
            bc?.close();
        } catch (_e) {
            /* ignore */
        }
    };
}

export function newParameterScanId() {
    return `scan_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * @param {{ scanId?: string, backendId?: string }} [opts]
 */
export function parameterScanSessionHref(opts = {}) {
    const params = new URLSearchParams();
    if (opts.backendId) params.set('backend_id', opts.backendId);
    if (opts.scanId) params.set('scan_id', opts.scanId);
    const q = params.toString();
    return q ? `/parameter-scan-session?${q}` : '/parameter-scan-session';
}

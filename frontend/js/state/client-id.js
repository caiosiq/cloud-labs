/**
 * Stable browser client identity for multi-user coordination.
 *
 * Every Twin tab gets a UUID in localStorage. Lease holders and job
 * ``requested_by`` strings use ``ui:<client_id>`` (optionally with a role suffix).
 */
const STORAGE_KEY = 'cloudlabs.clientId';

function _newId() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID().replace(/-/g, '').slice(0, 12);
    }
    return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`.slice(0, 12);
}

/** @returns {string} Stable short client id for this browser profile. */
export function getClientId() {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved && /^[a-zA-Z0-9_-]{8,64}$/.test(saved)) return saved;
    } catch (_) {
        /* ignore */
    }
    const id = _newId();
    try {
        localStorage.setItem(STORAGE_KEY, id);
    } catch (_) {
        /* ignore */
    }
    return id;
}

/**
 * Lease / job holder label.
 * @param {string} [role] Optional role segment (e.g. ``twin``, ``optimization``).
 * @returns {string} ``ui:<clientId>`` or ``ui:<clientId>:<role>``
 */
export function getClientHolder(role) {
    const id = getClientId();
    const r = String(role || '').trim();
    return r ? `ui:${id}:${r}` : `ui:${id}`;
}

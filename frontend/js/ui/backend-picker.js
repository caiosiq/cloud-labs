/**
 * Show the active backend as a session badge (selection happens at boot gate).
 * "Switch" clears the choice and reloads so the boot gate appears again.
 */
import {
    fetchBackends,
    getSelectedBackendId,
    setSelectedBackendId,
} from '../state/backend-selection.js';

export async function refreshBackendPicker() {
    const label = document.getElementById('backend-session-label');
    if (!label) return;
    const current = getSelectedBackendId();
    if (!current) {
        label.textContent = 'No backend selected';
        return;
    }
    try {
        const rows = await fetchBackends();
        const match = rows.find((b) => b.backend_id === current);
        label.textContent = match?.label || current;
        label.title = current;
    } catch (_) {
        label.textContent = current;
        label.title = current;
    }
}

export function initBackendPicker() {
    const switchBtn = document.getElementById('backend-switch-btn');
    if (switchBtn) {
        switchBtn.addEventListener('click', () => {
            try {
                localStorage.removeItem('cloudlabs.selectedBackendId');
            } catch (_) {
                /* ignore */
            }
            setSelectedBackendId('');
            // Full reload → bootstrap.js boot gate.
            window.location.reload();
        });
    }
    void refreshBackendPicker();
}

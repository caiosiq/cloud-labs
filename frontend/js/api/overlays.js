/**
 * Apply versioned alignment overlays (guides + laser lines) to the live bench.
 * Pure state projection — no robot motion — used as the first step when
 * applying a configuration on the bench.
 */
import { backendHeaders, withBackendQuery } from '../state/backend-selection.js';

export async function applyConfigurationOverlays(config) {
    const guides = (config && config.alignment_guides) || [];
    const laserLines = (config && config.laser_lines) || { snap_line_id: null, lines: [] };
    const res = await fetch(withBackendQuery('/api/overlays'), {
        method: 'PUT',
        headers: backendHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            alignment_guides: guides,
            laser_lines: laserLines,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data && data.detail;
        const msg =
            typeof detail === 'string'
                ? detail
                : (detail && detail.message) || res.statusText || `HTTP ${res.status}`;
        throw new Error(msg);
    }
    return data;
}

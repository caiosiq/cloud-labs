/**
 * Controlled-parts API — track / untrack without placing on the table.
 */
export async function trackComponent(tagId) {
    const response = await fetch('/api/components/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: tagId }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(formatApiError(payload, response.status));
    }
    return payload;
}

export async function untrackComponent(tagId) {
    const response = await fetch('/api/components/untrack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: tagId }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(formatApiError(payload, response.status));
    }
    return payload;
}

/** Place an OFF_TABLE part on the breadboard (physical layout). */
export async function placeComponentFromInventory(tagId, { placementMode = 'breadboard' } = {}) {
    const response = await fetch('/api/components/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            tag_id: tagId,
            placement_mode: placementMode,
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(formatApiError(payload, response.status));
    }
    return payload;
}

function formatApiError(payload, status) {
    const detail = payload.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    }
    return `Request failed (${status})`;
}

/**
 * Live feed session controls (START_LIVE_FEED / END_LIVE_FEED).
 */
export async function startLiveFeed(tagId, channel = 'stream') {
    const r = await fetch(
        `/api/components/${encodeURIComponent(tagId)}/telemetry/live-feed/start?channel=${encodeURIComponent(channel)}`,
        { method: 'POST' },
    );
    if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `Live feed start failed (${r.status})`);
    }
    return r.json();
}

export async function endLiveFeed(tagId, channel = 'all') {
    const r = await fetch(
        `/api/components/${encodeURIComponent(tagId)}/telemetry/live-feed/end?channel=${encodeURIComponent(channel)}`,
        { method: 'POST' },
    );
    if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `Live feed end failed (${r.status})`);
    }
    return r.json();
}

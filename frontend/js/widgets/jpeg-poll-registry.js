/** @type {Map<string, Set<() => void>>} */
const _pollStopsByTag = new Map();

/** Stop all in-flight JPEGPoll timers for a component (e.g. on END_LIVE_FEED). */
export function stopJpegPollForTag(tagId) {
    const stops = _pollStopsByTag.get(tagId);
    if (!stops) return;
    for (const stop of stops) stop();
    _pollStopsByTag.delete(tagId);
}

/** @returns {() => void} unregister */
export function registerJpegPollStop(tagId, stop) {
    if (!_pollStopsByTag.has(tagId)) _pollStopsByTag.set(tagId, new Set());
    _pollStopsByTag.get(tagId).add(stop);
    return () => {
        stop();
        const bucket = _pollStopsByTag.get(tagId);
        if (bucket) {
            bucket.delete(stop);
            if (bucket.size === 0) _pollStopsByTag.delete(tagId);
        }
    };
}

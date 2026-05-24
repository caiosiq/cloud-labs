import { primitiveRegion, sessionStartButton, sessionEndButton, afterCommandDispatch } from './shared.js';
import { startLiveFeed, endLiveFeed } from '../api/live-feed.js';

export function renderStartLiveFeed(ctx) {
    const { tagId, hooks } = ctx;
    const channel = ctx.liveFeedChannel || 'stream';
    const { section, body } = primitiveRegion('START_LIVE_FEED', 'START LIVE FEED', { accent: 'live-feed' });
    const btn = sessionStartButton('live-feed', 'Turn live feed on', 'videocam');
    btn.onclick = () =>
        void startLiveFeed(tagId, channel)
            .then(async () => {
                await afterCommandDispatch(hooks, tagId);
            })
            .catch((e) => {
                hooks.log(`Live feed start failed: ${e.message || e}`, 'error');
            });
    body.appendChild(btn);
    return section;
}

export function renderEndLiveFeed(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('END_LIVE_FEED', 'END LIVE FEED', { accent: 'live-feed', active: true });
    const btn = sessionEndButton('live-feed', 'Turn live feed off', 'videocam_off');
    btn.onclick = () =>
        void endLiveFeed(tagId, 'all')
            .then(async () => {
                await afterCommandDispatch(hooks, tagId);
            })
            .catch((e) => {
                hooks.log(`Live feed end failed: ${e.message || e}`, 'error');
            });
    body.appendChild(btn);
    return section;
}

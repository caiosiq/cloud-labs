import { primitiveRegion, sessionStartButton, sessionEndButton, afterCommandDispatch } from './shared.js';
import { startLiveFeed, endLiveFeed } from '../api/live-feed.js';
import { openLiveFeedPopout } from '../ui/live-feed-popout.js';

function _popOutButton(tagId, hooks) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'live-feed-pop-btn';
    btn.innerHTML =
        '<span class="material-icons-round" style="font-size:14px" aria-hidden="true">open_in_new</span> Pop out live preview';
    btn.title = 'Detach preview so you can teleop other components while watching';
    btn.onclick = () => {
        openLiveFeedPopout(tagId, {
            fetchLabState: hooks?.fetchLabState,
        });
        hooks?.log?.(`Live feed pop-out opened for ${tagId}`, 'info');
    };
    return btn;
}

export function renderStartLiveFeed(ctx) {
    const { tagId, hooks } = ctx;
    const channel = ctx.liveFeedChannel || 'stream';
    const { section, body } = primitiveRegion('START_LIVE_FEED', 'START LIVE FEED', { accent: 'live-feed' });
    const btn = sessionStartButton('live-feed', 'Turn live feed on', 'videocam');
    btn.onclick = () => {
        btn.disabled = true;
        void startLiveFeed(tagId, channel)
            .then(async () => {
                await afterCommandDispatch(hooks, tagId, { fetchLabState: true });
                openLiveFeedPopout(tagId, { fetchLabState: hooks?.fetchLabState });
            })
            .catch((e) => {
                hooks.log(`Live feed start failed: ${e.message || e}`, 'error');
            })
            .finally(() => {
                btn.disabled = false;
            });
    };
    body.appendChild(btn);
    return section;
}

export function renderEndLiveFeed(ctx) {
    const { tagId, hooks } = ctx;
    const { section, body } = primitiveRegion('END_LIVE_FEED', 'END LIVE FEED', { accent: 'live-feed', active: true });
    const btn = sessionEndButton('live-feed', 'Turn live feed off', 'videocam_off');
    btn.onclick = () => {
        btn.disabled = true;
        void endLiveFeed(tagId, 'all')
            .then(async () => {
                await afterCommandDispatch(hooks, tagId, { fetchLabState: true });
            })
            .catch((e) => {
                hooks.log(`Live feed end failed: ${e.message || e}`, 'error');
            })
            .finally(() => {
                btn.disabled = false;
            });
    };
    body.appendChild(btn);
    body.appendChild(_popOutButton(tagId, hooks));
    return section;
}

import { primitiveRegion, sessionStartButton, sessionEndButton, afterCommandDispatch, coordInput } from './shared.js';
import { startLiveFeed, endLiveFeed, setLiveExposure } from '../api/live-feed.js';
import { openLiveFeedPopout } from '../ui/live-feed-popout.js';
import { normalizeCapabilities } from '../component-state.js';

function _exposureDefaults(ctx) {
    const caps = normalizeCapabilities(ctx.catalogRow?.capabilities);
    const desc = caps.statecontrol?.tunables?.exposure_time_ms || {};
    const min = Number.isFinite(Number(desc.min)) ? Number(desc.min) : 0.1;
    const max = Number.isFinite(Number(desc.max)) ? Number(desc.max) : 1000;
    const unit = desc.unit || 'ms';
    const live = ctx.comp?.telemetry?.live_feed?.stream?.live_exposure_time_ms;
    const science = ctx.comp?.statecontrol?.tunables?.exposure_time_ms;
    const cur =
        (live !== undefined && live !== null && Number(live)) ||
        (science !== undefined && science !== null && Number(science)) ||
        (Number.isFinite(Number(desc.default)) ? Number(desc.default) : 50);
    return { min, max, unit, cur };
}

/** Science cameras declare exposure / SET_LIVE_EXPOSURE; overhead table-top does not. */
function _supportsLiveExposure(ctx) {
    const caps = normalizeCapabilities(ctx.catalogRow?.capabilities);
    if (caps.statecontrol?.tunables?.exposure_time_ms) return true;
    const prims = caps.primitives || [];
    return prims.includes('SET_LIVE_EXPOSURE') || prims.includes('SET_EXPOSURE');
}

export function renderStartLiveFeed(ctx) {
    const { tagId, hooks } = ctx;
    const channel = ctx.liveFeedChannel || 'stream';
    const showExp = _supportsLiveExposure(ctx);
    const { section, body } = primitiveRegion('START_LIVE_FEED', 'START LIVE FEED', { accent: 'live-feed' });

    let inp = null;
    if (showExp) {
        const { min, max, unit, cur } = _exposureDefaults(ctx);
        const hint = document.createElement('p');
        hint.style.fontSize = '10px';
        hint.style.color = '#94a3b8';
        hint.style.margin = '0';
        hint.style.lineHeight = '1.35';
        hint.textContent =
            `Preview exposure (${min}–${max} ${unit}) applies to live video only — not science SET_EXPOSURE / RECORD.`;
        body.appendChild(hint);

        inp = coordInput(`preview exposure (${unit})`, cur);
        inp.input.min = String(min);
        inp.input.max = String(max);
        inp.input.step = '0.1';
        body.appendChild(inp);
    }

    const btn = sessionStartButton('live-feed', 'Turn live feed on', 'videocam');
    btn.onclick = () => {
        const opts = {};
        if (showExp && inp) {
            const v = parseFloat(inp.value);
            if (Number.isFinite(v) && v > 0) {
                opts.exposure_time_ms = v;
            }
        }
        btn.disabled = true;
        void startLiveFeed(tagId, channel, opts)
            .then(async () => {
                await afterCommandDispatch(hooks, tagId, { fetchLabState: true });
                openLiveFeedPopout(tagId, { fetchLabState: hooks?.fetchLabState });
            })
            .catch((e) => {
                if (String(e?.message || e) === 'cancelled') return;
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
    const showExp = _supportsLiveExposure(ctx);
    const { section, body } = primitiveRegion('END_LIVE_FEED', 'END LIVE FEED', { accent: 'live-feed', active: true });

    if (showExp) {
        const { min, max, unit, cur } = _exposureDefaults(ctx);
        const hint = document.createElement('p');
        hint.style.fontSize = '10px';
        hint.style.color = '#94a3b8';
        hint.style.margin = '0';
        hint.style.lineHeight = '1.35';
        hint.textContent =
            'Adjust preview exposure while live (VEXP). Science SET_EXPOSURE is separate.';
        body.appendChild(hint);

        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.gap = '6px';
        row.style.alignItems = 'flex-end';

        const inp = coordInput(`preview (${unit})`, cur);
        inp.input.min = String(min);
        inp.input.max = String(max);
        inp.input.step = '0.1';
        inp.style.flex = '1';
        row.appendChild(inp);

        const applyBtn = document.createElement('button');
        applyBtn.type = 'button';
        applyBtn.className = 'btn btn-secondary';
        applyBtn.style.fontSize = '11px';
        applyBtn.style.whiteSpace = 'nowrap';
        applyBtn.textContent = 'Apply';
        applyBtn.onclick = () => {
            const v = parseFloat(inp.value);
            if (!Number.isFinite(v) || v <= 0) {
                hooks.log('Invalid preview exposure (need a positive number).', 'error');
                return;
            }
            applyBtn.disabled = true;
            void setLiveExposure(tagId, v)
                .then(async () => {
                    await afterCommandDispatch(hooks, tagId, { fetchLabState: true });
                    hooks.log?.(`Live preview exposure → ${v} ms`, 'info');
                })
                .catch((e) => {
                    if (String(e?.message || e) === 'cancelled') return;
                    hooks.log(`Live exposure failed: ${e.message || e}`, 'error');
                })
                .finally(() => {
                    applyBtn.disabled = false;
                });
        };
        row.appendChild(applyBtn);
        body.appendChild(row);
    }

    const btn = sessionEndButton('live-feed', 'Turn live feed off', 'videocam_off');
    btn.onclick = () => {
        btn.disabled = true;
        void endLiveFeed(tagId, 'all')
            .then(async () => {
                await afterCommandDispatch(hooks, tagId, { fetchLabState: true });
            })
            .catch((e) => {
                if (String(e?.message || e) === 'cancelled') return;
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

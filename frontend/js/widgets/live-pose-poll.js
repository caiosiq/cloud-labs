/**
 * `LivePosePoll` — telemetry widget for TeleOp hardware pose (§14.3).
 *
 * Displays ``current_pose`` (``getTeleopCurrentPose``) updated by the session-wide
 * poll in ``teleop-session.js`` / ``api/teleop-live-pose.js``. Repaints
 * while mounted so the readout tracks high-rate updates without rebuilding
 * the whole context panel.
 */
import { widgetCard, widgetTitle, row, fmtNum, nullPlaceholder, resolveTokens } from './common.js';
import { isTeleopReady } from '../component-state.js';
import { store } from '../state/store.js';
import { getTeleopCurrentPose } from '../teleop-pose.js';

function paintPose(card, tagId, descriptor) {
    const body = card.querySelector('[data-live-pose-body]');
    if (!body) return;
    body.replaceChildren();

    const comp = store.labState?.components?.[tagId];
    if (!isTeleopReady(comp)) {
        body.appendChild(nullPlaceholder('\u2014 teleop inactive (start session in PRIMITIVES)'));
        return;
    }

    const pose = getTeleopCurrentPose(tagId);
    if (!pose || typeof pose !== 'object' || !Number.isFinite(Number(pose.x))) {
        body.appendChild(nullPlaceholder('\u2014 waiting for live pose\u2026'));
        return;
    }

    body.appendChild(row('x (mm)', fmtNum(pose.x, 1)));
    body.appendChild(row('y (mm)', fmtNum(pose.y, 1)));
    body.appendChild(row('rotation (°)', fmtNum(pose.rotation || 0, 1)));
    if (Number.isFinite(Number(pose.z))) {
        body.appendChild(row('z (mm)', fmtNum(pose.z, 1)));
    }

    const meta = document.createElement('div');
    meta.style.color = '#475569';
    meta.style.fontSize = '9px';
    meta.style.marginTop = '4px';
    meta.style.fontFamily = 'ui-monospace, monospace';
    const url = resolveTokens(descriptor && descriptor.url, { tagId });
    const fps = Number(descriptor && descriptor.default_fps) > 0
        ? Number(descriptor.default_fps)
        : 20;
    meta.textContent = `${url || 'live-pose'}  \u2022  ${fps} fps`;
    body.appendChild(meta);
}

export default function LivePosePoll({ tagId, fieldName, descriptor }) {
    const card = widgetCard();
    card.dataset.livePosePoll = tagId;
    card.appendChild(widgetTitle(fieldName, descriptor));

    const body = document.createElement('div');
    body.dataset.livePoseBody = '1';
    card.appendChild(body);

    paintPose(card, tagId, descriptor);

    const intervalMs = 100;
    let timer = setInterval(() => paintPose(card, tagId, descriptor), intervalMs);
    function stop() {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    }
    const obs = new MutationObserver(() => {
        if (!card.isConnected) {
            stop();
            obs.disconnect();
        }
    });
    setTimeout(() => {
        if (card.parentNode) obs.observe(card.parentNode, { childList: true, subtree: true });
    }, 0);

    return card;
}

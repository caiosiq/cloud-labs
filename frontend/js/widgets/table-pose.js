/**
 * `TablePose` — tunable widget for ``nominal_pose`` (§14.1).
 *
 * **Pose surface #3 (read-only):** displays committed tunable intent from
 * statecontrol. Editing is via canvas ghost drag (surface #1) or context
 * X/Y/Rot inputs (surface #2) — see `component-model.js`.
 */
import { widgetCard, widgetTitle, row, fmtPoseMm } from './common.js';

export default function TablePose({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));
    const pose = value || {};
    const hasXY = Number.isFinite(Number(pose.x)) && Number.isFinite(Number(pose.y));
    if (!hasXY) {
        const el = document.createElement('div');
        el.style.fontStyle = 'italic';
        el.style.color = '#475569';
        el.style.fontSize = '11px';
        el.textContent = '\u2014 no pose committed (component is in inventory or storage)';
        card.appendChild(el);
        return card;
    }
    card.appendChild(row('x (mm)', fmtPoseMm(pose.x)));
    card.appendChild(row('y (mm)', fmtPoseMm(pose.y)));
    card.appendChild(row('rotation (\u00b0)', fmtPoseMm(pose.rotation || 0)));
    return card;
}

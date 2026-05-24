/**
 * `PoseReadout` — measurable widget (§14.2).
 *
 * Read-only encoder confirmation from ``measurables.pose``.
 * May be null during motion per the Golden Rule.
 */
import { widgetCard, widgetTitle, row, fmtNum, nullPlaceholder } from './common.js';

export default function PoseReadout({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const pose = value && typeof value === 'object' ? value : null;
    const hasXY = pose && Number.isFinite(Number(pose.x)) && Number.isFinite(Number(pose.y));
    if (!hasXY) {
        card.appendChild(nullPlaceholder('\u2014 null (in motion / not yet recorded)'));
        return card;
    }

    card.appendChild(row('x (mm)', fmtNum(pose.x, 1)));
    card.appendChild(row('y (mm)', fmtNum(pose.y, 1)));
    card.appendChild(row('rotation (°)', fmtNum(pose.rotation || 0, 1)));
    if (Number.isFinite(Number(pose.z))) {
        card.appendChild(row('z (mm)', fmtNum(pose.z, 1)));
    }
    return card;
}

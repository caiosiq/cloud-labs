/**
 * `PoseReadout` — measurable widget (§14.2).
 *
 * Read-only encoder confirmation from ``measurables.pose``.
 * May be null during motion per the Golden Rule.
 */
import { widgetCard, widgetTitle, row, fmtPoseMm, nullPlaceholder } from './common.js';

export default function PoseReadout({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const pose = value && typeof value === 'object' ? value : null;
    const hasXY = pose && Number.isFinite(Number(pose.x)) && Number.isFinite(Number(pose.y));
    if (!hasXY) {
        card.appendChild(nullPlaceholder('\u2014 null (in motion / not yet recorded)'));
        return card;
    }

    card.appendChild(row('x (mm)', fmtPoseMm(pose.x)));
    card.appendChild(row('y (mm)', fmtPoseMm(pose.y)));
    card.appendChild(row('rotation (°)', fmtPoseMm(pose.rotation || 0)));
    if (Number.isFinite(Number(pose.z))) {
        card.appendChild(row('z (mm)', fmtPoseMm(pose.z)));
    }
    return card;
}

/**
 * `PoseReadout` — read-only pose display.
 *
 * Prefer ``tunables.reported_pose`` (bench report of the pose tunable).
 * Legacy ``measurables.pose`` may still supply the value during migration.
 * May be null during motion.
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

    card.appendChild(row('x (mm)', fmtNum(pose.x, 2)));
    card.appendChild(row('y (mm)', fmtNum(pose.y, 2)));
    card.appendChild(row('rotation (°)', fmtNum(pose.rotation || 0, 2)));
    if (Number.isFinite(Number(pose.z))) {
        card.appendChild(row('z (mm)', fmtNum(pose.z, 1)));
    }
    return card;
}

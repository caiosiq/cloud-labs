/**
 * `MotorRotationsReadout` — measurable widget (§14.2).
 *
 * Read-only display of confirmed motor angles. Source is the lab's
 * ``measurables.motor_rotations`` dict (encoder readback), which goes
 * **null** during motion per Phase 3's Golden Rule and is repopulated
 * by ``RECORD_MEASURABLES`` or the next successful primitive commit.
 */
import { widgetCard, widgetTitle, fmtNum, nullPlaceholder } from './common.js';

export default function MotorRotationsReadout({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    if (!value || typeof value !== 'object' || !Object.keys(value).length) {
        card.appendChild(nullPlaceholder('\u2014 null (in motion / not yet recorded)'));
        return card;
    }

    Object.entries(value).forEach(([mid, ang]) => {
        const r = document.createElement('div');
        r.style.display = 'flex';
        r.style.justifyContent = 'space-between';
        r.style.alignItems = 'baseline';
        r.style.gap = '8px';
        const k = document.createElement('span');
        k.style.color = '#64748b';
        k.style.fontSize = '10px';
        k.textContent = `\u03b8_${mid}`;
        const v = document.createElement('span');
        v.style.color = '#cbd5e1';
        v.style.fontFamily = 'ui-monospace, monospace';
        v.style.fontSize = '11px';
        v.textContent = `${fmtNum(ang, 2)}\u00b0`;
        r.appendChild(k);
        r.appendChild(v);
        card.appendChild(r);
    });
    return card;
}

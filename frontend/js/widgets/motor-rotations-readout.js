/**
 * `MotorRotationsReadout` — optional readout for motor angle maps.
 *
 * Prefer binding this to ``tunables.nominal_motor_positions`` (lab observe
 * recalculates that same tunable). Motor angles are not a measurable.
 */
import { widgetCard, widgetTitle, fmtNum, nullPlaceholder } from './common.js';

export default function MotorRotationsReadout({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    if (!value || typeof value !== 'object' || !Object.keys(value).length) {
        card.appendChild(nullPlaceholder('\u2014 no motor angles yet'));
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
        k.style.fontSize = 'var(--text-xs)';
        k.textContent = `\u03b8_${mid}`;
        const v = document.createElement('span');
        v.style.color = '#cbd5e1';
        v.style.fontFamily = 'ui-monospace, monospace';
        v.style.fontSize = 'var(--text-sm)';
        v.textContent = `${fmtNum(ang, 2)}\u00b0`;
        r.appendChild(k);
        r.appendChild(v);
        card.appendChild(r);
    });
    return card;
}

/**
 * `FloatRange` — tunable widget for bounded floats (§14.1).
 *
 * Read-only display of current value, catalog bounds, and a range bar.
 * Writes go through the matching write primitive (e.g. SET_EXPOSURE).
 */
import { widgetCard, widgetTitle, row, fmtNum } from './common.js';

export default function FloatRange({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const min = Number(descriptor && descriptor.min);
    const max = Number(descriptor && descriptor.max);
    const unit = (descriptor && descriptor.unit) || '';
    const def = descriptor && descriptor.default;

    const cur = Number.isFinite(Number(value))
        ? Number(value)
        : Number.isFinite(Number(def))
            ? Number(def)
            : null;

    if (cur === null) {
        const el = document.createElement('div');
        el.style.fontStyle = 'italic';
        el.style.color = '#475569';
        el.style.fontSize = '11px';
        el.textContent = '\u2014 no value set';
        card.appendChild(el);
        return card;
    }

    card.appendChild(row('value', `${fmtNum(cur, 2)}${unit ? ` ${unit}` : ''}`));
    if (Number.isFinite(min) && Number.isFinite(max) && max > min) {
        card.appendChild(row('range', `${fmtNum(min, 2)}\u2013${fmtNum(max, 2)}${unit ? ` ${unit}` : ''}`));
        const pct = Math.max(0, Math.min(1, (cur - min) / (max - min)));
        const barWrap = document.createElement('div');
        barWrap.style.marginTop = '6px';
        barWrap.title = 'Read-only — use SET EXPOSURE in PRIMITIVES to change';
        const track = document.createElement('div');
        track.style.height = '6px';
        track.style.background = '#1e293b';
        track.style.borderRadius = '3px';
        track.style.overflow = 'hidden';
        const fill = document.createElement('div');
        fill.style.height = '100%';
        fill.style.width = `${(pct * 100).toFixed(1)}%`;
        fill.style.background = 'linear-gradient(90deg, #0e7490, #22d3ee)';
        fill.style.borderRadius = '3px';
        fill.style.pointerEvents = 'none';
        track.appendChild(fill);
        barWrap.appendChild(track);
        card.appendChild(barWrap);
    }
    return card;
}

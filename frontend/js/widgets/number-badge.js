/**
 * `NumberBadge` — measurable widget (§14.2).
 *
 * Formatted scalar display. ``descriptor.format`` accepts printf-style
 * strings like ``.3f`` or ``d``. Used today for
 * ``last_optimization_score`` on motorized components.
 */
import { widgetCard, widgetTitle, fmtByDescriptor, nullPlaceholder } from './common.js';

export default function NumberBadge({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        card.appendChild(nullPlaceholder('\u2014 null'));
        return card;
    }

    const badge = document.createElement('div');
    badge.style.fontFamily = 'ui-monospace, monospace';
    badge.style.fontSize = '20px';
    badge.style.color = '#22d3ee';
    badge.style.textAlign = 'center';
    badge.style.padding = '4px 0';
    badge.textContent = fmtByDescriptor(value, descriptor && descriptor.format);
    card.appendChild(badge);
    return card;
}

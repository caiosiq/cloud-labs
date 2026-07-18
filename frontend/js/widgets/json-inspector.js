/**
 * `JsonInspector` — universal fallback widget (§14.4).
 *
 * Used whenever the catalog references a widget name the running build
 * doesn't ship. Renders the raw value as formatted JSON so the operator
 * can still see what the lab reported / accepts, even when the
 * presentation layer is missing.
 */
import { widgetCard, widgetTitle, nullPlaceholder } from './common.js';

export default function JsonInspector({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));
    if (value === null || value === undefined) {
        card.appendChild(nullPlaceholder('\u2014 null'));
        return card;
    }
    const pre = document.createElement('pre');
    pre.style.margin = '0';
    pre.style.color = '#cbd5e1';
    pre.style.fontFamily = 'ui-monospace, monospace';
    pre.style.fontSize = '10px';
    pre.style.whiteSpace = 'pre-wrap';
    pre.style.wordBreak = 'break-word';
    try {
        pre.textContent = JSON.stringify(value, null, 2);
    } catch (_e) {
        pre.textContent = String(value);
    }
    card.appendChild(pre);
    return card;
}

/**
 * `StorageSlot` — tunable widget for ``storage`` intent (§14.1).
 *
 * Read-only display of inventory slot bookkeeping:
 * ``{ in_storage, slot: { i, j } }``. Explicit cell pick uses canvas
 * STORE choose-slot mode; autopack leaves slot assignment to the edge.
 */
import { widgetCard, widgetTitle, row, nullPlaceholder } from './common.js';

export default function StorageSlot({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));

    const storage = value && typeof value === 'object' ? value : null;
    if (!storage) {
        card.appendChild(nullPlaceholder('\u2014 no storage intent'));
        return card;
    }

    card.appendChild(row('in_storage', storage.in_storage ? 'true' : 'false'));
    const slot = storage.slot;
    if (slot && typeof slot === 'object' && Number.isFinite(Number(slot.i)) && Number.isFinite(Number(slot.j))) {
        card.appendChild(row('slot', `(${Number(slot.i)}, ${Number(slot.j)})`));
    } else {
        card.appendChild(row('slot', '\u2014 unassigned', { dim: true }));
    }
    return card;
}

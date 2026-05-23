/**
 * Read-only per-component capability viewer (TUNABLES / MEASURABLES / TELEMETRY).
 *
 * Writes go through primitive UI regions in ``component-popup.js`` /
 * ``frontend/js/primitives/``.
 */
import { store } from '../state/store.js';
import { getWidget } from '../widgets/index.js';

/** Interactive tunable widgets — shown only under primitive regions, not here. */
const INTERACTIVE_TUNABLE_WIDGETS = new Set(['NudgeMotorGroup', 'TeleopJog']);

function valueForField(comp, scope, fieldName) {
    if (!comp || typeof comp !== 'object') return null;
    if (scope === 'tunables') {
        const t = comp.tunables || {};
        return t[fieldName];
    }
    if (scope === 'measurables') {
        const m = comp.measurables || {};
        if (fieldName in m && m[fieldName] != null) return m[fieldName];
        // Motor angles are merged under measurables.pose by the backend.
        if (fieldName === 'motor_rotations') {
            const pose = m.pose;
            return (pose && pose.motor_rotations) || null;
        }
        return m[fieldName];
    }
    return null;
}

function sectionTitle(text, count) {
    const el = document.createElement('div');
    el.className = 'component-panel__section-title';
    el.style.fontSize = '10px';
    el.style.color = '#94a3b8';
    el.style.fontWeight = '600';
    el.style.letterSpacing = '0.05em';
    el.style.marginBottom = '6px';
    el.style.marginTop = '4px';
    el.style.display = 'flex';
    el.style.justifyContent = 'space-between';
    el.style.alignItems = 'baseline';
    el.style.paddingBottom = '4px';
    el.style.borderBottom = '1px solid #2a2e36';
    const name = document.createElement('span');
    name.textContent = text;
    el.appendChild(name);
    if (typeof count === 'number') {
        const c = document.createElement('span');
        c.style.color = '#475569';
        c.style.fontFamily = 'ui-monospace, monospace';
        c.style.fontWeight = '400';
        c.textContent = `${count}`;
        el.appendChild(c);
    }
    return el;
}

function renderEmptyHint(text) {
    const el = document.createElement('div');
    el.style.fontStyle = 'italic';
    el.style.color = '#475569';
    el.style.fontSize = '10px';
    el.style.padding = '4px 0';
    el.textContent = text;
    return el;
}

function renderScope(scope, declaration, comp, tagId, hooks, { skipInteractive = false } = {}) {
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.gap = '6px';
    const fields = Object.entries(declaration || {});
    if (!fields.length) {
        wrap.appendChild(renderEmptyHint(`\u2014 no ${scope} declared`));
        return wrap;
    }
    fields.forEach(([fieldName, descriptor]) => {
        const widgetName = (descriptor && descriptor.widget) || 'JsonInspector';
        if (skipInteractive && scope === 'tunables' && INTERACTIVE_TUNABLE_WIDGETS.has(widgetName)) {
            return;
        }
        const widget = getWidget(widgetName);
        const value = valueForField(comp, scope, fieldName);
        let card;
        try {
            card = widget({
                tagId,
                fieldName,
                descriptor: descriptor || {},
                value,
                scope,
                comp,
                hooks,
            });
        } catch (e) {
            console.error(`[component-viewer] widget ${widgetName} for ${scope}.${fieldName} threw:`, e);
            card = document.createElement('div');
            card.style.color = '#fca5a5';
            card.style.fontSize = '10px';
            card.textContent = `${scope}.${fieldName}: widget ${widgetName} failed (${e && e.message ? e.message : e})`;
        }
        if (card instanceof HTMLElement) wrap.appendChild(card);
    });
    if (!wrap.childElementCount) {
        wrap.appendChild(renderEmptyHint(`\u2014 no read-only ${scope} fields`));
    }
    return wrap;
}

/**
 * Read-only TUNABLES / MEASURABLES / TELEMETRY for one component.
 *
 * @param {string} tagId
 * @param {object|null} comp
 * @param {object} catalogRow
 * @returns {HTMLElement}
 */
export function renderReadOnlyPanel(tagId, comp, catalogRow) {
    const root = document.createElement('div');
    root.className = 'component-panel component-panel--readonly';
    root.style.display = 'flex';
    root.style.flexDirection = 'column';
    root.style.gap = '12px';

    const caps = catalogRow?.capabilities;
    if (!caps || typeof caps !== 'object') {
        root.appendChild(renderEmptyHint(`\u2014 ${tagId} has no capabilities block.`));
        return root;
    }

    const hooks = {};

    const tunablesSection = document.createElement('div');
    tunablesSection.className = 'component-panel__block';
    const tunableFields = Object.entries(caps.tunables || {}).filter(
        ([, d]) => !INTERACTIVE_TUNABLE_WIDGETS.has((d && d.widget) || ''),
    );
    tunablesSection.appendChild(sectionTitle('TUNABLES', tunableFields.length));
    tunablesSection.appendChild(
        renderScope('tunables', caps.tunables, comp, tagId, hooks, { skipInteractive: true }),
    );
    root.appendChild(tunablesSection);

    const measurablesSection = document.createElement('div');
    measurablesSection.className = 'component-panel__block';
    measurablesSection.appendChild(
        sectionTitle('MEASURABLES', Object.keys(caps.measurables || {}).length),
    );
    measurablesSection.appendChild(
        renderScope('measurables', caps.measurables, comp, tagId, hooks),
    );
    root.appendChild(measurablesSection);

    const telemetrySection = document.createElement('div');
    telemetrySection.className = 'component-panel__block';
    telemetrySection.appendChild(
        sectionTitle('TELEMETRY', Object.keys(caps.telemetry || {}).length),
    );
    telemetrySection.appendChild(
        renderScope('telemetry', caps.telemetry, comp, tagId, hooks),
    );
    root.appendChild(telemetrySection);

    return root;
}

/** @deprecated Use ``renderComponentPopup`` from ``component-popup.js``. */
export function renderComponentPanel(tagId) {
    const catalogRow = (store.catalogMap || {})[tagId];
    const comp =
        (store.labState && store.labState.components && store.labState.components[tagId]) ||
        null;
    return renderReadOnlyPanel(tagId, comp, catalogRow || {});
}

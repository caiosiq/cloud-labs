/**
 * Read-only per-component capability viewer (STATE CONTROL / TELEMETRY).
 */
import { getStatecontrol, getTelemetry, isLiveFeedActive, measurableValue, normalizeCapabilities, tunableValue } from '../component-state.js';
import { inAirDisplayPose, isHeldTag } from '../component-model.js';
import { store } from '../state/store.js';
import { getWidget } from '../widgets/index.js';

const LIVE_VIEW_WIDGETS = new Set(['MJPEGViewer', 'JPEGPoll']);
const TELEOP_CONTROL_WIDGETS = new Set(['TeleopRz', 'TeleopPose3d']);
const TELEOP_READOUT_WIDGETS = new Set(['LivePosePoll']);

function valueForField(comp, scope, fieldName, tagId) {
    if (
        scope === 'tunables'
        && fieldName === 'nominal_pose'
        && tagId
        && isHeldTag(tagId, store.labState)
    ) {
        return inAirDisplayPose(tagId, store.labState, comp);
    }
    if (scope === 'tunables') return tunableValue(comp, fieldName);
    if (scope === 'measurables') return measurableValue(comp, fieldName);
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

function renderScope(scope, declaration, comp, tagId, hooks, { gateLiveFeed = false } = {}) {
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
        if (scope === 'live_feed' && gateLiveFeed && !isLiveFeedActive(comp, fieldName)) {
            wrap.appendChild(renderEmptyHint(`\u2014 turn Live feed on to view ${fieldName}`));
            return;
        }
        const widget = getWidget(widgetName);
        const value = valueForField(comp, scope === 'live_feed' ? 'measurables' : scope, fieldName, tagId);
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
            card.textContent = `${scope}.${fieldName}: widget failed (${e && e.message ? e.message : e})`;
        }
        if (card instanceof HTMLElement) wrap.appendChild(card);
    });
    if (!wrap.childElementCount) {
        wrap.appendChild(renderEmptyHint(`\u2014 no read-only ${scope} fields`));
    }
    return wrap;
}

function renderTelemetryReadOnly(telDecl, comp, tagId, hooks) {
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.gap = '8px';

    const telemetry = getTelemetry(comp);
    const hasTeleop = Object.keys(telDecl.teleop || {}).length > 0;
    const hasLiveFeed = Object.keys(telDecl.live_feed || {}).length > 0;

    if (hasTeleop) {
        const block = document.createElement('div');
        block.appendChild(sectionTitle('TeleOp session', 1));
        const inspector = getWidget('JsonInspector');
        block.appendChild(
            inspector({
                tagId,
                fieldName: 'teleop',
                descriptor: { widget: 'JsonInspector' },
                value: telemetry.teleop || { active: false },
                scope: 'telemetry',
                comp,
                hooks,
            }),
        );

        Object.entries(telDecl.teleop || {}).forEach(([fieldName, descriptor]) => {
            if (fieldName === 'teleop') return;
            const widgetName = (descriptor && descriptor.widget) || 'JsonInspector';
            if (TELEOP_CONTROL_WIDGETS.has(widgetName)) {
                block.appendChild(renderEmptyHint(
                    `\u2014 use PRIMITIVES \u2192 TELEOP while session is active for ${fieldName}`,
                ));
                return;
            }
            if (TELEOP_READOUT_WIDGETS.has(widgetName)) {
                const widget = getWidget(widgetName);
                block.appendChild(
                    widget({
                        tagId,
                        fieldName,
                        descriptor: descriptor || {},
                        value: null,
                        scope: 'telemetry',
                        comp,
                        hooks,
                    }),
                );
                return;
            }
            block.appendChild(renderEmptyHint(`\u2014 teleop.${fieldName} (${widgetName})`));
        });
        wrap.appendChild(block);
    }

    if (hasLiveFeed) {
        const block = document.createElement('div');
        block.style.marginTop = hasTeleop ? '8px' : '0';
        block.appendChild(sectionTitle('Live feed', Object.keys(telDecl.live_feed).length));
        Object.entries(telDecl.live_feed).forEach(([fieldName, descriptor]) => {
            const channelState = (telemetry.live_feed || {})[fieldName] ?? null;
            const inspector = getWidget('JsonInspector');
            block.appendChild(
                inspector({
                    tagId,
                    fieldName,
                    descriptor: { widget: 'JsonInspector', label: `${fieldName} state` },
                    value: channelState,
                    scope: 'telemetry',
                    comp,
                    hooks,
                }),
            );

            const widgetName = (descriptor && descriptor.widget) || 'JsonInspector';
            if (LIVE_VIEW_WIDGETS.has(widgetName)) {
                if (isLiveFeedActive(comp, fieldName)) {
                    const viewWidget = getWidget(widgetName);
                    block.appendChild(
                        viewWidget({
                            tagId,
                            fieldName,
                            descriptor: descriptor || {},
                            value: channelState,
                            scope: 'live_feed',
                            comp,
                            hooks,
                        }),
                    );
                } else {
                    block.appendChild(
                        renderEmptyHint(
                            `\u2014 turn Live feed on in PRIMITIVES to view ${fieldName}`,
                        ),
                    );
                }
            }
        });
        wrap.appendChild(block);
    }

    if (!hasTeleop && !hasLiveFeed) {
        wrap.appendChild(renderEmptyHint('\u2014 no telemetry declared'));
    }
    return wrap;
}

/**
 * @param {string} tagId
 * @param {object|null} comp
 * @param {object} catalogRow
 * @param {object} [hooks]
 */
export function renderReadOnlyPanel(tagId, comp, catalogRow, hooks = {}) {
    const root = document.createElement('div');
    root.className = 'component-readonly-panel';
    root.style.display = 'flex';
    root.style.flexDirection = 'column';
    root.style.gap = '12px';

    const caps = normalizeCapabilities(catalogRow?.capabilities);
    if (!catalogRow?.capabilities) {
        root.appendChild(renderEmptyHint(`\u2014 ${tagId} has no capabilities block.`));
        return root;
    }

    const sc = caps.statecontrol || { tunables: {}, measurables: {} };
    const tel = caps.telemetry || { teleop: {}, live_feed: {} };

    const stateSection = document.createElement('div');
    stateSection.className = 'component-panel__block';
    stateSection.appendChild(sectionTitle('STATE CONTROL'));
    const tunBlock = document.createElement('div');
    tunBlock.appendChild(sectionTitle('Tunables', Object.keys(sc.tunables || {}).length));
    tunBlock.appendChild(renderScope('tunables', sc.tunables, comp, tagId, hooks));
    stateSection.appendChild(tunBlock);
    const measBlock = document.createElement('div');
    measBlock.style.marginTop = '8px';
    measBlock.appendChild(sectionTitle('Measurables', Object.keys(sc.measurables || {}).length));
    measBlock.appendChild(renderScope('measurables', sc.measurables, comp, tagId, hooks));
    stateSection.appendChild(measBlock);
    root.appendChild(stateSection);

    const telemetrySection = document.createElement('div');
    telemetrySection.className = 'component-panel__block';
    telemetrySection.appendChild(sectionTitle('TELEMETRY'));
    telemetrySection.appendChild(renderTelemetryReadOnly(tel, comp, tagId, hooks));
    root.appendChild(telemetrySection);

    return root;
}

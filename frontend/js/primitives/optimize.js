import { store } from '../state/store.js';
import { primitiveRegion, secondaryButton } from './shared.js';

export function renderOptimize(ctx) {
    const { tagId, placementState } = ctx;
    if (placementState === 'STORED') return null;
    const strategies = store.availableStrategies;
    if (!strategies || !Object.keys(strategies).length) return null;

    const { section, body } = primitiveRegion('OPTIMIZE', 'OPTIMIZE');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#b45309';
    hint.style.margin = '0 0 6px 0';
    hint.style.lineHeight = '1.35';
    hint.innerHTML =
        '<strong>Deprecated.</strong> Prefer right sidebar <em>Optimization → Alignment session</em> ' +
        '(ensemble / jobs). Legacy NEWTON/COBYLA kept for real-bench telemetry parity.';
    body.appendChild(hint);

    Object.entries(strategies).forEach(([stratKey, strat]) => {
        if (strat && strat.unimplemented) return;
        const label = strat.deprecated
            ? `${strat.name || stratKey} (legacy)`
            : strat.name || stratKey;
        const btn = secondaryButton(label, 'settings_suggest');
        btn.onclick = () => {
            if (typeof ctx.showParameterModal === 'function') {
                ctx.showParameterModal(stratKey, strat);
            }
        };
        body.appendChild(btn);
    });
    return section;
}

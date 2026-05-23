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
    hint.style.color = '#94a3b8';
    hint.style.margin = '0 0 4px 0';
    hint.textContent = 'Runs optimization for this component (strategy-specific parameters).';
    body.appendChild(hint);

    Object.entries(strategies).forEach(([stratKey, strat]) => {
        const btn = secondaryButton(strat.name || stratKey, 'settings_suggest');
        btn.onclick = () => {
            if (typeof ctx.showParameterModal === 'function') {
                ctx.showParameterModal(stratKey, strat);
            }
        };
        body.appendChild(btn);
    });
    return section;
}

import { coordInput, dispatchPrimitive, primitiveRegion, runButton } from './shared.js';
import { tunableValue, normalizeCapabilities } from '../component-state.js';

export function renderSetExposure(ctx) {
    const { tagId, comp, catalogRow, hooks } = ctx;
    const caps = normalizeCapabilities(catalogRow?.capabilities);
    const desc = caps.statecontrol?.tunables?.exposure_time_ms || {};
    const min = Number.isFinite(Number(desc.min)) ? Number(desc.min) : 10;
    const max = Number.isFinite(Number(desc.max)) ? Number(desc.max) : 1000;
    const unit = desc.unit || 'ms';

    const stored = tunableValue(comp, 'exposure_time_ms');
    const cur =
        (stored !== undefined && stored !== null && Number(stored)) ||
        (Number.isFinite(Number(desc.default)) ? Number(desc.default) : 200);

    const { section, body } = primitiveRegion('SET_EXPOSURE', 'SET EXPOSURE');
    const hint = document.createElement('p');
    hint.style.fontSize = '10px';
    hint.style.color = '#94a3b8';
    hint.style.margin = '0';
    hint.style.lineHeight = '1.35';
    hint.textContent =
        `Science capture exposure (${min}–${max} ${unit}) for RECORD / OPTIMIZE. ` +
        `Live preview brightness is separate (Start live feed / pop-out).`;
    body.appendChild(hint);

    const inp = coordInput(`exposure (${unit})`, cur);
    inp.min = String(min);
    inp.max = String(max);
    inp.step = '1';
    body.appendChild(inp);

    const btn = runButton('Set exposure', 'tune');
    btn.onclick = async () => {
        const v = parseFloat(inp.value);
        if (!Number.isFinite(v) || v <= 0) {
            hooks.log('Invalid exposure (need a positive number).', 'error');
            return;
        }
        await dispatchPrimitive(hooks, {
            action: 'SET_EXPOSURE',
            target_id: tagId,
            parameters: { exposure_time_ms: v },
        });
    };
    body.appendChild(btn);
    return section;
}

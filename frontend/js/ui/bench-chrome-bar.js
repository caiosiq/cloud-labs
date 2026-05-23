/**
 * Chrome bar above the optical table canvas: fixed bench components
 * (tunables but no TablePose — table-top camera, future laser sources).
 */
import { store } from '../state/store.js';
import {
    getCatalogRow,
    isChromeComponent,
    listChromeComponentTags,
} from '../component-model.js';
import { getComponentIcon } from './icons.js';

let _onSelect = () => {};

/**
 * @param {{ onSelect: (tagId: string) => void }} deps
 */
export function initBenchChromeBar(deps) {
    if (deps && typeof deps.onSelect === 'function') _onSelect = deps.onSelect;
}

export function refreshBenchChromeBar() {
    const bar = document.getElementById('bench-chrome-bar');
    if (!bar) return;

    const tags = listChromeComponentTags(store.labState);
    bar.innerHTML = '';
    if (!tags.length) {
        bar.hidden = true;
        return;
    }
    bar.hidden = false;

    const label = document.createElement('span');
    label.className = 'bench-chrome-bar__label';
    label.textContent = 'Bench';
    bar.appendChild(label);

    tags.forEach((tagId) => {
        const comp = store.labState.components[tagId];
        const row = getCatalogRow(tagId);
        const displayName = (row && row.name) || tagId;
        const icon = getComponentIcon(comp && comp.type);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'bench-chrome-bar__btn';
        if (tagId === store.selectedComponent) btn.classList.add('bench-chrome-bar__btn--active');
        btn.title = `${displayName} (${tagId}) — fixed bench component`;
        btn.innerHTML = `
            <span class="material-icons-round bench-chrome-bar__icon">${icon}</span>
            <span class="bench-chrome-bar__name">${displayName}</span>
        `;
        btn.addEventListener('click', () => {
            store.selectedComponent = tagId;
            _onSelect(tagId);
        });
        bar.appendChild(btn);
    });
}

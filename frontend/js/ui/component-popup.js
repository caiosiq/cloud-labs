/**
 * Component popup: read-only capability panels + per-primitive action regions.
 */
import { store } from '../state/store.js';
import { measPose } from '../component-model.js';
import { sendCommand } from '../api/commands.js';
import { fetchLabState } from '../state/lab-state.js';
import { log } from './log.js';
import { renderReadOnlyPanel } from './component-viewer.js';
import { renderPrimitiveRegions } from '../primitives/index.js';

/**
 * @param {string} tagId
 * @param {{
 *   checkCollision?: Function,
 *   render?: Function,
 *   updateContextPanel?: Function,
 *   showParameterModal?: Function,
 * }} deps
 */
export function renderComponentPopup(tagId, deps = {}) {
    const root = document.createElement('div');
    root.className = 'component-popup';
    root.style.display = 'flex';
    root.style.flexDirection = 'column';
    root.style.gap = '12px';

    const catalogRow = (store.catalogMap || {})[tagId];
    if (!catalogRow) {
        root.textContent = `Catalog not loaded for ${tagId}.`;
        return root;
    }

    const comp =
        (store.labState && store.labState.components && store.labState.components[tagId]) ||
        null;
    const caps = catalogRow.capabilities;

    root.appendChild(renderReadOnlyPanel(tagId, comp, catalogRow));

    if (caps && Array.isArray(caps.primitives) && caps.primitives.length) {
        const primBlock = document.createElement('div');
        primBlock.className = 'component-popup__primitives';
        const title = document.createElement('div');
        title.style.fontSize = '10px';
        title.style.color = '#94a3b8';
        title.style.fontWeight = '600';
        title.style.letterSpacing = '0.05em';
        title.style.paddingBottom = '4px';
        title.style.borderBottom = '1px solid #2a2e36';
        title.textContent = 'PRIMITIVES';
        primBlock.appendChild(title);

        const ctx = {
            tagId,
            comp,
            catalogRow,
            placementState: deps.placementState,
            getPose: () => {
                const ghost = store.ghostState && store.ghostState[tagId];
                if (ghost && Number.isFinite(ghost.x)) return ghost;
                return measPose(comp) || {};
            },
            hooks: {
                sendCommand,
                fetchLabState,
                log,
                refreshPanel: deps.updateContextPanel
                    ? (tid) => deps.updateContextPanel(tid)
                    : undefined,
            },
            checkCollision: deps.checkCollision,
            render: deps.render,
            updateContextPanel: deps.updateContextPanel,
            showParameterModal: deps.showParameterModal,
        };

        primBlock.appendChild(renderPrimitiveRegions(tagId, caps.primitives, ctx));
        root.appendChild(primBlock);
    }

    return root;
}

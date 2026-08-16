/**
 * Per-tag component panel template.
 *
 * Returns a complete ``.component-panel[data-tag-id=...]`` element for the
 * given tag. The panel-dock manager (``ui/context-panel.js``) mounts these
 * inside ``#panel-dock`` — one panel per open component. Multiple panels can
 * be open at once (Ctrl/Cmd+click). The focused panel is full; companions use
 * ``renderCompactComponentPanel``.
 *
 * The element exposes:
 *   - ``data-tag-id="<tagId>"`` on the root, so lab-state.js can find it.
 *
 * Replaces the legacy combination of static ``#context-panel`` markup +
 * dynamic ``renderComponentPopup`` body — header, read-only state /
 * telemetry, and primitives all live inside this template.
 */
import { store } from '../state/store.js';
import { measPose, isChromeComponent, isStoredComponent } from '../component-model.js';
import { sendCommand } from '../api/commands.js';
import { fetchLabState } from '../state/lab-state.js';
import { log } from './log.js';
import { updateMotorAngleLabels, placementUiLabel } from './context-panel.js';
import { renderReadOnlyPanel } from './component-viewer.js';
import { renderPrimitiveRegions } from '../primitives/index.js';
import { renderOptimizationVariablePicker, isOptimizationVariableStage } from './optimization-mode.js';

const PRIMITIVE_DEV_HINTS =
    typeof window !== 'undefined' &&
    typeof window.location !== 'undefined' &&
    /(?:^|[?&])dev=1(?:&|$)/.test(window.location.search || '');

/**
 * Build the full per-tag panel element.
 *
 * @param {string} tagId
 * @param {{
 *   checkCollision?: Function,
 *   render?: Function,
 *   updateContextPanel?: Function,
 *   placementState?: string,
 *   closePanel?: (tagId: string) => void,
 * }} deps
 * @returns {HTMLDivElement}
 */
export function renderComponentPanel(tagId, deps = {}) {
    const panel = document.createElement('div');
    panel.className = 'component-panel';
    panel.dataset.tagId = tagId;
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-label', `Component controls for ${tagId}`);

    const catalogRow = (store.catalogMap || {})[tagId];
    const comp =
        (store.labState && store.labState.components && store.labState.components[tagId]) ||
        null;

    if (!catalogRow) {
        panel.appendChild(_renderHeader(tagId, { name: tagId, properties: {}, hint: '' }, deps));
        const missing = document.createElement('div');
        missing.style.padding = '10px 0';
        missing.style.fontSize = '11px';
        missing.style.color = '#94a3b8';
        missing.textContent = `Catalog not loaded for ${tagId}.`;
        panel.appendChild(missing);
        return panel;
    }

    const displayName = catalogRow.name || tagId;
    const properties = catalogRow.properties || {};
    const chromeOnly = isChromeComponent(tagId);
    const placement = deps.placementState || placementUiLabel(comp);
    const isStored = comp ? isStoredComponent(comp) : false;

    const hint = chromeOnly
        ? 'Fixed bench component — not placed on the table. Use the chrome bar above the canvas for quick access.'
        : isStored
          ? 'Stored in Q3 at <strong>cell center</strong> and <strong>0°</strong> by default. Use <strong>Drag from storage</strong> or set X/Y/Rot and <strong>Place from storage</strong>.'
          : '';

    panel.appendChild(
        _renderHeader(tagId, { name: displayName, properties, hint }, deps),
    );

    panel.appendChild(_renderBody(tagId, comp, catalogRow, { ...deps, placementState: placement }));

    return panel;
}

/**
 * Compact companion card for an open-but-unfocused panel in the Selected Part dock.
 * Click focuses (expands to full); X closes.
 *
 * @param {string} tagId
 * @param {{
 *   placementState?: string,
 *   closePanel?: (tagId: string) => void,
 *   focusPanel?: (tagId: string) => void,
 * }} deps
 * @returns {HTMLDivElement}
 */
export function renderCompactComponentPanel(tagId, deps = {}) {
    const panel = document.createElement('div');
    panel.className = 'component-panel component-panel--compact';
    panel.dataset.tagId = tagId;
    panel.setAttribute('role', 'button');
    panel.setAttribute('tabindex', '0');
    panel.setAttribute('aria-label', `Focus panel for ${tagId}`);
    panel.title = 'Click to expand · Ctrl+click another part to add a companion';

    const catalogRow = (store.catalogMap || {})[tagId];
    const comp =
        (store.labState && store.labState.components && store.labState.components[tagId]) ||
        null;
    const displayName = (catalogRow && catalogRow.name) || tagId;
    const placement = deps.placementState || placementUiLabel(comp);

    const row = document.createElement('div');
    row.className = 'component-panel__compact-row';

    const text = document.createElement('div');
    text.className = 'component-panel__compact-text';
    const nameEl = document.createElement('div');
    nameEl.className = 'component-panel__compact-name';
    nameEl.textContent = displayName;
    const meta = document.createElement('div');
    meta.className = 'component-panel__compact-meta';
    meta.textContent = `${tagId} · ${placement}`;
    text.appendChild(nameEl);
    text.appendChild(meta);
    row.appendChild(text);

    const badge = document.createElement('span');
    badge.className = 'component-panel__compact-badge';
    badge.textContent = placement;
    row.appendChild(badge);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'component-panel__close';
    close.title = 'Close';
    close.setAttribute('aria-label', `Close panel for ${tagId}`);
    close.innerHTML = '<span class="material-icons-round" aria-hidden="true">close</span>';
    close.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (typeof deps.closePanel === 'function') deps.closePanel(tagId);
    });
    row.appendChild(close);

    panel.appendChild(row);

    const hint = document.createElement('div');
    hint.className = 'component-panel__compact-hint';
    hint.textContent = 'Click to expand';
    panel.appendChild(hint);

    panel.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            if (typeof deps.focusPanel === 'function') deps.focusPanel(tagId);
        }
    });

    return panel;
}

/**
 * Backwards-compatible alias. Older callers imported ``renderComponentPopup``
 * expecting just the "body" subtree; everything now goes through
 * ``renderComponentPanel`` which returns the full panel.
 *
 * @deprecated use ``renderComponentPanel`` instead.
 */
export const renderComponentPopup = renderComponentPanel;

// ---------- private helpers ----------

function _renderHeader(tagId, { name, properties, hint }, deps) {
    const header = document.createElement('div');
    header.className = 'component-panel__header';
    header.style.display = 'flex';
    header.style.alignItems = 'flex-start';
    header.style.justifyContent = 'space-between';
    header.style.gap = '10px';
    header.style.padding = '0 0 12px 0';
    header.style.borderBottom = '1px solid var(--border-color)';

    const info = document.createElement('div');
    info.style.flex = '1';
    info.style.minWidth = '0';

    const eyebrow = document.createElement('div');
    eyebrow.style.fontSize = '10px';
    eyebrow.style.color = 'var(--text-muted)';
    eyebrow.style.letterSpacing = '0.06em';
    eyebrow.style.textTransform = 'uppercase';
    eyebrow.style.marginBottom = '4px';
    eyebrow.textContent = 'Selected part';
    info.appendChild(eyebrow);

    const title = document.createElement('div');
    const titleSpan = document.createElement('span');
    titleSpan.style.color = 'var(--primary-accent)';
    titleSpan.textContent = name;
    title.appendChild(titleSpan);
    info.appendChild(title);

    const tagRow = document.createElement('div');
    tagRow.style.marginTop = '4px';
    tagRow.style.fontSize = '10px';
    tagRow.style.color = 'var(--text-muted)';
    tagRow.style.letterSpacing = '0.04em';
    const tagLabel = document.createElement('span');
    tagLabel.style.textTransform = 'uppercase';
    tagLabel.textContent = 'Tag ID:';
    const tagValue = document.createElement('span');
    tagValue.style.color = 'var(--text-secondary)';
    tagValue.style.fontFamily = "'JetBrains Mono', 'Fira Code', monospace";
    tagValue.style.marginLeft = '4px';
    tagValue.textContent = tagId;
    tagRow.appendChild(tagLabel);
    tagRow.appendChild(tagValue);
    info.appendChild(tagRow);

    const propsBox = document.createElement('div');
    propsBox.style.marginTop = '6px';
    propsBox.style.fontSize = '11px';
    propsBox.style.fontStyle = 'italic';
    propsBox.style.color = 'var(--text-muted)';
    propsBox.style.fontWeight = '400';
    propsBox.style.textTransform = 'none';
    propsBox.style.lineHeight = '1.4';
    if (properties && Object.keys(properties).length > 0) {
        const propsHtml = Object.entries(properties)
            .map(([key, val]) => {
                const cleanKey = key
                    .replace(/_/g, ' ')
                    .replace(/^\w/, (c) => c.toUpperCase());
                return `<div style="margin-bottom: 2px;">${cleanKey}: <span style="color: #e2e8f0;">${val}</span></div>`;
            })
            .join('');
        propsBox.innerHTML = propsHtml;
    }
    if (hint) {
        const hintEl = document.createElement('div');
        hintEl.style.marginTop = '8px';
        hintEl.style.fontSize = '10px';
        hintEl.style.color = '#94a3b8';
        hintEl.style.lineHeight = '1.35';
        hintEl.innerHTML = hint;
        propsBox.appendChild(hintEl);
    }
    info.appendChild(propsBox);

    header.appendChild(info);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'component-panel__close';
    close.title = 'Close';
    close.setAttribute('aria-label', `Close panel for ${tagId}`);
    close.innerHTML = '<span class="material-icons-round" aria-hidden="true">close</span>';
    close.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (typeof deps.closePanel === 'function') {
            deps.closePanel(tagId);
        }
    });
    header.appendChild(close);

    return header;
}

function _renderBody(tagId, comp, catalogRow, deps) {
    const body = document.createElement('div');
    body.className = 'component-panel__body';
    body.dataset.section = 'record-slot';

    const wrap = document.createElement('div');
    wrap.style.marginTop = '12px';

    const isStored = comp ? isStoredComponent(comp) : false;

    // Actions first: primitives sit above read-only tunables / measurables / telemetry.
    const caps = catalogRow.capabilities;
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
        title.textContent = isStored ? 'STORAGE' : 'PRIMITIVES';
        primBlock.appendChild(title);

        const ctx = {
            tagId,
            comp,
            catalogRow,
            labState: store.labState,
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
                updateMotorAngleLabels,
                refreshPanel: deps.updateContextPanel
                    ? (tid) => deps.updateContextPanel(tid)
                    : undefined,
                resetPanelSnapshot: () => {
                    store.contextPanelSnapshots.delete(tagId);
                },
                render: deps.render,
            },
            checkCollision: deps.checkCollision,
            render: deps.render,
            updateContextPanel: deps.updateContextPanel,
        };

        primBlock.appendChild(renderPrimitiveRegions(tagId, caps.primitives, ctx));
        wrap.appendChild(primBlock);
    }

    if (isOptimizationVariableStage()) {
        const optSlot = document.createElement('div');
        optSlot.className = 'component-popup__optimization-vars';
        optSlot.style.marginTop = wrap.childElementCount ? '12px' : '0';
        renderOptimizationVariablePicker(optSlot, tagId, comp);
        wrap.appendChild(optSlot);
    }

    if (!isStored) {
        const readonly = document.createElement('div');
        readonly.style.marginTop = wrap.childElementCount ? '12px' : '0';
        readonly.appendChild(
            renderReadOnlyPanel(tagId, comp, catalogRow, {
                sendCommand,
                fetchLabState,
                updateMotorAngleLabels,
                refreshPanel: deps.updateContextPanel
                    ? (tid) => deps.updateContextPanel(tid || tagId)
                    : undefined,
                resetPanelSnapshot: () => {
                    store.contextPanelSnapshots.delete(tagId);
                },
                render: deps.render,
                log,
            }),
        );
        wrap.appendChild(readonly);
    }

    if (PRIMITIVE_DEV_HINTS) {
        const dev = document.createElement('div');
        dev.style.fontSize = '9px';
        dev.style.color = '#475569';
        dev.style.marginTop = '6px';
        dev.innerHTML =
            'Dev: <code>POST /api/components/{tag}/measurables/record</code>';
        wrap.appendChild(dev);
    }

    body.appendChild(wrap);
    return body;
}

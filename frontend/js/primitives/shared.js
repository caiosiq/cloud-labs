/**
 * Shared chrome for per-primitive UI regions in the component popup.
 */
import { syncTeleopLivePosePolls } from '../teleop-session.js';

export function primitiveRegion(primitiveId, title, opts = {}) {
    const section = document.createElement('div');
    section.className = 'prim-region';
    section.dataset.primitive = primitiveId;
    if (opts.accent === 'teleop') section.classList.add('prim-region--teleop');
    if (opts.accent === 'live-feed') section.classList.add('prim-region--live-feed');
    if (opts.active) section.classList.add('prim-region--session-active');
    section.style.marginTop = '10px';
    section.style.paddingTop = '10px';
    section.style.borderTop = '1px solid #2a2e36';

    const header = document.createElement('div');
    header.className = 'prim-region__title';
    header.style.fontSize = 'var(--text-xs)';
    header.style.color = '#94a3b8';
    header.style.fontWeight = '600';
    header.style.marginBottom = '6px';
    header.textContent = title;
    section.appendChild(header);

    const body = document.createElement('div');
    body.className = 'prim-region__body';
    body.style.display = 'flex';
    body.style.flexDirection = 'column';
    body.style.gap = '6px';
    section.appendChild(body);

    return { section, body };
}

export function runButton(label, iconName) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-primary';
    btn.style.fontSize = 'var(--text-sm)';
    btn.style.width = '100%';
    if (iconName) {
        btn.innerHTML =
            `<span class="material-icons-round" style="font-size: var(--text-base);vertical-align:middle;">${iconName}</span> ${label}`;
    } else {
        btn.textContent = label;
    }
    return btn;
}

/** Accent start button for telemetry sessions (TeleOp / live feed). */
export function sessionStartButton(kind, label, iconName) {
    const btn = runButton(label, iconName);
    btn.classList.remove('btn-primary');
    btn.classList.add(kind === 'live-feed' ? 'btn-live-feed' : 'btn-teleop');
    return btn;
}

/** Accent end button shown while a telemetry session is active. */
export function sessionEndButton(kind, label, iconName) {
    const btn = secondaryButton(label, iconName);
    btn.classList.add(kind === 'live-feed' ? 'btn-live-feed-end' : 'btn-teleop-end');
    return btn;
}

export function sessionHint(text) {
    const el = document.createElement('div');
    el.className = 'prim-session-hint';
    el.textContent = text;
    return el;
}

export function secondaryButton(label, iconName) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-secondary';
    btn.style.fontSize = 'var(--text-sm)';
    btn.style.width = '100%';
    if (iconName) {
        btn.innerHTML =
            `<span class="material-icons-round" style="font-size: var(--text-base);vertical-align:middle;">${iconName}</span> ${label}`;
    } else {
        btn.textContent = label;
    }
    return btn;
}

/**
 * Labeled numeric field for pose / setpoint editors.
 * Returns a ``<label>`` wrapper; ``.value`` proxies to the inner input so
 * callers can keep using ``parseFloat(field.value)``.
 *
 * @param {string} label Always-visible caption (placeholders hide once filled).
 * @param {unknown} [value]
 * @param {number} [digits] When set, initial display uses fixed decimal places.
 * @returns {HTMLLabelElement & { value: string, input: HTMLInputElement }}
 */
export function coordInput(label, value, digits) {
    const field = document.createElement('label');
    field.className = 'coord-field';

    const caption = document.createElement('span');
    caption.className = 'coord-field__label';
    caption.textContent = label;

    const inp = document.createElement('input');
    inp.type = 'number';
    inp.className = 'coord-input';
    inp.placeholder = label;
    inp.setAttribute('aria-label', label);
    if (value !== undefined && value !== null && Number.isFinite(Number(value))) {
        inp.value = Number.isFinite(Number(digits))
            ? Number(value).toFixed(Number(digits))
            : String(value);
    }

    field.appendChild(caption);
    field.appendChild(inp);
    Object.defineProperty(field, 'value', {
        get() {
            return inp.value;
        },
        set(v) {
            inp.value = v == null ? '' : String(v);
        },
        enumerable: true,
    });
    field.input = inp;
    return field;
}

export async function afterCommandDispatch(hooks, targetId, opts = {}) {
    const refreshPanel = opts.refreshPanel !== false;
    const fetchState = opts.fetchLabState !== false;
    if (fetchState && typeof hooks.fetchLabState === 'function') {
        try {
            await hooks.fetchLabState();
        } catch (_e) {
            /* poll will catch up */
        }
    }
    if (refreshPanel && typeof hooks.refreshPanel === 'function' && targetId) {
        // Force rebuild — telemetry session edges are not always picked up by
        // componentDataSnapshot diff alone when poll races the POST handler.
        if (typeof hooks.resetPanelSnapshot === 'function') {
            hooks.resetPanelSnapshot();
        }
        syncTeleopLivePosePolls();
        hooks.refreshPanel(targetId);
        if (typeof hooks.render === 'function') {
            hooks.render();
        }
    }
}

/**
 * Send a primitive command and optionally refresh lab state / panel.
 *
 * @param {object} hooks
 * @param {object} command
 * @param {{ refreshPanel?: boolean, fetchLabState?: boolean, onSuccess?: Function, onError?: Function }} [opts]
 * @returns {Promise<{ ok?: boolean, error?: string }|undefined>}
 */
export async function dispatchPrimitive(hooks, command, opts = {}) {
    if (!hooks || typeof hooks.sendCommand !== 'function') {
        return { ok: false, error: 'sendCommand unavailable' };
    }
    const result = await hooks.sendCommand(command);
    if (!result?.ok) {
        if (typeof opts.onError === 'function') opts.onError(result);
        return result;
    }
    await afterCommandDispatch(hooks, command && command.target_id, opts);
    if (typeof opts.onSuccess === 'function') opts.onSuccess(result);
    return result;
}

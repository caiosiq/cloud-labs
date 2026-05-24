/**
 * Shared chrome for per-primitive UI regions in the component popup.
 */

export function primitiveRegion(primitiveId, title) {
    const section = document.createElement('div');
    section.className = 'prim-region';
    section.dataset.primitive = primitiveId;
    section.style.marginTop = '10px';
    section.style.paddingTop = '10px';
    section.style.borderTop = '1px solid #2a2e36';

    const header = document.createElement('div');
    header.style.fontSize = '10px';
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
    btn.style.fontSize = '11px';
    btn.style.width = '100%';
    if (iconName) {
        btn.innerHTML =
            `<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">${iconName}</span> ${label}`;
    } else {
        btn.textContent = label;
    }
    return btn;
}

export function secondaryButton(label, iconName) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-secondary';
    btn.style.fontSize = '11px';
    btn.style.width = '100%';
    if (iconName) {
        btn.innerHTML =
            `<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">${iconName}</span> ${label}`;
    } else {
        btn.textContent = label;
    }
    return btn;
}

export function coordInput(placeholder, value) {
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.className = 'coord-input';
    inp.placeholder = placeholder;
    if (value !== undefined && value !== null && Number.isFinite(Number(value))) {
        inp.value = String(value);
    }
    return inp;
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
        hooks.refreshPanel(targetId);
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

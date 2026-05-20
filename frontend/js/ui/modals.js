/**
 * Generic full-screen modal overlays (error + confirm + strategy-parameter form).
 *
 * `showParameterModal` is the only modal here that touches `store` / `log` / `sendCommand` —
 * it builds the strategy-parameter form (e.g. for COBYLA) and dispatches the `OPTIMIZE` command
 * on submit.
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { sendCommand } from '../api/commands.js';

/**
 * Blocking error banner with a "Retry Connection" button that reloads the page.
 * No-ops if `#error-modal` is already on screen.
 * @param {string} title
 * @param {string} message HTML allowed (inlined into innerHTML).
 */
export function showErrorModal(title, message) {
    if (document.getElementById('error-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'error-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100vw';
    overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #ef4444';
    card.style.borderRadius = '8px';
    card.style.padding = '32px';
    card.style.width = '450px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 48px; color: #ef4444; margin-bottom: 16px;">report_problem</span>
        <h2 style="margin: 0 0 12px 0; color: #e2e8f0; font-size: 20px;">${title}</h2>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5;">${message}</p>
        <button onclick="location.reload()" class="btn btn-primary" style="background-color: #ef4444; width: auto; margin: 0 auto; padding: 10px 24px;">
            <span class="material-icons-round">refresh</span> Retry Connection
        </button>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

/**
 * Modal with Cancel / Confirm; runs the appropriate callback on click and removes itself.
 * No-ops if `#confirm-modal` is already on screen.
 * @param {string} message HTML allowed.
 * @param {() => void} [onConfirm]
 * @param {() => void} [onCancel]
 */
export function showConfirmationModal(message, onConfirm, onCancel) {
    if (document.getElementById('confirm-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'confirm-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100vw';
    overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #3b82f6';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '400px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 40px; color: #3b82f6; margin-bottom: 12px;">help_outline</span>
        <h3 style="margin: 0 0 12px 0; color: #e2e8f0;">Confirm Action</h3>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5;">${message}</p>
        <div style="display: flex; justify-content: center; gap: 12px;">
            <button id="confirm-no" class="btn btn-secondary" style="width: auto; padding: 8px 20px;">Cancel</button>
            <button id="confirm-yes" class="btn btn-primary" style="width: auto; padding: 8px 20px;">Confirm</button>
        </div>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    document.getElementById('confirm-no').onclick = () => {
        overlay.remove();
        if (onCancel) onCancel();
    };

    document.getElementById('confirm-yes').onclick = () => {
        overlay.remove();
        if (onConfirm) onConfirm();
    };
}

/**
 * Build a parameter form from `strategyDef.parameters` and dispatch an `OPTIMIZE` command on
 * submit. The currently-selected component is taken from `store.selectedComponent`; for COBYLA
 * we additionally auto-inject `motor_ids` from the catalog so the user doesn't have to type them.
 *
 * @param {string} strategyKey   Backend key (e.g. "COBYLA", "GRID_SEARCH").
 * @param {{ name: string, description: string, parameters: Record<string, any> }} strategyDef
 */
export function showParameterModal(strategyKey, strategyDef) {
    const existing = document.getElementById('param-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'param-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.7)';
    overlay.style.zIndex = '2000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #2a2e36';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '400px';
    card.style.boxShadow = '0 10px 25px rgba(0,0,0,0.5)';

    const title = document.createElement('h3');
    title.textContent = `Configure ${strategyDef.name}`;
    title.style.margin = '0 0 8px 0';
    title.style.color = '#e2e8f0';
    card.appendChild(title);

    const desc = document.createElement('p');
    desc.textContent = strategyDef.description;
    desc.style.margin = '0 0 20px 0';
    desc.style.color = '#94a3b8';
    desc.style.fontSize = '13px';
    card.appendChild(desc);

    const form = document.createElement('form');
    const inputs = {};

    Object.entries(strategyDef.parameters).forEach(([paramKey, paramDef]) => {
        const field = document.createElement('div');
        field.style.marginBottom = '16px';

        const label = document.createElement('label');
        label.textContent = `${paramKey} (${paramDef.description})`;
        label.style.display = 'block';
        label.style.marginBottom = '6px';
        label.style.color = '#cbd5e1';
        label.style.fontSize = '12px';
        field.appendChild(label);

        let input;
        if (paramDef.enum) {
            input = document.createElement('select');
            paramDef.enum.forEach((opt) => {
                const option = document.createElement('option');
                option.value = opt;
                option.textContent = opt;
                if (opt === paramDef.default) option.selected = true;
                input.appendChild(option);
            });
        } else {
            input = document.createElement('input');
            input.type = paramDef.type === 'integer' || paramDef.type === 'float' ? 'number' : 'text';
            input.value = paramDef.default !== null ? paramDef.default : '';
            if (paramDef.type === 'float') input.step = '0.01';
        }

        input.style.width = '100%';
        input.style.padding = '8px';
        input.style.backgroundColor = '#0f1115';
        input.style.border = '1px solid #2a2e36';
        input.style.borderRadius = '4px';
        input.style.color = 'white';

        inputs[paramKey] = input;
        field.appendChild(input);
        form.appendChild(field);
    });

    const btnRow = document.createElement('div');
    btnRow.style.display = 'flex';
    btnRow.style.justifyContent = 'flex-end';
    btnRow.style.gap = '12px';
    btnRow.style.marginTop = '24px';

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.style.width = 'auto';
    cancelBtn.onclick = () => overlay.remove();

    const runBtn = document.createElement('button');
    runBtn.textContent = 'Run Strategy';
    runBtn.type = 'submit';
    runBtn.className = 'btn btn-primary';
    runBtn.style.width = 'auto';

    form.onsubmit = (e) => {
        e.preventDefault();
        const params = {};
        Object.entries(inputs).forEach(([key, el]) => {
            const def = strategyDef.parameters[key];
            let val = el.value;
            if (def.type === 'integer') val = parseInt(val);
            if (def.type === 'float') val = parseFloat(val);
            params[key] = val;
        });

        params.strategy = strategyKey;

        // Auto-inject `motor_ids` for COBYLA so the user doesn't have to type them by hand —
        // they're already in the catalog for any motorized part.
        if (strategyKey === 'COBYLA' && store.selectedComponent) {
            if (
                store.catalogMap[store.selectedComponent] &&
                store.catalogMap[store.selectedComponent].motor_ids
            ) {
                params.motor_ids = store.catalogMap[store.selectedComponent].motor_ids;
                log(`Using motor_ids: [${params.motor_ids.join(', ')}]`, 'info');
            }
        }

        sendCommand({
            action: 'OPTIMIZE',
            target_id: store.selectedComponent,
            parameters: params,
        });
        overlay.remove();
    };

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(runBtn);
    form.appendChild(btnRow);
    card.appendChild(form);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

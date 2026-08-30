/**
 * Generic full-screen modal overlays (error + confirm).
 */

/**
 * Blocking error modal.
 *
 * Two kinds, distinguished by `options.kind` (recorded on
 * `overlay.dataset.errorKind` so the lab-state poll knows whether it may
 * auto-clear the modal on recovery):
 *
 *   - `'dismissible'` (default): an *operation* failed (e.g. "Apply on bench
 *     failed"). The backend is still reachable, so this modal is NOT
 *     auto-removed by the poll — it stays up with a "Dismiss" button until
 *     the user reads it and closes it.
 *   - `'connection'`: connection / fetch failure. Shows a "Retry Connection"
 *     button that reloads the page. The lab-state poll removes this modal
 *     automatically once it can fetch state again (it's stale by then).
 *
 * No-ops if `#error-modal` is already on screen.
 *
 * @param {string} title
 * @param {string} message HTML allowed (inlined into innerHTML).
 * @param {{ kind?: 'connection' | 'dismissible' }} [options]
 */
export function showErrorModal(title, message, options = {}) {
    if (document.getElementById('error-modal')) return;

    const kind = options.kind === 'connection' ? 'connection' : 'dismissible';

    const overlay = document.createElement('div');
    overlay.id = 'error-modal';
    overlay.dataset.errorKind = kind;
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

    const actionHtml =
        kind === 'dismissible'
            ? `<button id="error-modal-dismiss" class="btn btn-primary" style="background-color: #ef4444; width: auto; margin: 0 auto; padding: 10px 24px;">
                    <span class="material-icons-round">close</span> Dismiss
                </button>`
            : `<button id="error-modal-reload" class="btn btn-primary" style="background-color: #ef4444; width: auto; margin: 0 auto; padding: 10px 24px;">
                    <span class="material-icons-round">refresh</span> Retry Connection
                </button>`;

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 48px; color: #ef4444; margin-bottom: 16px;">report_problem</span>
        <h2 style="margin: 0 0 12px 0; color: #e2e8f0; font-size: 20px;">${title}</h2>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5; white-space: pre-wrap; text-align: left;">${message}</p>
        ${actionHtml}
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    if (kind === 'dismissible') {
        document.getElementById('error-modal-dismiss')?.addEventListener('click', () => {
            overlay.remove();
        });
    } else {
        document.getElementById('error-modal-reload')?.addEventListener('click', () => {
            location.reload();
        });
    }
}

/**
 * Modal with Cancel / Confirm; runs the appropriate callback on click and removes itself.
 * No-ops if `#confirm-modal` is already on screen.
 * @param {string} message HTML allowed.
 * @param {() => void} [onConfirm]
 * @param {() => void} [onCancel]
 * @param {{
 *   confirmLabel?: string,
 *   cancelLabel?: string,
 *   placement?: 'center' | 'top-left',
 * }} [options]
 */
export function showConfirmationModal(message, onConfirm, onCancel, options = {}) {
    if (document.getElementById('confirm-modal')) return;

    const confirmLabel = options.confirmLabel || 'Confirm';
    const cancelLabel = options.cancelLabel || 'Cancel';
    const placement = options.placement === 'center' ? 'center' : 'top-left';

    const overlay = document.createElement('div');
    overlay.id = 'confirm-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100vw';
    overlay.style.height = '100vh';
    // Light dim so the canvas / ghost stay visible behind the dialog.
    overlay.style.backgroundColor = 'rgba(15, 23, 42, 0.28)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    if (placement === 'top-left') {
        overlay.style.alignItems = 'flex-start';
        overlay.style.justifyContent = 'flex-start';
        // Below top nav; inset past the left sidebar so the card sits over the table.
        overlay.style.padding = '56px 16px 16px 276px';
    } else {
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';
        overlay.style.padding = '16px';
    }
    overlay.style.boxSizing = 'border-box';

    const card = document.createElement('div');
    card.style.backgroundColor = 'rgba(24, 27, 33, 0.94)';
    card.style.border = '1px solid #3b82f6';
    card.style.borderRadius = '8px';
    card.style.padding = '20px';
    card.style.width = '360px';
    card.style.maxWidth = 'min(360px, calc(100vw - 300px))';
    card.style.textAlign = 'left';
    card.style.boxShadow = '0 12px 40px rgba(0,0,0,0.45)';
    card.style.backdropFilter = 'blur(2px)';

    card.innerHTML = `
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
            <span class="material-icons-round" style="font-size: 28px; color: #3b82f6;">help_outline</span>
            <h3 style="margin: 0; color: #e2e8f0; font-size: 16px;">Confirm Action</h3>
        </div>
        <p style="margin: 0 0 18px 0; color: #94a3b8; font-size: 13px; line-height: 1.5;">${message}</p>
        <div style="display: flex; justify-content: flex-end; gap: 10px;">
            <button id="confirm-no" class="btn btn-secondary" style="width: auto; padding: 8px 16px;">${cancelLabel}</button>
            <button id="confirm-yes" class="btn btn-primary" style="width: auto; padding: 8px 16px;">${confirmLabel}</button>
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

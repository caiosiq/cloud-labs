/**
 * Persist Twin left/right sidebar collapse for paper-friendly canvas focus.
 */
const LEFT_KEY = 'cloudlabs.twin.sidebar.leftCollapsed';
const RIGHT_KEY = 'cloudlabs.twin.sidebar.rightCollapsed';

function readBool(key, fallback) {
    try {
        const v = localStorage.getItem(key);
        if (v === null) return fallback;
        return v === '1' || v === 'true';
    } catch {
        return fallback;
    }
}

function writeBool(key, value) {
    try {
        localStorage.setItem(key, value ? '1' : '0');
    } catch {
        /* ignore quota / private mode */
    }
}

function defaultLeftCollapsed() {
    return typeof window !== 'undefined' && window.innerWidth < 1280;
}

function defaultRightCollapsed() {
    return typeof window !== 'undefined' && window.innerWidth < 1100;
}

function applyCollapsed(shell, side, collapsed) {
    if (!shell) return;
    const cls = side === 'left' ? 'left-collapsed' : 'right-collapsed';
    shell.classList.toggle(cls, collapsed);
    const btn = document.getElementById(
        side === 'left' ? 'toggle-sidebar-left' : 'toggle-sidebar-right',
    );
    if (btn) {
        const openIcon = side === 'left' ? 'chevron_left' : 'chevron_right';
        const closedIcon = side === 'left' ? 'chevron_right' : 'chevron_left';
        const icon = btn.querySelector('.material-icons-round');
        if (icon) icon.textContent = collapsed ? closedIcon : openIcon;
        btn.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
        btn.title = collapsed
            ? side === 'left'
                ? 'Show inventory sidebar'
                : 'Show workspace sidebar'
            : side === 'left'
              ? 'Hide inventory sidebar'
              : 'Hide workspace sidebar';
        btn.setAttribute(
            'aria-label',
            btn.title,
        );
    }
}

export function initSidebarCollapse() {
    const shell = document.querySelector('.app-shell');
    if (!shell) return;

    let left = readBool(LEFT_KEY, defaultLeftCollapsed());
    let right = readBool(RIGHT_KEY, defaultRightCollapsed());

    applyCollapsed(shell, 'left', left);
    applyCollapsed(shell, 'right', right);

    const leftBtn = document.getElementById('toggle-sidebar-left');
    const rightBtn = document.getElementById('toggle-sidebar-right');

    leftBtn?.addEventListener('click', () => {
        left = !left;
        writeBool(LEFT_KEY, left);
        applyCollapsed(shell, 'left', left);
        window.dispatchEvent(new Event('cloudlabs:layout'));
    });

    rightBtn?.addEventListener('click', () => {
        right = !right;
        writeBool(RIGHT_KEY, right);
        applyCollapsed(shell, 'right', right);
        window.dispatchEvent(new Event('cloudlabs:layout'));
    });
}

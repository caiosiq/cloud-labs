const STORAGE_KEY = 'cloudlabs.systemMonitorHeightPx';
const DEFAULT_HEIGHT_PX = 150;
const MIN_MONITOR_HEIGHT_PX = 96;
const MIN_WORKSPACE_HEIGHT_PX = 180;

function storedHeight() {
    try {
        const value = Number(localStorage.getItem(STORAGE_KEY));
        return Number.isFinite(value) ? value : DEFAULT_HEIGHT_PX;
    } catch {
        return DEFAULT_HEIGHT_PX;
    }
}

function persistHeight(heightPx) {
    try {
        localStorage.setItem(STORAGE_KEY, String(Math.round(heightPx)));
    } catch {
        // Ignore storage failures; resizing should still work for this session.
    }
}

export function initSidebarResize() {
    const sidebar = document.getElementById('sidebar-right');
    const monitor = document.getElementById('system-monitor-panel');
    const handle = document.getElementById('sidebar-right-resize-handle');
    if (!sidebar || !monitor || !handle) return;

    let dragging = false;
    let startY = 0;
    let startHeight = DEFAULT_HEIGHT_PX;
    let activePointerId = null;
    let usingMouseFallback = false;

    function clampHeight(heightPx) {
        const sidebarHeight = sidebar.getBoundingClientRect().height || window.innerHeight;
        const handleHeight = handle.getBoundingClientRect().height || 8;
        const maxHeight = Math.max(
            MIN_MONITOR_HEIGHT_PX,
            sidebarHeight - handleHeight - MIN_WORKSPACE_HEIGHT_PX,
        );
        return Math.min(Math.max(heightPx, MIN_MONITOR_HEIGHT_PX), maxHeight);
    }

    function setHeight(heightPx, { persist = false } = {}) {
        const height = clampHeight(heightPx);
        sidebar.style.setProperty('--system-monitor-height', `${Math.round(height)}px`);
        handle.setAttribute('aria-valuemin', String(MIN_MONITOR_HEIGHT_PX));
        handle.setAttribute('aria-valuemax', String(Math.round(clampHeight(Number.MAX_SAFE_INTEGER))));
        handle.setAttribute('aria-valuenow', String(Math.round(height)));
        if (persist) persistHeight(height);
    }

    setHeight(storedHeight());

    function beginDrag(event) {
        if (dragging) return;
        dragging = true;
        startY = event.clientY;
        startHeight = monitor.getBoundingClientRect().height || DEFAULT_HEIGHT_PX;
        handle.classList.add('is-dragging');
        document.body.classList.add('sidebar-resizing');
        event.preventDefault();
    }

    handle.addEventListener('pointerdown', (event) => {
        usingMouseFallback = false;
        activePointerId = event.pointerId;
        beginDrag(event);
        handle.setPointerCapture(event.pointerId);
    });

    handle.addEventListener('pointermove', (event) => {
        if (!dragging || usingMouseFallback) return;
        setHeight(startHeight + event.clientY - startY);
        event.preventDefault();
    });

    function endDrag(event) {
        if (!dragging) return;
        dragging = false;
        if (activePointerId != null) {
            try {
                handle.releasePointerCapture(activePointerId);
            } catch {
                // Pointer capture may already be released by the browser.
            }
        }
        activePointerId = null;
        usingMouseFallback = false;
        handle.classList.remove('is-dragging');
        document.body.classList.remove('sidebar-resizing');
        setHeight(monitor.getBoundingClientRect().height, { persist: true });
    }

    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);

    handle.addEventListener('mousedown', (event) => {
        if (event.button !== 0 || dragging) return;
        usingMouseFallback = true;
        beginDrag(event);
    });

    document.addEventListener('mousemove', (event) => {
        if (!dragging || !usingMouseFallback) return;
        setHeight(startHeight + event.clientY - startY);
        event.preventDefault();
    });

    document.addEventListener('mouseup', (event) => {
        if (!dragging || !usingMouseFallback) return;
        endDrag(event);
    });

    handle.addEventListener('keydown', (event) => {
        if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
        const current = monitor.getBoundingClientRect().height || DEFAULT_HEIGHT_PX;
        const step = event.shiftKey ? 48 : 16;
        if (event.key === 'ArrowUp') setHeight(current - step, { persist: true });
        if (event.key === 'ArrowDown') setHeight(current + step, { persist: true });
        if (event.key === 'Home') setHeight(MIN_MONITOR_HEIGHT_PX, { persist: true });
        if (event.key === 'End') setHeight(Number.MAX_SAFE_INTEGER, { persist: true });
        event.preventDefault();
    });

    window.addEventListener('resize', () => {
        setHeight(monitor.getBoundingClientRect().height, { persist: true });
    });
}

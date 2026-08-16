/**
 * Command Matrix thread panel — VC-style per-thread action lists beside the canvas.
 *
 * Columns = execution threads. Arm / sense always appear (idle = muted).
 * Motor columns appear only while they have work. During VC apply, the Applying
 * badge keeps the plan step list; live threads stay here (no duplicate embed).
 */
import { store } from '../state/store.js';
import { log } from './log.js';
import { getCatalogRow } from '../component-model.js';
import { BUSY_POLLING_INTERVAL } from '../config.js';
import {
    cancelAllQueuedCommands,
    fetchCommandQueue,
} from '../api/command-queue.js';
import { isReconcileRunning } from '../control/control-state.js';

/** @type {ReturnType<typeof setInterval> | null} */
let _pollTimer = null;
/** @type {boolean} */
let _fetchInFlight = false;

const KIND_ORDER = { arm: 0, sense: 1, motor: 2 };

function shortAction(action) {
    const a = String(action || '').trim();
    if (!a) return '?';
    return a.replace(/_/g, ' ');
}

function formatTarget(tagId) {
    const id = tagId == null || tagId === '' ? '' : String(tagId);
    if (!id) return '';
    const name = getCatalogRow(id)?.name;
    if (name && name !== id) return `${name} (${id})`;
    return id;
}

function formatJobLabel(item) {
    if (!item) return '';
    const act = shortAction(item.action);
    const target = formatTarget(item.target_id);
    if (String(item.action || '').toUpperCase() === 'OPTIMIZE') {
        return target ? `OPTIMIZE · ${target}` : 'OPTIMIZE (barrier)';
    }
    return target ? `${act} → ${target}` : act;
}

function threadTitle(thread) {
    const kind = String(thread.kind || '').toLowerCase();
    const id = String(thread.id || '');
    if (kind === 'arm') return 'arm';
    if (kind === 'sense') return 'sense';
    if (kind === 'motor') {
        if (thread.tag_id != null && thread.motor_id != null) {
            return `motor ${thread.tag_id} · ${thread.motor_id}`;
        }
        return id.replace(/^motor\./, 'motor ');
    }
    return id || kind || 'thread';
}

function sortThreads(threads) {
    return [...(threads || [])].sort((a, b) => {
        const ka = KIND_ORDER[String(a.kind || '').toLowerCase()] ?? 9;
        const kb = KIND_ORDER[String(b.kind || '').toLowerCase()] ?? 9;
        if (ka !== kb) return ka - kb;
        return String(a.id || '').localeCompare(String(b.id || ''));
    });
}

function threadHasActivity(thread) {
    if (!thread) return false;
    if (thread.session_lock) return true;
    if (thread.running) return true;
    const q = thread.queue || [];
    return q.some((item) => item && (item.status === 'queued' || item.status === 'running'));
}

function matrixHasActivity(snap) {
    if (!snap || snap.enabled === false) return false;
    if (snap.session_locks && Object.keys(snap.session_locks).length) return true;
    return (snap.threads || []).some(threadHasActivity);
}

function countQueued(snap) {
    let n = 0;
    for (const t of snap.threads || []) {
        for (const item of t.queue || []) {
            if (item && item.status === 'queued') n += 1;
        }
    }
    // Barriers appear on every thread — count unique command_ids.
    const seen = new Set();
    let unique = 0;
    for (const t of snap.threads || []) {
        for (const item of t.queue || []) {
            if (!item || item.status !== 'queued') continue;
            const cid = item.command_id || '';
            if (cid && seen.has(cid)) continue;
            if (cid) seen.add(cid);
            unique += 1;
        }
    }
    return unique || n;
}

/**
 * Build display rows for one thread (running first, then queued behind).
 * @param {Record<string, any>} thread
 */
function buildThreadRows(thread) {
    const rows = [];
    const lock = thread.session_lock;
    if (lock) {
        const tag = formatTarget(lock.tag_id) || lock.tag_id || '?';
        rows.push({
            kind: 'lock',
            status: 'lock',
            label: `TELEOP holds ${tag}`,
            marker: '🔒',
        });
    }

    const seen = new Set();
    const pushItem = (item, forcedStatus) => {
        if (!item) return;
        const cid = item.command_id || `${item.action}:${item.target_id}`;
        if (seen.has(cid)) return;
        seen.add(cid);
        const action = String(item.action || '').toUpperCase();
        const isBarrier = item.kind === 'barrier' || action === 'OPTIMIZE';
        let status = forcedStatus || item.status || 'queued';
        if (status === 'running') status = 'active';
        const blocked =
            Boolean(lock) &&
            status === 'queued' &&
            action !== 'END_TELEOP' &&
            String(lock.kind || '') === 'teleop';
        rows.push({
            kind: isBarrier ? 'barrier' : 'job',
            status: blocked ? 'blocked' : status,
            label: formatJobLabel(item),
            marker:
                status === 'active'
                    ? '▶'
                    : isBarrier
                      ? '≡'
                      : blocked
                        ? '⏸'
                        : '·',
            commandId: item.command_id,
        });
    };

    if (thread.running) pushItem(thread.running, 'active');
    for (const item of thread.queue || []) {
        if (!item) continue;
        if (item.status === 'running') continue; // already via running
        if (thread.running && item.command_id === thread.running.command_id) continue;
        pushItem(item, item.status === 'queued' ? 'queued' : item.status);
    }
    return rows;
}

function shouldShowThread(thread) {
    const kind = String(thread?.kind || '').toLowerCase();
    // Shared topology columns stay visible (idle = muted). Motor columns are
    // created on demand and only appear while they have work / a session lock.
    if (kind === 'arm' || kind === 'sense') return true;
    return threadHasActivity(thread);
}

/**
 * Render matrix snapshot into a container.
 * @param {HTMLElement | null} container
 * @param {Record<string, any> | null} snap
 * @param {{ compact?: boolean }} [opts]
 */
export function renderCommandMatrixInto(container, snap, opts = {}) {
    if (!container) return;
    container.innerHTML = '';
    if (!snap || snap.enabled === false) {
        const empty = document.createElement('p');
        empty.className = 'command-matrix-panel__empty';
        empty.textContent = snap && snap.enabled === false
            ? 'Command Matrix off for this backend'
            : 'Waiting for queue…';
        container.appendChild(empty);
        return;
    }

    const threads = sortThreads(snap.threads || []).filter(shouldShowThread);
    if (!threads.length) {
        const empty = document.createElement('p');
        empty.className = 'command-matrix-panel__empty';
        empty.textContent = 'No threads advertised';
        container.appendChild(empty);
        return;
    }

    const grid = document.createElement('div');
    grid.className = 'command-matrix-panel__grid';
    if (opts.compact) grid.classList.add('is-compact');

    for (const thread of threads) {
        const busy = threadHasActivity(thread);
        const col = document.createElement('div');
        col.className = 'command-matrix-panel__col';
        if (!busy) col.classList.add('is-empty');
        col.dataset.threadId = thread.id || '';
        col.dataset.kind = thread.kind || '';

        const head = document.createElement('div');
        head.className = 'command-matrix-panel__col-head';
        head.textContent = threadTitle(thread);
        head.title = thread.id || '';
        col.appendChild(head);

        const list = document.createElement('ol');
        list.className = 'command-matrix-panel__steps';
        const rows = buildThreadRows(thread);
        if (!rows.length) {
            const li = document.createElement('li');
            li.className = 'command-matrix-panel__step is-idle';
            li.innerHTML =
                '<span class="command-matrix-panel__marker">·</span>' +
                '<span class="command-matrix-panel__label">idle</span>';
            list.appendChild(li);
        } else {
            for (const row of rows) {
                const li = document.createElement('li');
                li.className = 'command-matrix-panel__step';
                if (row.status === 'active') li.classList.add('is-active');
                if (row.status === 'blocked') li.classList.add('is-blocked');
                if (row.kind === 'barrier') li.classList.add('is-barrier');
                if (row.kind === 'lock') li.classList.add('is-lock');
                if (row.status === 'done') li.classList.add('is-done');
                if (row.status === 'error' || row.status === 'failed') {
                    li.classList.add('is-error');
                }

                const marker = document.createElement('span');
                marker.className = 'command-matrix-panel__marker';
                marker.textContent = row.marker;

                const label = document.createElement('span');
                label.className = 'command-matrix-panel__label';
                label.textContent = row.label;

                li.appendChild(marker);
                li.appendChild(label);
                list.appendChild(li);
            }
        }
        col.appendChild(list);
        grid.appendChild(col);
    }

    container.appendChild(grid);
}

function panelEls() {
    return {
        panel: document.getElementById('command-matrix-panel'),
        threads: document.getElementById('command-matrix-threads'),
        clearBtn: document.getElementById('command-matrix-clear-btn'),
        meta: document.getElementById('command-matrix-meta'),
    };
}

/**
 * Apply a snapshot to store + the Command queue panel.
 * @param {Record<string, any> | null} snap
 */
export function applyCommandMatrixSnapshot(snap) {
    store.commandMatrix = snap;
    const { panel, threads, clearBtn, meta } = panelEls();
    const active = matrixHasActivity(snap) || isReconcileRunning();
    const enabled = Boolean(snap && snap.enabled !== false);

    if (panel) {
        // Always visible when the matrix is on; idle arm/sense stay muted.
        panel.hidden = !enabled;
        panel.classList.toggle('is-reconcile', isReconcileRunning());
        panel.classList.toggle('is-idle', enabled && !active);
    }
    if (threads) renderCommandMatrixInto(threads, snap);
    if (meta) {
        const q = snap ? countQueued(snap) : 0;
        const running = (snap?.threads || []).some((t) => t.running);
        if (!enabled) meta.textContent = '';
        else if (!active) meta.textContent = 'idle';
        else if (running && q) meta.textContent = `running · ${q} queued`;
        else if (running) meta.textContent = 'running';
        else if (q) meta.textContent = `${q} queued`;
        else meta.textContent = 'session lock';
    }
    if (clearBtn) {
        const q = snap ? countQueued(snap) : 0;
        clearBtn.hidden = q === 0;
        clearBtn.disabled = q === 0;
    }

    if (active && enabled) startCommandMatrixPolling();
    else stopCommandMatrixPolling();
}

export function syncCommandMatrixPanel() {
    applyCommandMatrixSnapshot(store.commandMatrix);
}

export async function refreshCommandMatrixPanel() {
    if (_fetchInFlight) return;
    _fetchInFlight = true;
    try {
        const snap = await fetchCommandQueue();
        applyCommandMatrixSnapshot(snap);
    } catch (e) {
        // Soft-fail: keep last snapshot; avoid spamming logs every poll tick.
        if (!_pollTimer) {
            log(`Command queue: ${e.message || e}`, 'warn');
        }
    } finally {
        _fetchInFlight = false;
    }
}

export function startCommandMatrixPolling() {
    if (_pollTimer != null) return;
    _pollTimer = setInterval(() => {
        void refreshCommandMatrixPanel();
    }, BUSY_POLLING_INTERVAL);
}

export function stopCommandMatrixPolling() {
    if (_pollTimer == null) return;
    clearInterval(_pollTimer);
    _pollTimer = null;
}

export function initCommandMatrixPanel() {
    const { clearBtn } = panelEls();
    if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
            try {
                clearBtn.disabled = true;
                const data = await cancelAllQueuedCommands();
                if (data.command_matrix) {
                    applyCommandMatrixSnapshot(data.command_matrix);
                } else {
                    await refreshCommandMatrixPanel();
                }
                const n = (data.cancelled || []).length;
                log(
                    n ? `Cleared ${n} queued command(s)` : 'No queued commands to clear',
                    'info',
                );
            } catch (e) {
                log(`Clear queued failed: ${e.message || e}`, 'error');
            } finally {
                clearBtn.disabled = false;
            }
        });
    }
    syncCommandMatrixPanel();
    // Catch an in-flight queue if the Twin loaded mid-run.
    void refreshCommandMatrixPanel();
}

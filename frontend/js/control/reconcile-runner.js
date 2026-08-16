/**
 * Step-by-step reconcile plan runner.
 *
 * Drives each primitive through `/api/command` so the operator sees per-step
 * feedback: component highlight, System Monitor lines, and a live step list in
 * the config-view badge beside the canvas.
 */
import { store } from '../state/store.js';
import { log } from '../ui/log.js';
import { render } from '../canvas/render.js';
import { fetchLabState } from '../state/lab-state.js';
import { applyConfigurationOverlays } from '../api/overlays.js';
import { syncReconcileProgressUi } from '../ui/config-view-mode.js';
import {
    RECONCILE_STEP_DELAY_MS,
    RECONCILE_POLL_INTERVAL_MS,
    RECONCILE_STEP_TIMEOUT_MS,
} from '../config.js';
import { leaseHeaders } from '../api/session-lease.js';
import { withBackendQuery } from '../state/backend-selection.js';
import { getCatalogRow } from '../component-model.js';
import { runtimeEditableOrMessage } from './control-state.js';
import {
    applyCommandMatrixSnapshot,
    refreshCommandMatrixPanel,
} from '../ui/command-matrix-panel.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Max time (ms) to wait for a step to enter BUSY before treating it as a no-op. */
const BUSY_APPEAR_TIMEOUT_MS = 1500;

/** Primitives that should light the canvas MOVING / in-air overlay for one tag. */
const CANVAS_HIGHLIGHT_ACTIONS = new Set([
    'MOVE_COMPONENT',
    'STORE_COMPONENT',
    'PLACE_FROM_STORAGE',
    'PICK_COMPONENT',
    'HOVER',
    'PLACE_FROM_HOVER',
    'REMOVE',
]);

function currentStatus() {
    return (store.labState && store.labState.system_status) || 'IDLE';
}

function isStable(status) {
    return status === 'IDLE' || status === 'HOLDING';
}

function isReconcileActive() {
    return Boolean(store.control.reconcileProgress && store.control.reconcileProgress.active);
}

function formatTargetLabel(tagId) {
    const id = tagId == null || tagId === '' ? '' : String(tagId);
    if (!id) return '';
    const name = getCatalogRow(id)?.name;
    if (name && name !== id) return `${name} (${id})`;
    return id;
}

function formatPrimitiveLabel(env) {
    const action = env.action || '?';
    const target = formatTargetLabel(env.target_id);
    return target ? `${action} → ${target}` : action;
}

/**
 * Build the step list shown beside the canvas.
 * @param {Array<Record<string, any>>} plan
 * @param {{ includeOverlays?: boolean }} [opts]
 */
export function buildReconcileStepList(plan, { includeOverlays = false } = {}) {
    const steps = [];
    if (includeOverlays) {
        steps.push({
            id: 'overlays',
            label: 'Update alignment lines',
            status: 'pending',
        });
    }
    (plan || []).forEach((env, index) => {
        steps.push({
            id: `primitive-${index}`,
            label: formatPrimitiveLabel(env),
            status: 'pending',
        });
    });
    return steps;
}

function initProgress({ title, commitId, steps }) {
    store.control.reconcileProgress = {
        active: true,
        title,
        commitId: commitId ?? null,
        steps: steps.map((s) => ({ ...s })),
        currentIndex: -1,
    };
    syncReconcileProgressUi();
}

function setStepStatus(index, status) {
    const progress = store.control.reconcileProgress;
    if (!progress || !progress.steps[index]) return;
    progress.steps[index].status = status;
    if (status === 'active') progress.currentIndex = index;
    syncReconcileProgressUi();
}

function finishProgress() {
    store.control.reconcileProgress = null;
    syncReconcileProgressUi();
}

function failProgress(index) {
    const progress = store.control.reconcileProgress;
    if (progress && progress.steps[index]) {
        progress.steps[index].status = 'error';
        syncReconcileProgressUi();
    }
}

function clearAllHighlights() {
    store.pendingCommands.clear();
    store.pendingActions.clear();
}

/**
 * Highlight exactly one tag for the active reconcile step (or none).
 * Replaces any prior pending overlays so completed steps do not keep MOVING...
 */
function setHighlight(target, action) {
    clearAllHighlights();
    if (!target) return;
    const act = action ? String(action) : '';
    if (act && !CANVAS_HIGHLIGHT_ACTIONS.has(act)) return;
    store.pendingCommands.add(String(target));
    if (act) store.pendingActions.set(String(target), act);
}

async function postPrimitive(envelope) {
    const res = await fetch(withBackendQuery('/api/command'), {
        method: 'POST',
        headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            action: envelope.action,
            target_id: envelope.target_id,
            parameters: envelope.parameters || {},
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data && data.detail;
        const msg =
            typeof detail === 'string'
                ? detail
                : (detail && detail.message) || res.statusText || `HTTP ${res.status}`;
        throw new Error(msg);
    }
    return data;
}

async function fetchCommandStatuses(commandIds) {
    const res = await fetch(withBackendQuery('/api/command-queue/status'), {
        method: 'POST',
        headers: leaseHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ command_ids: commandIds }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return {};
    return (data && data.statuses) || {};
}

/**
 * Matrix-aware reconcile: enqueue the whole plan, then wait for drain.
 * Falls back to legacy one-at-a-time waiting when the server does not queue.
 */
async function runPrimitivesViaMatrix(
    primitives,
    { stepIndexOffset = 0, stepsLength = 0, delayMs = 0 } = {},
) {
    const tracked = [];
    let useMatrix = null; // null = unknown, true/false after first response

    for (let i = 0; i < primitives.length; i += 1) {
        const env = primitives[i] || {};
        const target = env.target_id;
        const action = env.action;
        const idx = stepIndexOffset + i;

        setStepStatus(idx, 'active');
        setHighlight(target, action);
        render();
        log(`Step ${idx + 1}/${stepsLength}: ${formatPrimitiveLabel(env)}`, 'info');

        try {
            const res = await postPrimitive(env);
            if (res && res.message) log(`  ${res.message}`, 'info');

            if (useMatrix === null) {
                useMatrix = Boolean(res && res.status === 'queued' && res.command_id);
            }

            if (useMatrix && res && res.status === 'queued' && res.command_id) {
                tracked.push({
                    commandId: String(res.command_id),
                    stepIndex: idx,
                    target,
                    action,
                });
                if (res.command_matrix) {
                    applyCommandMatrixSnapshot(res.command_matrix);
                } else {
                    void refreshCommandMatrixPanel();
                }
            } else {
                await waitForStepSettled(target, action);
                setStepStatus(idx, 'done');
                clearAllHighlights();
                await fetchLabState();
                render();
                if (delayMs > 0 && i + 1 < primitives.length) await sleep(delayMs);
            }
        } catch (e) {
            failProgress(idx);
            throw new Error(
                `Step ${idx + 1}/${stepsLength} (${formatPrimitiveLabel(env)}) failed: ` +
                    (e.message || String(e)),
            );
        }
    }

    if (tracked.length === 0) return;

    log(`Waiting for ${tracked.length} queued reconcile step(s) to drain…`, 'info');
    const start = Date.now();
    const pending = new Set(tracked.map((t) => t.commandId));

    while (pending.size > 0) {
        if (Date.now() - start > RECONCILE_STEP_TIMEOUT_MS * Math.max(1, tracked.length)) {
            throw new Error('timed out waiting for queued reconcile steps to finish');
        }
        const statuses = await fetchCommandStatuses([...pending]);
        for (const t of tracked) {
            if (!pending.has(t.commandId)) continue;
            const st = statuses[t.commandId];
            const status = st && st.status;
            if (status === 'running' || status === 'queued') {
                setStepStatus(t.stepIndex, 'active');
            } else if (status === 'done') {
                pending.delete(t.commandId);
                setStepStatus(t.stepIndex, 'done');
            } else if (status === 'failed' || status === 'cancelled') {
                pending.delete(t.commandId);
                failProgress(t.stepIndex);
                const err = (st && st.error) || status;
                throw new Error(
                    `Step ${t.stepIndex + 1}/${stepsLength} (${formatPrimitiveLabel({
                        action: t.action,
                        target_id: t.target,
                    })}) ${status}: ${err}`,
                );
            } else if (status === 'unknown' && isStable(currentStatus())) {
                pending.delete(t.commandId);
                setStepStatus(t.stepIndex, 'done');
            }
        }
        clearAllHighlights();
        for (const t of tracked) {
            if (pending.has(t.commandId)) {
                setHighlight(t.target, t.action);
                break;
            }
        }
        render();
        await sleep(RECONCILE_POLL_INTERVAL_MS);
        await fetchLabState();
        void refreshCommandMatrixPanel();
    }
}

async function waitForStepSettled(target, action) {
    const start = Date.now();
    let sawBusy = false;
    for (;;) {
        const status = currentStatus();
        if (status === 'BUSY' || status === 'OPTIMIZING') sawBusy = true;
        const settled = isStable(status);
        if (sawBusy && settled) return;
        if (!sawBusy && settled && Date.now() - start > BUSY_APPEAR_TIMEOUT_MS) {
            return;
        }
        if (Date.now() - start > RECONCILE_STEP_TIMEOUT_MS) {
            throw new Error('timed out waiting for the step to finish');
        }
        await sleep(RECONCILE_POLL_INTERVAL_MS);
        await fetchLabState();
        if (isReconcileActive() && !isStable(currentStatus())) {
            setHighlight(target, action);
        }
        render();
    }
}

/**
 * @param {Array<Record<string, any>>} plan
 * @param {{
 *   delayMs?: number,
 *   title?: string,
 *   commitId?: string|null,
 *   targetConfig?: Record<string, any>|null,
 *   applyOverlaysFirst?: boolean,
 * }} [opts]
 */
export async function runReconcilePlan(
    plan,
    {
        delayMs = RECONCILE_STEP_DELAY_MS,
        title = 'Reconcile',
        commitId = null,
        targetConfig = null,
        applyOverlaysFirst = false,
    } = {},
) {
    // Apply / stash / pop are the VC paths that *intentionally* move the bench
    // while a preview (or detached tip) may still be active. Only lease + lab
    // init should gate them — not the "return before editing" preview message.
    const blocked = runtimeEditableOrMessage({
        allowPreview: true,
        allowDetached: true,
    });
    if (blocked) {
        log(blocked, 'warn');
        throw new Error(blocked);
    }

    const primitives = Array.isArray(plan) ? plan : [];
    const includeOverlays = applyOverlaysFirst && targetConfig;
    const steps = buildReconcileStepList(primitives, { includeOverlays });
    if (steps.length === 0) return { steps: 0 };

    initProgress({ title, commitId, steps });
    clearAllHighlights();
    log(`${title}: running ${steps.length} step(s)…`, 'info');

    let stepIndex = 0;

    try {
        if (includeOverlays) {
            clearAllHighlights();
            setStepStatus(stepIndex, 'active');
            log('Step 1: updating alignment lines…', 'info');
            await applyConfigurationOverlays(targetConfig);
            await fetchLabState();
            render();
            setStepStatus(stepIndex, 'done');
            stepIndex += 1;
            if (delayMs > 0 && stepIndex < steps.length) await sleep(delayMs);
        }

        // Prefer Command Matrix: enqueue the whole plan, then await drain.
        // Falls back to legacy one-at-a-time wait when the server does not queue.
        await runPrimitivesViaMatrix(primitives, {
            stepIndexOffset: stepIndex,
            stepsLength: steps.length,
            delayMs,
        });

        log(`${title}: all ${steps.length} step(s) complete.`, 'info');
        return { steps: steps.length };
    } finally {
        clearAllHighlights();
        finishProgress();
        render();
    }
}

/** True while a reconcile plan is executing (used to keep preview chrome up). */
export function isReconcileRunning() {
    return isReconcileActive();
}

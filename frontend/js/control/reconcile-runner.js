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
import { runtimeEditableOrMessage } from './control-state.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Max time (ms) to wait for a step to enter BUSY before treating it as a no-op. */
const BUSY_APPEAR_TIMEOUT_MS = 1500;

function currentStatus() {
    return (store.labState && store.labState.system_status) || 'IDLE';
}

function isStable(status) {
    return status === 'IDLE' || status === 'HOLDING';
}

function isReconcileActive() {
    return Boolean(store.control.reconcileProgress && store.control.reconcileProgress.active);
}

function formatPrimitiveLabel(env) {
    const action = env.action || '?';
    const target = env.target_id || '';
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

function setHighlight(target, action) {
    if (!target) return;
    store.pendingCommands.add(target);
    if (action) store.pendingActions.set(target, action);
}

function clearAllHighlights() {
    store.pendingCommands.clear();
    store.pendingActions.clear();
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
    log(`${title}: running ${steps.length} step(s)…`, 'info');

    let stepIndex = 0;

    try {
        if (includeOverlays) {
            setStepStatus(stepIndex, 'active');
            log('Step 1: updating alignment lines…', 'info');
            await applyConfigurationOverlays(targetConfig);
            await fetchLabState();
            render();
            setStepStatus(stepIndex, 'done');
            stepIndex += 1;
            if (delayMs > 0 && stepIndex < steps.length) await sleep(delayMs);
        }

        for (let i = 0; i < primitives.length; i += 1) {
            const env = primitives[i] || {};
            const target = env.target_id;
            const action = env.action;
            const idx = stepIndex;

            setStepStatus(idx, 'active');
            setHighlight(target, action);
            render();
            log(
                `Step ${idx + 1}/${steps.length}: ${formatPrimitiveLabel(env)}`,
                'info',
            );

            try {
                const res = await postPrimitive(env);
                if (res && res.message) log(`  ${res.message}`, 'info');
                await waitForStepSettled(target, action);
            } catch (e) {
                failProgress(idx);
                throw new Error(
                    `Step ${idx + 1}/${steps.length} (${formatPrimitiveLabel(env)}) failed: ` +
                        (e.message || String(e)),
                );
            }

            setStepStatus(idx, 'done');
            await fetchLabState();
            render();
            stepIndex += 1;

            if (delayMs > 0 && stepIndex < steps.length) await sleep(delayMs);
        }

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

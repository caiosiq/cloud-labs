/**
 * Job telemetry + canvas highlight sync for /operations (Phase C.2).
 *
 * Uses the same ``store.pendingCommands`` / ``pendingActions`` mechanism as
 * ``reconcile-runner.js`` and ``commands.js`` so the twin canvas highlights
 * match the Twin UI during job execution.
 */
import { store } from '../state/store.js';
import { render } from '../canvas/render.js';

let _trackedJob = null;

export function getTrackedJob() {
    return _trackedJob;
}

export function setTrackedJob(job) {
    _trackedJob = job || null;
    const running = !!(job && job.status === 'running');
    store.operationsMonitorActive = running;
    store.operationsTrackedJobId = running ? job.job_id : null;
    syncJobCanvasHighlight(job);
    renderJobTelemetry(job);
}

/**
 * Mirror in-flight job step onto canvas pending overlays (same as Twin UI).
 * @param {object | null | undefined} job
 */
export function syncJobCanvasHighlight(job) {
    if (!job || job.status !== 'running') {
        if (!store.operationsMonitorActive) {
            return;
        }
        store.pendingCommands.clear();
        store.pendingActions.clear();
        render();
        return;
    }

    store.pendingCommands.clear();
    store.pendingActions.clear();

    const progress = job.progress || {};

    if (job.mode === 'compiled_dag') {
        const target = progress.current_target;
        const action = progress.current_action;
        if (target) {
            store.pendingCommands.add(target);
            if (action) store.pendingActions.set(target, action);
        }
    } else if (job.mode === 'closed_loop') {
        const target =
            store.labState?.optimization_target_id ||
            job.spec?.command?.target_id ||
            job.result?.last_ensemble_optimization?.target_id;
        if (target) {
            store.pendingCommands.add(target);
            store.pendingActions.set(target, 'OPTIMIZE');
        }
    }

    render();
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function formatLoss(value) {
    if (value == null || Number.isNaN(Number(value))) return '—';
    const n = Number(value);
    const abs = Math.abs(n);
    if (abs !== 0 && (abs < 1e-3 || abs >= 1e4)) return n.toExponential(2);
    return n.toFixed(4);
}

function formatPrimitiveLabel(env) {
    if (!env || typeof env !== 'object') return '—';
    const action = env.action || '?';
    const target = env.target_id || '';
    return target ? `${action} → ${target}` : action;
}

function metricChip(label, value) {
    return `<div class="job-metric"><span class="job-metric__label">${escapeHtml(label)}</span><span class="job-metric__value">${value}</span></div>`;
}

function renderClosedLoopTelemetry(container, job) {
    const p = job.progress || {};
    const sess = store.labState?.optimization_session;
    const result = job.result?.last_ensemble_optimization || {};
    const trace =
        p.trace_tail || sess?.trace || job.result?.optimization_session?.trace || [];
    const evalN = p.eval ?? sess?.eval ?? result.evals;
    const bestLoss = p.best_loss ?? sess?.best_loss ?? result.best_loss;
    const lastEval = p.last_eval ?? sess?.last_eval;
    const label = p.session_label || '';

    let html = '';
    if (p.phase === 'init') {
        html += `<p class="job-note">${escapeHtml(p.message || 'Reconciling before run…')}</p>`;
    } else if (p.phase === 'post_commit') {
        html += `<p class="job-note">${escapeHtml(p.message || 'Post-job commit…')}</p>`;
    }

    if (label) {
        html += `<p class="job-note job-note--label">${escapeHtml(label)}</p>`;
    }

    html += '<div class="job-metrics">';
    html += metricChip('Eval', evalN != null ? `<strong>${escapeHtml(evalN)}</strong>` : '—');
    html += metricChip('Best loss', `<strong>${escapeHtml(formatLoss(bestLoss))}</strong>`);
    if (lastEval && typeof lastEval === 'object' && lastEval.loss != null) {
        html += metricChip('Last loss', escapeHtml(formatLoss(lastEval.loss)));
    }
    html += '</div>';

    if (Array.isArray(trace) && trace.length) {
        html += '<div class="job-trace-wrap"><table class="job-trace-table"><thead><tr>';
        html += '<th>Eval</th><th>Loss</th><th>Best</th>';
        html += '</tr></thead><tbody>';
        for (const row of trace.slice(-10)) {
            html += `<tr>
                <td>${row.eval ?? '—'}</td>
                <td>${formatLoss(row.loss)}</td>
                <td>${formatLoss(row.best_loss)}</td>
            </tr>`;
        }
        html += '</tbody></table></div>';
    }

    const postCommit = job.result?.post_commit;
    if (job.status === 'succeeded' && postCommit?.configuration_id) {
        html += `<p class="job-note">Committed <code>${escapeHtml(String(postCommit.configuration_id).slice(0, 8))}…</code></p>`;
    }
    if (job.result?.post_commit_error) {
        html += `<p class="job-note job-note--error">${escapeHtml(job.result.post_commit_error)}</p>`;
    }

    container.innerHTML = html;
}

function renderCompiledDagTelemetry(container, job) {
    const p = job.progress || {};
    const steps = job.spec?.steps || [];
    const total = p.steps_total || steps.length || 0;
    const index = p.step_index != null ? p.step_index : 0;
    const completed = p.steps_completed || 0;

    let html = '<div class="job-metrics">';
    html += metricChip(
        'Step',
        `<strong>${Math.min(index + 1, total || index + 1)}</strong> / ${total || '?'}`,
    );
    if (completed) html += metricChip('Done', `<strong>${completed}</strong>`);
    html += '</div>';

    if (p.message) {
        html += `<p class="job-note">${escapeHtml(p.message)}</p>`;
    }

    if (steps.length) {
        html += '<ol class="job-step-list">';
        steps.forEach((step, i) => {
            let cls = '';
            if (i < completed) cls = 'is-done';
            else if (i === index) cls = 'is-active';
            html += `<li class="${cls}"><code>${escapeHtml(formatPrimitiveLabel(step))}</code></li>`;
        });
        html += '</ol>';
    } else if (p.phase === 'finalize') {
        html += '<p class="job-note">Finalizing checkout pointer…</p>';
    }

    container.innerHTML = html;
}

/**
 * @param {object | null | undefined} job
 */
export function renderJobTelemetry(job) {
    const summary = document.getElementById('job-telemetry');
    const json = document.getElementById('job-detail');
    if (!summary) return;

    if (!job) {
        summary.innerHTML =
            '<p class="empty">Select a job to monitor telemetry and twin motion.</p>';
        if (json) {
            json.hidden = true;
            json.textContent = '';
        }
        return;
    }

    // Status/mode live in the jobs table — telemetry is metrics only.
    summary.innerHTML = '';
    const body = document.createElement('div');
    body.className = 'job-telemetry-body';
    summary.appendChild(body);

    if (job.mode === 'closed_loop') {
        renderClosedLoopTelemetry(body, job);
    } else if (job.mode === 'compiled_dag') {
        renderCompiledDagTelemetry(body, job);
    } else {
        body.innerHTML = '<p class="empty">No telemetry renderer for this mode.</p>';
    }

    if (!json) return;
    if (job.status === 'running') {
        json.hidden = false;
        json.innerHTML =
            '<summary class="job-raw-summary">Raw JSON</summary>' +
            '<pre class="job-raw-pre">Running — JSON available when the job finishes.</pre>';
        return;
    }
    json.hidden = false;
    json.innerHTML =
        '<summary class="job-raw-summary">Raw JSON</summary>' +
        `<pre class="job-raw-pre">${escapeHtml(JSON.stringify(job, null, 2))}</pre>`;
}

/** Re-apply highlight after lab-state poll (which may have cleared pending). */
export function refreshJobMonitorFromPoll() {
    if (_trackedJob?.status === 'running') {
        syncJobCanvasHighlight(_trackedJob);
    }
}

store.operationsOnLabStatePoll = refreshJobMonitorFromPoll;

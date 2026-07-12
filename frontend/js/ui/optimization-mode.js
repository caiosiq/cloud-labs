/**
 * Staged "Optimization mode" — measure → variables → objective → run.
 *
 * Replaces the flat ensemble form; aligns with ensemble JSON (variables + objective + solver)
 * while forcing the operator to declare measurables before run.
 */
import { store } from '../state/store.js';
import { isBreadboardIntent, getCatalogRow } from '../component-model.js';
import {
    runtimeEditableOrMessage,
    getAppliedCommitId,
    getAppliedBranch,
    isDetached,
    isConfigViewMode,
} from '../control/control-state.js';
import {
    createOptimizationBuilder,
    OPTIMIZATION_STAGES,
    componentDisplayLabel,
    getOptimizableMeasurablesForTag,
    createObjectiveTerm,
    createDefaultSolverBlock,
    sanitizeObjectiveTerms,
    syncSolverBlocksFromVariables,
    buildObjectiveTermsPayload,
    buildSolverPayload,
    buildAuthoringObjectiveGraph,
    isOptimizationPlanningActive,
} from '../state/optimization-builder.js';
import { executeSendCommand } from '../api/commands.js';
import { fetchJob, submitClosedLoopJob } from '../api/jobs.js';
import { compileObjective } from '../api/optimization.js';
import { fetchLabState } from '../state/lab-state.js';
import { render } from '../canvas/render.js';
import { syncComponentSidebarHighlights } from './updateUI.js';
import { syncBenchChromeHighlights } from './bench-chrome-bar.js';

export { isOptimizationPlanningActive };

let _sendCommand = async (cmd) => executeSendCommand(cmd);
let _log = () => {};
let _lastJobOutcomePollMs = 0;

function syncOptimizationRuntimeChrome() {
    const optimizing = store.labState?.system_status === 'OPTIMIZING';
    const b = builder();
    const awaiting = !!(b.active && b.awaitingRunResults);
    const showResultsChrome = !!(b.active && (b.runCompleted || b.viewingLastRun));
    document.body.classList.toggle('is-optimizing', !!(optimizing || awaiting));
    document.body.classList.toggle('opt-results-ready', showResultsChrome);
    document.body.classList.toggle(
        'is-ensemble-optimize',
        !!(optimizing || showResultsChrome),
    );
}

function refreshOptimizationVisuals() {
    syncOptimizationRuntimeChrome();
    render();
    syncComponentSidebarHighlights();
    syncBenchChromeHighlights();
}

function builder() {
    if (!store.optimizationBuilder) {
        store.optimizationBuilder = createOptimizationBuilder();
    }
    return store.optimizationBuilder;
}

function shortTunableLabel(opt) {
    const tail = opt.path.split('.').pop() || '';
    if (opt.path.includes('motor')) return `Motor ${tail}`;
    if (tail === 'rotation') return 'Rotation';
    return tail.charAt(0).toUpperCase() + tail.slice(1);
}

function groupVariablesByComponent(variables) {
    const groups = new Map();
    variables.forEach((v) => {
        if (!groups.has(v.tag_id)) {
            groups.set(v.tag_id, {
                tagId: v.tag_id,
                label: componentDisplayLabel(v.tag_id),
                items: [],
            });
        }
        groups.get(v.tag_id).items.push(v);
    });
    return [...groups.values()].sort((a, b) =>
        a.label.localeCompare(b.label, undefined, { numeric: true }),
    );
}

function appendSelectedVariablesBar(body, variables, onRemove) {
    if (!variables.length) return;
    const bar = document.createElement('div');
    bar.className = 'opt-selected-bar opt-selected-bar--variables';

    const head = document.createElement('div');
    head.className = 'opt-selected-bar__head';
    head.innerHTML = `<span class="opt-field-label" style="margin:0">Selected variables</span><span class="opt-badge">${variables.length}</span>`;
    bar.appendChild(head);

    const groupsEl = document.createElement('div');
    groupsEl.className = 'opt-selected-groups';

    groupVariablesByComponent(variables).forEach((group) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'opt-selected-group';
        groupEl.dataset.tagId = group.tagId;

        const name = document.createElement('div');
        name.className = 'opt-selected-group__name';
        name.textContent = group.label;
        groupEl.appendChild(name);

        const row = document.createElement('div');
        row.className = 'opt-chip-row';
        group.items.forEach((v) => {
            const chip = document.createElement('span');
            chip.className = 'opt-chip is-selected opt-chip--compact';
            chip.title = v.label;
            const text = document.createElement('span');
            text.className = 'opt-chip-text';
            text.textContent = shortTunableLabel(v);
            chip.appendChild(text);
            const rm = document.createElement('button');
            rm.type = 'button';
            rm.className = 'opt-chip-remove';
            rm.textContent = '×';
            rm.title = `Remove ${v.label}`;
            rm.onclick = () => onRemove(v.id);
            chip.appendChild(rm);
            row.appendChild(chip);
        });
        groupEl.appendChild(row);
        groupsEl.appendChild(groupEl);
    });

    bar.appendChild(groupsEl);
    body.appendChild(bar);
}

function createOptInput({ type = 'number', value, step, min, onChange } = {}) {
    const inp = document.createElement('input');
    inp.type = type;
    inp.className = 'opt-input';
    if (step != null) inp.step = String(step);
    if (min != null) inp.min = String(min);
    if (value != null) inp.value = String(value);
    if (onChange) inp.addEventListener('change', () => onChange(inp));
    return inp;
}

function createFormField(labelText, inputEl, fullWidth = false) {
    const field = document.createElement('div');
    field.className = 'opt-field' + (fullWidth ? ' opt-field--full' : '');
    const label = document.createElement('label');
    label.textContent = labelText;
    field.appendChild(label);
    field.appendChild(inputEl);
    return field;
}

function createStageNav(buttons) {
    const nav = document.createElement('div');
    nav.className = 'opt-nav-row';
    buttons.forEach(({ label, primary, disabled, onClick }) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `btn btn--sidebar-inline ${primary ? 'btn-primary' : 'btn-secondary'}`;
        btn.textContent = label;
        btn.disabled = !!disabled;
        btn.onclick = onClick;
        nav.appendChild(btn);
    });
    return nav;
}

function appendSelectedBar(body, items, { title, onRemove, headActions }) {
    if (!items.length) return;
    const bar = document.createElement('div');
    bar.className = 'opt-selected-bar';
    const head = document.createElement('div');
    head.className = 'opt-selected-bar__head';
    const titleEl = document.createElement('span');
    titleEl.className = 'opt-field-label';
    titleEl.style.margin = '0';
    titleEl.textContent = title;
    head.appendChild(titleEl);

    if (headActions?.length) {
        const actionsWrap = document.createElement('div');
        actionsWrap.className = 'opt-selected-bar__actions';
        headActions.forEach(({ label, title: btnTitle, onClick }) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'opt-bar-action';
            btn.textContent = label;
            if (btnTitle) btn.title = btnTitle;
            btn.onclick = onClick;
            actionsWrap.appendChild(btn);
        });
        head.appendChild(actionsWrap);
    }

    const badge = document.createElement('span');
    badge.className = 'opt-badge';
    badge.textContent = String(items.length);
    head.appendChild(badge);
    bar.appendChild(head);
    const row = document.createElement('div');
    row.className = 'opt-chip-row';
    items.forEach(({ id, label, title: chipTitle, actions }) => {
        const chip = document.createElement('span');
        chip.className = 'opt-chip is-selected opt-chip--rich';
        const text = document.createElement('span');
        text.className = 'opt-chip-text';
        text.textContent = label;
        chip.appendChild(text);
        if (chipTitle) chip.title = chipTitle;

        (actions || []).forEach(({ label: actLabel, title: actTitle, className, onClick }) => {
            const act = document.createElement('button');
            act.type = 'button';
            act.className = 'opt-chip-action' + (className ? ` ${className}` : '');
            act.textContent = actLabel;
            if (actTitle) act.title = actTitle;
            act.onclick = (e) => {
                e.stopPropagation();
                onClick();
            };
            chip.appendChild(act);
        });

        const rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'opt-chip-remove';
        rm.textContent = '×';
        rm.title = 'Remove';
        rm.onclick = () => onRemove(id);
        chip.appendChild(rm);
        row.appendChild(chip);
    });
    bar.appendChild(row);
    body.appendChild(bar);
}

async function recordMeasurablesForTag(tagId) {
    await _sendCommand({ action: 'RECORD_MEASURABLES', target_id: tagId });
    await fetchLabState();
}

function objectiveTermChipActions(term) {
    if (term.field !== 'camera_image') return [];
    return [
        {
            label: 'Rec',
            title: 'Record centroid baseline for this camera',
            className: 'opt-chip-action--record',
            onClick: () => recordMeasurablesForTag(term.tag_id),
        },
    ];
}

function buildVariableSearchIndex(tagId, comp) {
    const opts = listTunableOptions(tagId, comp);
    const compLabel = componentDisplayLabel(tagId);
    return {
        tagId,
        comp,
        compLabel,
        opts,
        searchText: [compLabel, tagId, ...opts.map((o) => `${o.label} ${shortTunableLabel(o)}`)]
            .join(' ')
            .toLowerCase(),
    };
}

function applyVariableListFilter(listEl, emptyEl, query) {
    const q = query.trim().toLowerCase();
    let visibleSections = 0;
    listEl.querySelectorAll('.opt-comp-accordion').forEach((details) => {
        const compText = details.dataset.searchText || '';
        const compMatch = !q || compText.includes(q);
        let visibleChips = 0;
        details.querySelectorAll('.opt-chip').forEach((chip) => {
            const chipText = `${chip.textContent} ${chip.title || ''}`.toLowerCase();
            const show = !q || compMatch || chipText.includes(q);
            chip.hidden = !show;
            if (show) visibleChips += 1;
        });
        const showSection = !q || compMatch || visibleChips > 0;
        details.hidden = !showSection;
        if (showSection) visibleSections += 1;
    });
    if (emptyEl) emptyEl.hidden = visibleSections > 0;
}

function setBodyMode(active) {
    document.body.classList.toggle('optimization-mode-active', !!active);
    const panel = document.getElementById('workspace-panel-optimization');
    if (panel) panel.classList.toggle('optimization-mode-panel', !!active);
}

function listSensorTags() {
    const out = [];
    const comps = store.labState?.components || {};
    Object.entries(comps).forEach(([tagId, comp]) => {
        if (!comp || !isBreadboardIntent(comp)) return;
        const optimizable = getOptimizableMeasurablesForTag(tagId);
        if (!optimizable.length) return;
        const meas = comp.statecontrol?.measurables || {};
        const hasData = optimizable.some((m) => meas[m.field] != null);
        out.push({
            tagId,
            label: componentDisplayLabel(tagId),
            hasData,
            optimizable,
        });
    });
    return out.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
}


function addObjectiveTerm(tagId, field) {
    const b = builder();
    if (!tagId || !field) return;
    const term = createObjectiveTerm(tagId, field, b.objectiveTerms);
    if (!term) return;
    if (b.objectiveTerms.some((t) => t.id === term.id)) return;
    b.objectiveTerms.push(term);
    renderStageContent();
}

function removeObjectiveTerm(id) {
    const b = builder();
    b.objectiveTerms = b.objectiveTerms.filter((t) => t.id !== id);
    renderStageContent();
}

function listTunableOptions(tagId, comp) {
    const shortName = getCatalogRow(tagId)?.name || tagId;
    const options = [];
    const tun = comp?.statecontrol?.tunables || {};
    const motors = tun.nominal_motor_positions;
    if (motors && typeof motors === 'object') {
        Object.keys(motors).forEach((mid) => {
            options.push({
                id: `v_${tagId}_m${mid}`,
                tag_id: tagId,
                path: `tunables.nominal_motor_positions.${mid}`,
                label: `${shortName} · motor ${mid}`,
                physical_type: 'continuous',
            });
        });
    }
    const pose = tun.nominal_pose;
    if (pose && typeof pose === 'object') {
        ['x', 'y', 'rotation'].forEach((axis) => {
            if (axis in pose) {
                options.push({
                    id: `v_${tagId}_${axis}`,
                    tag_id: tagId,
                    path: `tunables.nominal_pose.${axis}`,
                    label: `${shortName} · pose ${axis}`,
                    physical_type: 'continuous',
                });
            }
        });
    }
    return options;
}

function addVariable(entry) {
    const b = builder();
    if (b.variables.some((v) => v.id === entry.id)) return;
    b.variables.push(entry);
    syncSolverBlocksFromVariables(b);
    renderStageContent();
}

function removeVariable(id) {
    const b = builder();
    b.variables = b.variables.filter((v) => v.id !== id);
    syncSolverBlocksFromVariables(b);
    renderStageContent();
}

export function buildPayloadFromBuilder(b) {
    if (!b.variables.length || !b.objectiveTerms.length) return null;
    const anchor = b.variables[0].tag_id;
    const terms = buildObjectiveTermsPayload(b);
    const solver = buildSolverPayload(b);
    if (!terms.length || !solver.blocks.length) return null;
    return {
        action: 'OPTIMIZE',
        target_id: anchor,
        parameters: {
            mode: 'ensemble',
            session_label: 'UI optimization mode',
            variables: b.variables.map((v) => ({
                id: v.id,
                tag_id: v.tag_id,
                path: v.path,
                kind: 'continuous',
                physical_type: v.physical_type || 'continuous',
                unit: v.path.includes('motor') ? 'deg' : 'mm',
                bounds: v.path.includes('motor')
                    ? { min: -3.0, max: 3.0 }
                    : { min: -5.0, max: 5.0 },
                delta: true,
            })),
            objective: {
                type: 'weighted_sum',
                minimize: true,
                terms,
            },
            solver,
        },
    };
}

function buildJobSubmitOptions(b) {
    const opts = {
        holder: 'ui:optimization-mode',
        initializationPolicy: 'force_reconcile',
    };
    const repo = store.control?.repoId;
    const commit = getAppliedCommitId();
    const branch = store.control?.branch || getAppliedBranch() || 'main';

    if (b.reconcileBeforeRun && repo && commit) {
        opts.snapshot = { repo_id: repo, branch, commit };
    }

    if (b.commitAfterRun && repo && !isDetached() && !isConfigViewMode()) {
        const msg =
            (b.commitMessage || '').trim() ||
            `after optimization · ${new Date().toISOString().slice(0, 16).replace('T', ' ')}`;
        opts.onSuccess = {
            commit_configuration: { repo_id: repo, branch, message: msg },
        };
    }

    return opts;
}

function capturePostCommitFromJobResult(b, job) {
    if (!job || typeof job !== 'object') return;
    b.lastJobProgress = job.progress || null;
    if (job.result?.post_commit) {
        b.lastPostCommit = job.result.post_commit;
    }
    if (job.result?.post_commit_error) {
        b.lastPostCommitError = job.result.post_commit_error;
    }
    if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
        b.postCommitFetched = true;
    }
}

/** Apply client run state after a closed-loop job is accepted. */
function markEnsembleRunStarted(command, jobId) {
    const b = builder();
    store.isOptimizing = true;
    store.ensembleLossTrace = [];
    b.runCompleted = false;
    b.viewingLastRun = false;
    b.lastSeenResultAt = null;
    b.awaitingRunResults = true;
    b.activeJobId = jobId;
    b.lastPostCommit = null;
    b.lastPostCommitError = null;
    b.postCommitFetched = false;
    b.lastJobProgress = null;
    b.maxEvalsForRun = command.parameters?.solver?.max_total_evals ?? b.maxEvals ?? 200;

    if (command.target_id) {
        store.pendingCommands.add(command.target_id);
        store.pendingActions.set(command.target_id, 'OPTIMIZE');
    }
}

async function submitEnsembleOptimizationJob(command) {
    const b = builder();
    const graph = buildAuthoringObjectiveGraph(b);
    const compileResult = await compileObjective({
        graph,
        parameters: command.parameters,
        preflight: true,
    });
    if (!compileResult.ok) {
        const err =
            compileResult.error ||
            compileResult.preflight?.message ||
            'Objective compile/preflight failed';
        return { ok: false, error: err, detail: compileResult.detail || compileResult.preflight };
    }
    if (compileResult.objective) {
        command.parameters.objective = compileResult.objective;
    }

    const result = await submitClosedLoopJob(command, buildJobSubmitOptions(b));
    if (!result.ok) {
        if (command.target_id) {
            store.pendingCommands.delete(command.target_id);
            store.pendingActions.delete(command.target_id);
        }
        return result;
    }
    const jobId = result.job?.job_id;
    if (!jobId) {
        return { ok: false, error: 'Job submit succeeded but no job_id returned' };
    }
    markEnsembleRunStarted(command, jobId);
    _log(`Optimization job submitted (${jobId.slice(0, 14)}…)`, 'info');
    void fetchLabState();
    return { ok: true, jobId, message: `Job ${jobId} queued` };
}

async function pollOptimizationJobOutcome(root) {
    const b = builder();
    const jobId = b.activeJobId || store.labState?.active_job_id;
    if (!b.awaitingRunResults || !jobId) return;

    const st = store.labState?.system_status;
    const completed = !!store.labState?.last_ensemble_optimization?.completed_at;
    if (st === 'OPTIMIZING') return;
    if (completed && b.postCommitFetched) return;

    const now = Date.now();
    if (now - _lastJobOutcomePollMs < 2000) return;
    _lastJobOutcomePollMs = now;

    try {
        const job = await fetchJob(jobId);
        capturePostCommitFromJobResult(b, job);
        if (job.status === 'failed' || job.status === 'cancelled') {
            b.awaitingRunResults = false;
            b.activeJobId = null;
            store.isOptimizing = false;
            if (b.variables[0]?.tag_id) {
                store.pendingCommands.delete(b.variables[0].tag_id);
                store.pendingActions.delete(b.variables[0].tag_id);
            }
            const msg = job.error || `Job ${job.status}`;
            _log(`Optimization job ${job.status}: ${msg}`, 'error');
            if (job.result?.post_commit_error) {
                _log(`Post-job commit failed: ${job.result.post_commit_error}`, 'warn');
            }
            updateRunStatus(root);
            refreshOptimizationVisuals();
        } else if (job.status === 'queued' || job.status === 'running') {
            updateRunStatus(root);
        } else if (job.status === 'succeeded' && completed) {
            if (job.result?.post_commit) {
                const cid = job.result.post_commit.configuration_id || '';
                _log(
                    `Post-job commit ${cid ? cid.slice(0, 8) + '…' : 'ok'}`,
                    'info',
                );
            } else if (job.result?.post_commit_error) {
                _log(`Post-job commit failed: ${job.result.post_commit_error}`, 'warn');
            }
            updateRunStatus(root);
        }
    } catch (err) {
        _log(`Job status poll failed: ${err.message}`, 'warn');
    }
}

function getEnsembleTraceFromState() {
    const sess = store.labState?.optimization_session;
    if (store.labState?.system_status === 'OPTIMIZING' && sess?.mode === 'ensemble' && Array.isArray(sess.trace)) {
        return sess.trace;
    }
    const last = store.labState?.last_ensemble_optimization;
    if (last && Array.isArray(last.trace)) return last.trace;
    return [];
}

function syncEnsembleLossTraceFromState() {
    const trace = getEnsembleTraceFromState();
    store.ensembleLossTrace = trace
        .filter((row) => row.best_loss != null && Number.isFinite(Number(row.best_loss)))
        .map((row) => ({ step: Number(row.eval), loss: Number(row.best_loss) }));
}

function getEnsembleLiveContext() {
    const b = builder();
    const sess = store.labState?.optimization_session;
    if (store.labState?.system_status === 'OPTIMIZING' && sess?.mode === 'ensemble') {
        return {
            eval: sess.eval ?? 0,
            bestLoss: sess.best_loss,
            lastEval: sess.last_eval,
            maxEvals: builder().maxEvalsForRun || builder().maxEvals || 200,
            running: true,
        };
    }
    if (b.awaitingRunResults && store.labState?.system_status !== 'OPTIMIZING') {
        return {
            eval: 0,
            bestLoss: null,
            lastEval: null,
            maxEvals: b.maxEvalsForRun || b.maxEvals || 200,
            running: true,
            queued: true,
        };
    }
    const last = store.labState?.last_ensemble_optimization;
    if (last) {
        const rows = last.trace || [];
        const tail = rows.length ? rows[rows.length - 1] : null;
        return {
            eval: last.evals ?? rows.length,
            bestLoss: last.best_loss,
            lastEval: tail,
            maxEvals: last.evals ?? rows.length,
            running: false,
            result: last,
        };
    }
    return null;
}

function renderOptimizationTelemetry(root) {
    const el = root.querySelector('#opt-mode-telemetry');
    if (!el) return;
    const ctx = getEnsembleLiveContext();
    const show =
        document.body.classList.contains('is-optimizing') ||
        document.body.classList.contains('opt-results-ready');
    if (!show || !ctx) {
        el.hidden = true;
        el.innerHTML = '';
        return;
    }
    el.hidden = false;
    syncEnsembleLossTraceFromState();

    const evalN = ctx.eval ?? 0;
    const maxEvals = Math.max(1, ctx.maxEvals ?? 200);
    const pct = Math.min(100, Math.round((evalN / maxEvals) * 100));
    const best =
        ctx.bestLoss != null && Number.isFinite(Number(ctx.bestLoss))
            ? Number(ctx.bestLoss).toFixed(4)
            : '—';
    const last = ctx.lastEval;
    const lastLoss =
        last?.loss != null && Number.isFinite(Number(last.loss))
            ? Number(last.loss).toFixed(4)
            : '—';
    const blockId = last?.block_id ? String(last.block_id) : '—';

    let termsHtml = '';
    if (last?.terms && typeof last.terms === 'object') {
        termsHtml = Object.entries(last.terms)
            .map(([id, val]) => {
                const n = Number(val);
                const shown = Number.isFinite(n) ? n.toFixed(4) : String(val);
                return `<span class="opt-telemetry-term">${id}: ${shown}</span>`;
            })
            .join('');
    }

    const rows = getEnsembleTraceFromState().slice(-12).reverse();
    const valuesSummary = formatEvalValuesSummary(last?.values, 6);
    const tableRows = rows
        .map((row) => {
            const vals = formatEvalValuesSummary(row.values, 2) || '—';
            return `<tr>
                <td>${row.eval}</td>
                <td>${Number(row.loss).toFixed(4)}</td>
                <td class="opt-telemetry-values">${vals}</td>
                <td>${row.best_loss != null ? Number(row.best_loss).toFixed(4) : '—'}</td>
            </tr>`;
        })
        .join('');

    const statusLabel = ctx.queued ? 'Job queued' : ctx.running ? 'Running COBYLA' : 'Completed';
    const showProbe = Array.isArray(last?.u) && last.u.length;

    el.innerHTML = `
        <div class="opt-telemetry-head">
            <strong>${statusLabel}</strong>
            <span>eval ${evalN}/${maxEvals} · best ${best}</span>
        </div>
        <div class="opt-telemetry-progress" title="Evaluation progress"><span style="width:${pct}%"></span></div>
        <div class="opt-telemetry-sub">Latest loss ${lastLoss} · block ${blockId}</div>
        ${valuesSummary ? `<div class="opt-telemetry-sub">Setpoints: ${valuesSummary}</div>` : ''}
        ${showProbe ? `<div class="opt-telemetry-sub">Probe u: [${last.u.map((v) => Number(v).toFixed(3)).join(', ')}]</div>` : ''}
        <div class="opt-telemetry-terms">${termsHtml || '<span style="color:#64748b">Term breakdown after first eval</span>'}</div>
        <div class="opt-telemetry-table-wrap">
            <table class="opt-telemetry-table">
                <thead><tr><th>Eval</th><th>Loss</th><th>Variables</th><th>Best</th></tr></thead>
                <tbody>${tableRows || '<tr><td colspan="4" style="color:#64748b">Waiting for iterations…</td></tr>'}</tbody>
            </table>
        </div>
    `;
}

function maybeAdvanceToResults(root) {
    const b = builder();
    const last = store.labState?.last_ensemble_optimization;
    if (!b.active || !b.awaitingRunResults || !last?.completed_at) return;
    if (b.lastSeenResultAt === last.completed_at) return;

    const jobId = b.activeJobId;
    if (jobId && !b.postCommitFetched) {
        void fetchJob(jobId)
            .then((job) => {
                capturePostCommitFromJobResult(b, job);
                finishAdvanceToResults(root, b, last);
            })
            .catch(() => {
                finishAdvanceToResults(root, b, last);
            });
        return;
    }

    finishAdvanceToResults(root, b, last);
}

function finishAdvanceToResults(root, b, last) {
    if (b.lastSeenResultAt === last.completed_at) return;
    b.lastSeenResultAt = last.completed_at;
    b.runCompleted = true;
    b.awaitingRunResults = false;
    b.activeJobId = null;
    b.viewingLastRun = false;
    syncEnsembleLossTraceFromState();
    if (b.stage !== 6) {
        b.stage = 6;
        renderAll(root);
    } else {
        updateOptimizationModeLive(root);
    }
}

function enterOptimizationMode(root, { viewingLastRun = false } = {}) {
    const b = builder();
    const last = store.labState?.last_ensemble_optimization;
    b.active = true;
    b.awaitingRunResults = false;
    b.viewingLastRun = !!viewingLastRun;
    b.runCompleted = !!viewingLastRun;
    b.lastSeenResultAt = last?.completed_at ?? null;
    if (viewingLastRun && last) {
        b.stage = 6;
        syncEnsembleLossTraceFromState();
        _log('Showing last optimization results.', 'info');
    } else {
        b.stage = 1;
        b.variables = [];
        b.objectiveTerms = [];
        b.solverBlocks = [];
        b.variableSearchQuery = '';
        _log('Entered optimization mode — stage 1: define objective terms.', 'info');
    }
    renderAll(root);
    refreshOptimizationVisuals();
}

function exitOptimizationMode(root) {
    store.optimizationBuilder = createOptimizationBuilder();
    setBodyMode(false);
    renderAll(root);
    refreshOptimizationVisuals();
    _log('Exited optimization mode.', 'info');
}

function renderIdleLastRunCard(root) {
    const idle = root.querySelector('#opt-mode-idle');
    if (!idle) return;
    const last = store.labState?.last_ensemble_optimization;
    let card = idle.querySelector('#opt-mode-last-run');
    if (!last) {
        card?.remove();
        return;
    }
    if (!card) {
        card = document.createElement('div');
        card.id = 'opt-mode-last-run';
        card.className = 'opt-card opt-last-run-card';
        const enterWrap = idle.querySelector('.opt-idle-actions');
        if (enterWrap) idle.insertBefore(card, enterWrap);
        else idle.appendChild(card);
    }
    const loss = Number(last.best_loss).toFixed(4);
    const evals = last.evals ?? last.trace?.length ?? 0;
    const when = last.completed_at ? new Date(last.completed_at).toLocaleString() : '';
    card.innerHTML = `
        <div class="opt-card__title">Last run saved</div>
        <div class="opt-card__meta">Best loss ${loss} · ${evals} evals${when ? ` · ${when}` : ''}</div>
        <button type="button" class="btn btn-secondary btn--sidebar-inline opt-view-last-btn" style="margin-top:8px;width:100%;">
            View last results
        </button>
    `;
    card.querySelector('.opt-view-last-btn').onclick = () => {
        enterOptimizationMode(root, { viewingLastRun: true });
    };
}

function renderLossChart(canvas) {
    if (!canvas) return;
    const showChart =
        document.body.classList.contains('is-optimizing') ||
        document.body.classList.contains('opt-results-ready');
    if (!showChart) {
        canvas.hidden = true;
        return;
    }
    canvas.hidden = false;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    const trace = store.ensembleLossTrace || [];
    ctx.fillStyle = '#0f1115';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#2a2e36';
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px Inter, sans-serif';
    ctx.fillText('Loss (best so far)', 8, 16);
    if (trace.length < 2) {
        ctx.fillStyle = '#64748b';
        ctx.font = '10px Inter, sans-serif';
        ctx.fillText('Chart fills during session', 8, h / 2);
        return;
    }
    const pad = { l: 8, r: 8, t: 24, b: 18 };
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    const losses = trace.map((p) => p.loss);
    const minL = Math.min(...losses);
    const maxL = Math.max(...losses, minL + 1e-6);
    const maxStep = Math.max(...trace.map((p) => p.step), 1);
    ctx.beginPath();
    ctx.strokeStyle = '#a78bfa';
    ctx.lineWidth = 2;
    trace.forEach((pt, i) => {
        const x = pad.l + (pt.step / maxStep) * plotW;
        const y = pad.t + plotH - ((pt.loss - minL) / (maxL - minL)) * plotH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();
}

function renderStageNav(root) {
    const b = builder();
    const nav = root.querySelector('#opt-mode-stage-nav');
    if (!nav) return;
    nav.innerHTML = '';
    OPTIMIZATION_STAGES.forEach((st) => {
        const resultsAvailable = b.runCompleted || b.viewingLastRun;
        if (st.id === 6 && !resultsAvailable) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        const done = st.id < b.stage || (st.id === 6 && resultsAvailable);
        btn.className =
            'opt-stage-tab' +
            (b.stage === st.id ? ' is-active' : '') +
            (done && b.stage !== st.id ? ' is-done' : '');
        btn.disabled = !b.active;
        btn.innerHTML = `<span class="opt-step-num">${st.id}</span><span class="opt-step-label">${st.title}</span>`;
        btn.title = st.title;
        btn.onclick = () => {
            b.stage = st.id;
            renderAll(root);
        };
        nav.appendChild(btn);
    });
}

function renderStageContent() {
    const root = document.getElementById('optimization-mode-section');
    if (!root) return;
    renderAll(root);
}

function syncOptimizationShell(root) {
    const b = builder();
    const idle = root.querySelector('#opt-mode-idle');
    const active = root.querySelector('#opt-mode-active');
    const enterBtn = root.querySelector('#opt-mode-enter-btn');
    const exitBtn = root.querySelector('#opt-mode-exit-btn');

    if (idle) idle.hidden = b.active;
    if (active) active.hidden = !b.active;
    if (enterBtn) enterBtn.hidden = b.active;
    if (exitBtn) exitBtn.hidden = !b.active;

    const legend = root.querySelector('#opt-mode-plan-legend');
    if (legend) legend.hidden = !b.active;

    setBodyMode(b.active);
}

/** Poll-safe: chart + status only — never destroys open selects/checkboxes. */
function updateOptimizationModeLive(root) {
    if (!root) return;
    syncOptimizationShell(root);
    syncOptimizationRuntimeChrome();
    syncEnsembleLossTraceFromState();
    renderLossChart(root.querySelector('#opt-mode-loss-chart'));
    renderOptimizationTelemetry(root);
    updateRunStatus(root);
    maybeAdvanceToResults(root);
    void pollOptimizationJobOutcome(root);
}

function renderAll(root) {
    const b = builder();
    syncOptimizationShell(root);
    renderStageNav(root);

    const hint = root.querySelector('#opt-mode-stage-hint');
    const stageDef = OPTIMIZATION_STAGES.find((s) => s.id === b.stage);
    if (hint && stageDef) hint.textContent = stageDef.hint;

    const body = root.querySelector('#opt-mode-stage-body');
    if (!body || !b.active) {
        if (!b.active) renderIdleLastRunCard(root);
        updateOptimizationModeLive(root);
        return;
    }
    body.innerHTML = '';

    if (b.stage === 1) renderStageObjective(body);
    else if (b.stage === 2) renderStageVariables(body);
    else if (b.stage === 3) renderStageTune(body);
    else if (b.stage === 4) renderStageSolver(body);
    else if (b.stage === 5) renderStageRun(body);
    else if (b.stage === 6) renderStageResults(body);

    body.dataset.renderedStage = String(b.stage);
    updateOptimizationModeLive(root);
    refreshOptimizationVisuals();
}

function renderStageObjective(body) {
    const b = builder();
    b.objectiveTerms = sanitizeObjectiveTerms(b.objectiveTerms);
    const sensors = listSensorTags();

    appendSelectedBar(
        body,
        b.objectiveTerms.map((t) => ({
            id: t.id,
            label: t.label,
            actions: objectiveTermChipActions(t),
        })),
        {
            title: 'Objective terms',
            onRemove: removeObjectiveTerm,
            headActions: b.objectiveTerms.some((t) => t.field === 'camera_image')
                ? [
                      {
                          label: 'Record all',
                          title: 'Record centroid baselines for all camera terms',
                          onClick: async () => {
                              const tags = [
                                  ...new Set(
                                      b.objectiveTerms
                                          .filter((t) => t.field === 'camera_image')
                                          .map((t) => t.tag_id),
                                  ),
                              ];
                              for (const tagId of tags) {
                                  await recordMeasurablesForTag(tagId);
                              }
                              _log(`Recorded measurables for ${tags.length} camera(s).`, 'info');
                          },
                      },
                  ]
                : undefined,
        },
    );

    if (sensors.length) {
        const addCard = document.createElement('div');
        addCard.className = 'opt-card';
        addCard.innerHTML = '<div class="opt-card__title">Add term</div>';

        const tagSel = document.createElement('select');
        tagSel.className = 'opt-select';
        tagSel.innerHTML = '<option value="">Select component…</option>';
        sensors.forEach(({ tagId, label }) => {
            const opt = document.createElement('option');
            opt.value = tagId;
            opt.textContent = label;
            tagSel.appendChild(opt);
        });
        addCard.appendChild(createFormField('Component', tagSel, true));

        const fieldSel = document.createElement('select');
        fieldSel.className = 'opt-select';
        fieldSel.innerHTML = '<option value="">Select measurable…</option>';
        addCard.appendChild(createFormField('Measurable', fieldSel, true));

        tagSel.onchange = () => {
            fieldSel.innerHTML = '<option value="">Select measurable…</option>';
            const tagId = tagSel.value;
            if (!tagId) return;
            getOptimizableMeasurablesForTag(tagId).forEach((m) => {
                const opt = document.createElement('option');
                opt.value = m.field;
                opt.textContent = m.label;
                fieldSel.appendChild(opt);
            });
        };

        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn btn-secondary btn--sidebar-inline';
        addBtn.style.marginTop = '8px';
        addBtn.textContent = 'Add to objective';
        addBtn.onclick = () => addObjectiveTerm(tagSel.value, fieldSel.value);
        addCard.appendChild(addBtn);
        body.appendChild(addCard);
    } else {
        const empty = document.createElement('p');
        empty.className = 'opt-hint';
        empty.textContent = 'No placed components with camera or power measurables.';
        body.appendChild(empty);
    }

    if (store.labState?.lab_mode === 'MOCK') {
        const note = document.createElement('p');
        note.className = 'opt-hint';
        note.textContent = 'Mock runs use a synthetic landscape; terms here define the payload sent to the solver.';
        body.appendChild(note);
    }

    body.appendChild(
        createStageNav([
            {
                label: 'Next: Variables →',
                primary: true,
                disabled: !b.objectiveTerms.length,
                onClick: () => {
                    b.stage = 2;
                    renderStageContent();
                },
            },
        ]),
    );
}

function renderStageVariables(body) {
    const b = builder();

    appendSelectedVariablesBar(body, b.variables, removeVariable);

    const searchWrap = document.createElement('div');
    searchWrap.className = 'opt-search-wrap';
    const searchIcon = document.createElement('span');
    searchIcon.className = 'material-icons-round opt-search-icon';
    searchIcon.textContent = 'search';
    searchWrap.appendChild(searchIcon);
    const searchInp = document.createElement('input');
    searchInp.type = 'search';
    searchInp.className = 'opt-input opt-search';
    searchInp.placeholder = 'Filter components or tunables…';
    searchInp.value = b.variableSearchQuery || '';
    searchInp.autocomplete = 'off';
    searchWrap.appendChild(searchInp);
    body.appendChild(searchWrap);

    const list = document.createElement('div');
    list.className = 'opt-comp-list';
    list.id = 'opt-var-comp-list';

    const entries = [];
    const comps = store.labState?.components || {};
    Object.entries(comps).forEach(([tagId, comp]) => {
        if (!comp || !isBreadboardIntent(comp)) return;
        const index = buildVariableSearchIndex(tagId, comp);
        if (!index.opts.length) return;
        entries.push(index);
    });
    entries.sort((a, b) => a.compLabel.localeCompare(b.compLabel, undefined, { numeric: true }));

    entries.forEach(({ tagId, comp, compLabel, opts, searchText }) => {
        const selectedCount = opts.filter((o) => b.variables.some((v) => v.id === o.id)).length;
        const details = document.createElement('details');
        details.className = 'opt-comp-accordion';
        details.dataset.searchText = searchText;
        if (selectedCount > 0 || (b.variableSearchQuery && searchText.includes(b.variableSearchQuery.trim().toLowerCase()))) {
            details.open = true;
        }

        const summary = document.createElement('summary');
        summary.innerHTML = `<span>${compLabel}</span><span class="opt-badge">${selectedCount}/${opts.length}</span>`;
        details.appendChild(summary);

        const inner = document.createElement('div');
        inner.className = 'opt-comp-accordion__body';
        const chipRow = document.createElement('div');
        chipRow.className = 'opt-chip-row';
        opts.forEach((opt) => {
            const added = b.variables.some((v) => v.id === opt.id);
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'opt-chip' + (added ? ' is-selected' : '');
            chip.textContent = shortTunableLabel(opt);
            chip.disabled = added;
            chip.title = opt.label;
            chip.onclick = () => addVariable(opt);
            chipRow.appendChild(chip);
        });
        inner.appendChild(chipRow);
        details.appendChild(inner);
        list.appendChild(details);
    });

    const emptyFilter = document.createElement('p');
    emptyFilter.className = 'opt-hint opt-filter-empty';
    emptyFilter.hidden = true;
    emptyFilter.textContent = 'No components match this filter.';
    body.appendChild(list);
    body.appendChild(emptyFilter);

    searchInp.addEventListener('input', () => {
        b.variableSearchQuery = searchInp.value;
        applyVariableListFilter(list, emptyFilter, searchInp.value);
    });
    applyVariableListFilter(list, emptyFilter, searchInp.value);

    body.appendChild(
        createStageNav([
            {
                label: '← Objective',
                onClick: () => {
                    b.stage = 1;
                    renderStageContent();
                },
            },
            {
                label: 'Next: Tune →',
                primary: true,
                disabled: !b.variables.length,
                onClick: () => {
                    syncSolverBlocksFromVariables(b);
                    b.stage = 3;
                    renderStageContent();
                },
            },
        ]),
    );
}

function renderStageTune(body) {
    const b = builder();

    b.objectiveTerms.forEach((term) => {
        const card = document.createElement('div');
        card.className = 'opt-card';
        const title = document.createElement('div');
        title.className = 'opt-card__title';
        title.textContent = term.label;
        card.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'opt-form-grid';

        const wIn = createOptInput({
            value: term.weight,
            step: 0.05,
            min: 0,
            onChange: (inp) => {
                term.weight = parseFloat(inp.value) || 0;
            },
        });
        grid.appendChild(createFormField('Weight', wIn));

        if (term.field === 'camera_image') {
            ['x', 'y'].forEach((axis) => {
                const inp = createOptInput({
                    value: term.centroidTarget?.[axis] ?? 0,
                    onChange: (el) => {
                        if (!term.centroidTarget) term.centroidTarget = { x: 512, y: 384 };
                        term.centroidTarget[axis] = parseFloat(el.value) || 0;
                    },
                });
                grid.appendChild(createFormField(`Target ${axis.toUpperCase()}`, inp));
            });
        } else {
            ['Min', 'Max'].forEach((bound, i) => {
                const key = i === 0 ? 'normalizeMin' : 'normalizeMax';
                const inp = createOptInput({
                    value: term[key] ?? 0,
                    step: 0.01,
                    onChange: (el) => {
                        term[key] = parseFloat(el.value) || 0;
                    },
                });
                grid.appendChild(createFormField(`Normalize ${bound}`, inp));
            });
        }
        card.appendChild(grid);

        const meta = document.createElement('div');
        meta.className = 'opt-card__meta';
        meta.textContent = `Metric: ${term.metric}`;
        card.appendChild(meta);
        body.appendChild(card);
    });

    body.appendChild(
        createStageNav([
            {
                label: '← Variables',
                onClick: () => {
                    b.stage = 2;
                    renderStageContent();
                },
            },
            {
                label: 'Next: Solver →',
                primary: true,
                disabled: !b.objectiveTerms.length,
                onClick: () => {
                    syncSolverBlocksFromVariables(b);
                    b.stage = 4;
                    renderStageContent();
                },
            },
        ]),
    );
}

function renderStageSolver(body) {
    const b = builder();
    syncSolverBlocksFromVariables(b);

    const budgetCard = document.createElement('div');
    budgetCard.className = 'opt-card';
    budgetCard.innerHTML = '<div class="opt-card__title">Session budget</div>';
    const budgetGrid = document.createElement('div');
    budgetGrid.className = 'opt-form-grid';

    budgetGrid.appendChild(
        createFormField(
            'Max total evals',
            createOptInput({
                value: b.maxEvals,
                min: 10,
                onChange: (inp) => {
                    b.maxEvals = parseInt(inp.value, 10) || 200;
                    syncSolverBlocksFromVariables(b);
                },
            }),
        ),
    );
    budgetGrid.appendChild(
        createFormField(
            'Settle (ms)',
            createOptInput({
                value: b.settleMs,
                min: 0,
                onChange: (inp) => {
                    b.settleMs = parseInt(inp.value, 10) || 0;
                },
            }),
        ),
    );
    budgetCard.appendChild(budgetGrid);
    body.appendChild(budgetCard);

    b.solverBlocks.forEach((block) => {
        const card = document.createElement('div');
        card.className = 'opt-card';
        card.innerHTML = `<div class="opt-card__title">Block ${block.id}</div>`;

        const varHint = document.createElement('div');
        varHint.className = 'opt-card__meta';
        varHint.textContent = `${block.variable_ids.length} variable(s) in this block`;
        card.appendChild(varHint);

        const grid = document.createElement('div');
        grid.className = 'opt-form-grid';
        [
            ['max_evals', 'Max evals', block.max_evals, 1],
            ['passes', 'Passes', block.passes, 1],
            ['rhobeg_u', 'Trust start', block.rhobeg_u, 0.001],
            ['rhoend_u', 'Trust end', block.rhoend_u, 0.001],
        ].forEach(([key, label, val, step]) => {
            grid.appendChild(
                createFormField(
                    label,
                    createOptInput({
                        value: val,
                        step,
                        onChange: (inp) => {
                            block[key] = parseFloat(inp.value) || 0;
                        },
                    }),
                ),
            );
        });
        card.appendChild(grid);
        body.appendChild(card);
    });

    const addBlock = document.createElement('button');
    addBlock.type = 'button';
    addBlock.className = 'btn btn-secondary btn--sidebar-inline';
    addBlock.textContent = '+ Split into another block';
    addBlock.onclick = () => {
        const used = new Set(b.solverBlocks.flatMap((bl) => bl.variable_ids));
        const free = b.variables.map((v) => v.id).filter((id) => !used.has(id));
        if (!free.length) {
            _log('All variables are already assigned to blocks.', 'warn');
            return;
        }
        b.solverBlocks.push({
            ...createDefaultSolverBlock(free, b.maxEvals),
            id: `block_${b.solverBlocks.length + 1}`,
        });
        renderStageContent();
    };
    body.appendChild(addBlock);

    body.appendChild(
        createStageNav([
            {
                label: '← Tune',
                onClick: () => {
                    b.stage = 3;
                    renderStageContent();
                },
            },
            {
                label: 'Next: Run →',
                primary: true,
                disabled: !b.solverBlocks.length,
                onClick: () => {
                    b.stage = 5;
                    renderStageContent();
                },
            },
        ]),
    );
}

function renderStageRun(body) {
    const b = builder();
    const payload = buildPayloadFromBuilder(b);

    const stats = document.createElement('div');
    stats.className = 'opt-run-stats';
    if (payload) {
        stats.innerHTML = `
            <div class="opt-stat"><span class="opt-stat-val">${b.variables.length}</span><span class="opt-stat-label">Variables</span></div>
            <div class="opt-stat"><span class="opt-stat-val">${b.objectiveTerms.length}</span><span class="opt-stat-label">Terms</span></div>
            <div class="opt-stat"><span class="opt-stat-val">${b.solverBlocks.length}</span><span class="opt-stat-label">Blocks</span></div>
            <div class="opt-stat"><span class="opt-stat-val">${b.maxEvals}</span><span class="opt-stat-label">Max evals</span></div>
        `;
    } else {
        stats.innerHTML =
            '<p class="opt-hint" style="grid-column:1/-1;margin:0">Incomplete plan — fill earlier stages before running.</p>';
    }
    body.appendChild(stats);

    const repo = store.control?.repoId;
    const commit = getAppliedCommitId();
    const canCommit = Boolean(repo) && !isDetached() && !isConfigViewMode();
    const vcSection = document.createElement('div');
    vcSection.className = 'opt-job-options';

    if (!repo) {
        const hint = document.createElement('p');
        hint.className = 'opt-hint';
        hint.textContent =
            'No configuration repo — enable local VC to reconcile before run or commit after success.';
        vcSection.appendChild(hint);
    } else {
        const reconcileLabel = document.createElement('label');
        reconcileLabel.className = 'opt-job-option';
        const reconcileCb = document.createElement('input');
        reconcileCb.type = 'checkbox';
        reconcileCb.checked = b.reconcileBeforeRun;
        reconcileCb.disabled = !commit;
        reconcileCb.addEventListener('change', () => {
            b.reconcileBeforeRun = reconcileCb.checked;
        });
        reconcileLabel.appendChild(reconcileCb);
        reconcileLabel.appendChild(
            document.createTextNode(
                commit
                    ? ` Reconcile to current commit (${commit.slice(0, 8)}…) before run`
                    : ' Reconcile before run (set a current commit first)',
            ),
        );
        vcSection.appendChild(reconcileLabel);

        const commitLabel = document.createElement('label');
        commitLabel.className = 'opt-job-option';
        const commitCb = document.createElement('input');
        commitCb.type = 'checkbox';
        commitCb.checked = b.commitAfterRun;
        commitCb.disabled = !canCommit;
        commitCb.addEventListener('change', () => {
            b.commitAfterRun = commitCb.checked;
            msgWrap.hidden = !commitCb.checked;
        });
        commitLabel.appendChild(commitCb);
        let commitHint = ' Commit configuration after successful run';
        if (isDetached()) commitHint += ' (fork first — detached commit)';
        else if (isConfigViewMode()) commitHint += ' (exit preview first)';
        commitLabel.appendChild(document.createTextNode(commitHint));
        vcSection.appendChild(commitLabel);

        const msgWrap = document.createElement('div');
        msgWrap.className = 'opt-job-commit-msg';
        msgWrap.hidden = !b.commitAfterRun;
        const msgInput = document.createElement('input');
        msgInput.type = 'text';
        msgInput.className = 'opt-job-commit-input';
        msgInput.placeholder = 'Commit message (optional)';
        msgInput.value = b.commitMessage || '';
        msgInput.disabled = !canCommit;
        msgInput.addEventListener('input', () => {
            b.commitMessage = msgInput.value;
        });
        msgWrap.appendChild(msgInput);
        vcSection.appendChild(msgWrap);
    }
    body.appendChild(vcSection);

    body.appendChild(
        createStageNav([
            {
                label: '← Solver',
                onClick: () => {
                    b.stage = 4;
                    renderStageContent();
                },
            },
            {
                label: 'Start optimization',
                primary: true,
                disabled: !payload || store.labState?.system_status === 'OPTIMIZING' || builder().awaitingRunResults,
                onClick: async () => {
                    const blocked = runtimeEditableOrMessage();
                    if (blocked) {
                        _log(blocked, 'warn');
                        return;
                    }
                    b.runCompleted = false;
                    b.viewingLastRun = false;
                    b.lastSeenResultAt = null;
                    b.awaitingRunResults = true;
                    b.activeJobId = null;
                    b.lastPostCommit = null;
                    b.lastPostCommitError = null;
                    b.postCommitFetched = false;
                    b.lastJobProgress = null;
                    b.maxEvalsForRun = b.maxEvals || 200;
                    store.ensembleLossTrace = [];
                    scrollLivePanelIntoView();
                    _log('Submitting optimization job…', 'info');
                    const result = await submitEnsembleOptimizationJob(payload);
                    if (!result.ok) _log(`Failed: ${result.error}`, 'error');
                    else void fetchLabState();
                },
            },
        ]),
    );
}

function labelForVariableId(id) {
    const v = builder().variables.find((x) => x.id === id);
    return v?.label || id;
}

function formatEvalValuesSummary(values, maxItems = 5) {
    if (!values || typeof values !== 'object') return '';
    const b = builder();
    return Object.entries(values)
        .slice(0, maxItems)
        .map(([id, val]) => {
            const v = b.variables.find((x) => x.id === id);
            const name = v ? shortTunableLabel(v) : id;
            const tag = v ? componentDisplayLabel(v.tag_id).split(' (')[0] : '';
            const n = Number(val);
            const shown = Number.isFinite(n) ? n.toFixed(2) : String(val);
            return tag ? `${tag}·${name}=${shown}` : `${name}=${shown}`;
        })
        .join(' · ');
}

function scrollLivePanelIntoView() {
    document.getElementById('opt-mode-live-panel')?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
    });
}

function renderStageResults(body) {
    const b = builder();
    const last = store.labState?.last_ensemble_optimization;
    if (!last) {
        const p = document.createElement('p');
        p.className = 'opt-hint';
        p.textContent = 'No results yet. Run optimization from stage 5.';
        body.appendChild(p);
        return;
    }

    const headline = document.createElement('div');
    headline.className = 'opt-results-grid';
    headline.innerHTML = `
        <div class="opt-results-card">
            <h4>Best loss</h4>
            <div class="opt-stat-val opt-stat-val--success">${Number(last.best_loss).toFixed(4)}</div>
        </div>
        <div class="opt-results-card">
            <h4>Evaluations</h4>
            <div class="opt-stat-val">${last.evals ?? last.trace?.length ?? 0}</div>
        </div>
    `;
    body.appendChild(headline);

    const tail = last.trace?.length ? last.trace[last.trace.length - 1] : null;
    if (tail?.terms && typeof tail.terms === 'object') {
        const termsCard = document.createElement('div');
        termsCard.className = 'opt-results-card';
        termsCard.innerHTML = '<h4>Final term contributions</h4>';
        const chips = document.createElement('div');
        chips.className = 'opt-telemetry-terms';
        Object.entries(tail.terms).forEach(([id, val]) => {
            const n = Number(val);
            const shown = Number.isFinite(n) ? n.toFixed(4) : String(val);
            const chip = document.createElement('span');
            chip.className = 'opt-telemetry-term';
            chip.textContent = `${id}: ${shown}`;
            chips.appendChild(chip);
        });
        termsCard.appendChild(chips);
        body.appendChild(termsCard);
    }

    const vals = last.final_values || {};
    const valKeys = Object.keys(vals);
    if (valKeys.length) {
        const setCard = document.createElement('div');
        setCard.className = 'opt-results-card';
        setCard.innerHTML = '<h4>Applied setpoints</h4>';
        const table = document.createElement('table');
        table.className = 'opt-telemetry-table';
        table.innerHTML = '<thead><tr><th>Variable</th><th>Value</th></tr></thead>';
        const tbody = document.createElement('tbody');
        valKeys.forEach((id) => {
            const tr = document.createElement('tr');
            const n = Number(vals[id]);
            tr.innerHTML = `<td>${labelForVariableId(id)}</td><td>${Number.isFinite(n) ? n.toFixed(4) : vals[id]}</td>`;
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        setCard.appendChild(table);
        body.appendChild(setCard);
    }

    if (b.lastPostCommit || b.lastPostCommitError) {
        const commitCard = document.createElement('div');
        commitCard.className = 'opt-results-card';
        if (b.lastPostCommit) {
            const cid = b.lastPostCommit.configuration_id || '';
            commitCard.innerHTML = `
                <h4>Configuration committed</h4>
                <p class="opt-hint">${b.lastPostCommit.message || 'Post-job commit'}</p>
                ${cid ? `<code class="opt-commit-id">${cid.slice(0, 12)}…</code>` : ''}
            `;
        } else {
            commitCard.innerHTML = `
                <h4>Commit failed</h4>
                <p class="opt-hint opt-hint--warn">${b.lastPostCommitError}</p>
            `;
        }
        body.appendChild(commitCard);
    }

    const hint = document.createElement('p');
    hint.className = 'opt-hint';
    hint.textContent = 'Live trace stays in the panel above. Canvas setpoints reflect final values.';
    body.appendChild(hint);

    body.appendChild(
        createStageNav([
            {
                label: 'Run again',
                primary: true,
                onClick: () => {
                    b.runCompleted = false;
                    b.viewingLastRun = false;
                    b.awaitingRunResults = false;
                    b.lastSeenResultAt = store.labState?.last_ensemble_optimization?.completed_at ?? null;
                    b.stage = 5;
                    renderStageContent();
                },
            },
            {
                label: '← Edit plan',
                onClick: () => {
                    b.viewingLastRun = false;
                    b.runCompleted = false;
                    b.stage = 1;
                    renderStageContent();
                },
            },
        ]),
    );
}

function updateRunStatus(root) {
    const el = root.querySelector('#opt-mode-run-status');
    if (!el) return;
    const b = builder();
    const showStatus =
        document.body.classList.contains('is-optimizing') ||
        document.body.classList.contains('opt-results-ready');
    if (!showStatus) {
        el.hidden = true;
        el.textContent = '';
        el.className = 'opt-run-status';
        return;
    }
    el.hidden = false;
    const st = store.labState?.system_status;
    const sess = store.labState?.optimization_session;
    const last = store.labState?.last_ensemble_optimization;
    if (st === 'OPTIMIZING' && sess?.mode === 'ensemble') {
        el.className = 'opt-run-status opt-run-status--running';
        const jobBit = b.activeJobId ? ` · ${b.activeJobId.slice(0, 12)}…` : '';
        el.textContent = `Running · eval ${sess.eval ?? 0} · best ${Number(sess.best_loss).toFixed(4)}${jobBit}`;
    } else if (b.lastJobProgress?.phase === 'post_commit') {
        el.className = 'opt-run-status opt-run-status--running';
        el.textContent = b.lastJobProgress.message || 'Committing configuration…';
    } else if (b.lastJobProgress?.phase === 'init') {
        el.className = 'opt-run-status opt-run-status--running';
        el.textContent = b.lastJobProgress.message || 'Reconciling before run…';
    } else if (b.awaitingRunResults && (b.activeJobId || store.labState?.active_job_id)) {
        el.className = 'opt-run-status opt-run-status--running';
        const jid = b.activeJobId || store.labState?.active_job_id || '';
        if (jid) {
            el.innerHTML = `Job queued · <code>${jid.slice(0, 14)}…</code> · <a href="/operations" target="_blank" rel="noopener" style="color:#93c5fd">operations</a>`;
        } else {
            el.textContent = 'Job queued…';
        }
    } else if (last) {
        el.className = 'opt-run-status opt-run-status--done';
        let text = `Complete · ${last.evals ?? 0} evals · best ${Number(last.best_loss).toFixed(4)}`;
        if (b.lastPostCommit) {
            const cid = b.lastPostCommit.configuration_id || '';
            text += cid ? ` · commit ${cid.slice(0, 8)}…` : ' · committed';
        } else if (b.lastPostCommitError) {
            text += ' · commit failed';
        }
        el.textContent = text;
    } else {
        el.textContent = '';
        el.hidden = true;
    }
}

/** Called from component panels in stage 2. */
export function renderOptimizationVariablePicker(container, tagId, comp) {
    container.innerHTML = '';
    const b = builder();
    if (!b.active || b.stage !== 2) return;

    const wrap = document.createElement('div');
    wrap.className = 'opt-panel-picker';
    wrap.innerHTML = '<div class="opt-panel-picker-title">Add variables</div>';
    const chipRow = document.createElement('div');
    chipRow.className = 'opt-chip-row';
    listTunableOptions(tagId, comp).forEach((opt) => {
        const added = b.variables.some((v) => v.id === opt.id);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'opt-chip' + (added ? ' is-selected' : '');
        chip.textContent = shortTunableLabel(opt);
        chip.disabled = added;
        chip.title = opt.label;
        chip.onclick = () => {
            addVariable(opt);
            renderOptimizationVariablePicker(container, tagId, comp);
        };
        chipRow.appendChild(chip);
    });
    wrap.appendChild(chipRow);
    container.appendChild(wrap);
}

export function isOptimizationVariableStage() {
    const b = store.optimizationBuilder;
    return !!(b && b.active && b.stage === 2);
}

export function updateOptimizationModeUi() {
    const root = document.getElementById('optimization-mode-section');
    if (!root) return;
    // Lab state polls every 500ms — only refresh live telemetry, not the staged form.
    updateOptimizationModeLive(root);
}

export function initOptimizationMode(deps) {
    if (deps?.sendCommand) _sendCommand = deps.sendCommand;
    if (deps?.log) _log = deps.log;
    store.optimizationBuilder = createOptimizationBuilder();

    const root = document.getElementById('optimization-mode-section');
    if (!root) return;

    root.querySelector('#opt-mode-enter-btn')?.addEventListener('click', () => {
        enterOptimizationMode(root, { viewingLastRun: false });
    });

    root.querySelector('#opt-mode-exit-btn')?.addEventListener('click', () => {
        exitOptimizationMode(root);
    });

    renderAll(root);
}

/** @deprecated use updateOptimizationModeUi */
export function updateEnsembleSessionUi() {
    updateOptimizationModeUi();
}

/** @deprecated use initOptimizationMode */
export function initEnsembleSession(deps) {
    initOptimizationMode(deps);
}

export { buildPayloadFromBuilder as buildEnsemblePayload };

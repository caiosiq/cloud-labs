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
    EDGE_OBJECTIVE_PRESETS,
    componentDisplayLabel,
    getOptimizableMeasurablesForTag,
    createEdgeKernelObjectiveTerm,
    createDefaultSolverBlock,
    sanitizeObjectiveTerms,
    syncSolverBlocksFromVariables,
    buildObjectiveTermsPayload,
    buildSolverPayload,
    buildAuthoringObjectiveGraph,
    buildRunPlanSummary,
    isOptimizationPlanningActive,
    cameraCenterDefaultHint,
    defaultCentroidTargetForCamera,
    enrichVariableSearchBounds,
    syncMaxDeltaWithVariableBounds,
    approxInitialStepPhysical,
    variableSearchSpan,
} from '../state/optimization-builder.js';
import { executeSendCommand } from '../api/commands.js';
import { fetchJob, submitClosedLoopJob, acceptJob } from '../api/jobs.js';
import { compileObjective, fetchEdgeKernels } from '../api/optimization.js';
import { fetchLabState } from '../state/lab-state.js';
import { isLiveFeedActive, measurableValue } from '../component-state.js';
import { render } from '../canvas/render.js';
import { syncComponentSidebarHighlights } from './updateUI.js';
import { syncBenchChromeHighlights } from './bench-chrome-bar.js';
import { withBackendQuery, getSelectedBackendId } from '../state/backend-selection.js';
import { attachLossChart, lossPointsFromTrace } from '../optimize-session/loss-chart.js';

function optimizeSessionHref({ jobId = '', maxEvals = null } = {}) {
    const params = new URLSearchParams();
    const backendId = getSelectedBackendId();
    if (backendId) params.set('backend_id', backendId);
    if (jobId) params.set('job_id', jobId);
    if (maxEvals != null && Number.isFinite(Number(maxEvals))) {
        params.set('max_evals', String(maxEvals));
    }
    const q = params.toString();
    return q ? `/optimize-session?${q}` : '/optimize-session';
}

export { isOptimizationPlanningActive };

let _sendCommand = async (cmd) => executeSendCommand(cmd);
let _log = () => {};
let _lastJobOutcomePollMs = 0;
let _optAcceptInFlight = false;
let _optTelemetrySig = '';
let _optLiveDelegatesBound = false;
/** @type {ReturnType<typeof attachLossChart>|null} */
let _optLossChart = null;

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

/**
 * Per-variable search box (pipeline ``bounds`` + ``delta``).
 * Physical window the solver may explore; ``rhobeg`` is a fraction of its span.
 */
function appendVariableSearchBoundsEditor(body, b) {
    if (!b.variables?.length) return;
    const card = document.createElement('div');
    card.className = 'opt-card';
    card.style.marginTop = '10px';
    card.innerHTML =
        '<div class="opt-card__title">Search bounds</div>' +
        '<p class="opt-hint" style="margin:0 0 8px">How far the solver may move each tunable. ' +
        'Relative = offset from start; Absolute = lab angles/positions. ' +
        'COBYLA initial step ≈ <code>rhobeg</code> × (max − min).</p>';

    const block = b.solverBlocks?.[0];
    const rhobeg = Number(block?.rhobeg_u ?? 0.1);

    b.variables.forEach((v) => {
        enrichVariableSearchBounds(v);
        const row = document.createElement('div');
        row.className = 'opt-card';
        row.style.cssText = 'margin:0 0 8px;padding:10px 12px;box-shadow:none;';

        const title = document.createElement('div');
        title.style.cssText = 'font-weight:600;font-size:12px;margin-bottom:6px;';
        title.textContent = v.label || v.id;
        row.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'opt-form-grid';

        const modeSel = document.createElement('select');
        modeSel.className = 'opt-input';
        [
            { value: 'delta', label: 'Relative to start (Δ)' },
            { value: 'absolute', label: 'Absolute' },
        ].forEach((opt) => {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if ((v.boundsDelta !== false) === (opt.value === 'delta')) o.selected = true;
            modeSel.appendChild(o);
        });
        modeSel.addEventListener('change', () => {
            v.boundsDelta = modeSel.value === 'delta';
            if (!v.boundsDelta) {
                if (String(v.path || '').includes('nominal_motor_positions')) {
                    if (Number(v.boundsMin) < 0 && Number(v.boundsMax) <= 90) {
                        v.boundsMin = 0;
                        v.boundsMax = 180;
                    }
                }
            } else if (String(v.path || '').includes('nominal_motor_positions')) {
                if (Number(v.boundsMin) === 0 && Number(v.boundsMax) >= 180) {
                    v.boundsMin = -45;
                    v.boundsMax = 45;
                }
            }
            syncMaxDeltaWithVariableBounds(b);
            renderStageContent();
        });
        grid.appendChild(createFormField('Mode', modeSel));

        const unit = v.unit || '';
        ['Min', 'Max'].forEach((bound, i) => {
            const key = i === 0 ? 'boundsMin' : 'boundsMax';
            const inp = createOptInput({
                value: v[key],
                step: unit === 'deg' ? 1 : 0.1,
                onChange: (el) => {
                    const n = parseFloat(el.value);
                    if (Number.isFinite(n)) v[key] = n;
                    syncMaxDeltaWithVariableBounds(b);
                    const hintEl = row.querySelector('[data-bounds-hint]');
                    if (hintEl) hintEl.textContent = _boundsHintText(v, rhobeg);
                },
            });
            grid.appendChild(createFormField(`${bound} (${unit || 'units'})`, inp));
        });

        row.appendChild(grid);
        const hint = document.createElement('p');
        hint.className = 'opt-hint';
        hint.dataset.boundsHint = '1';
        hint.style.cssText = 'margin:6px 0 0;';
        hint.textContent = _boundsHintText(v, rhobeg);
        row.appendChild(hint);
        card.appendChild(row);
    });

    body.appendChild(card);
}

function _boundsHintText(v, rhobeg) {
    const span = variableSearchSpan(v);
    const step = approxInitialStepPhysical(v, rhobeg);
    const unit = v.unit || 'units';
    const mode = v.boundsDelta !== false ? 'relative' : 'absolute';
    const box =
        mode === 'relative'
            ? `Δ [${Number(v.boundsMin)}, ${Number(v.boundsMax)}] ${unit} from start`
            : `[${Number(v.boundsMin)}, ${Number(v.boundsMax)}] ${unit}`;
    const stepTxt =
        step != null && Number.isFinite(step)
            ? ` · initial step ≈ ${step.toFixed(step >= 1 ? 1 : 3)} ${unit} (rhobeg=${rhobeg})`
            : '';
    return `Search ${box} · span ${span.toFixed(span >= 1 ? 1 : 3)} ${unit}${stepTxt}`;
}

function _rhobegPhysicalTip(builder, rhobegU) {
    const parts = (builder.variables || [])
        .map((v) => {
            enrichVariableSearchBounds(v);
            const step = approxInitialStepPhysical(v, rhobegU);
            if (step == null) return null;
            const unit = v.unit || '';
            return `≈ ${step.toFixed(step >= 1 ? 1 : 3)} ${unit}`.trim();
        })
        .filter(Boolean);
    const phys = parts.length ? ` → ${parts.join(', ')} for current variables` : '';
    return (
        `Starting move as a fraction of each variable's search span (COBYLA rhobeg)${phys}. ` +
        'Widen Search bounds on Variables if travel is too small.'
    );
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


/** @type {{id:string,label?:string,artifact_present?:boolean}[]|null} */
let _edgeKernelCatalog = null;
let _edgeKernelCatalogLoading = false;

async function ensureEdgeKernelCatalog() {
    if (_edgeKernelCatalog || _edgeKernelCatalogLoading) return _edgeKernelCatalog;
    _edgeKernelCatalogLoading = true;
    try {
        const res = await fetchEdgeKernels();
        _edgeKernelCatalog = res.ok ? res.kernels || [] : [];
    } catch {
        _edgeKernelCatalog = [];
    } finally {
        _edgeKernelCatalogLoading = false;
    }
    return _edgeKernelCatalog;
}

function addEdgeKernelPreset(tagId, presetId) {
    const b = builder();
    if (!tagId || !presetId) return;
    const term = createEdgeKernelObjectiveTerm(tagId, presetId, b.objectiveTerms);
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
    const catalogMids = getCatalogRow(tagId)?.motor_ids;
    // Prefer catalog motor_ids so OPTIMIZE lists motors before the first MOVE
    // seeds Twin ``nominal_motor_positions`` keys.
    const midKeys = new Set();
    if (Array.isArray(catalogMids) && catalogMids.length) {
        catalogMids.forEach((mid) => midKeys.add(String(mid)));
    }
    if (motors && typeof motors === 'object') {
        Object.keys(motors).forEach((mid) => midKeys.add(String(mid)));
    }
    midKeys.forEach((mid) => {
        options.push({
            id: `v_${tagId}_m${mid}`,
            tag_id: tagId,
            path: `tunables.nominal_motor_positions.${mid}`,
            label: `${shortName} · motor ${mid}`,
            physical_type: 'continuous',
        });
    });
    const pose = tun.nominal_pose;
    if (pose && typeof pose === 'object') {
        ['x', 'y', 'rotation'].forEach((axis) => {
            if (axis in pose) {
                options.push({
                    id: `v_${tagId}_${axis}`,
                    tag_id: tagId,
                    path: `tunables.nominal_pose.${axis}`,
                    label: `${shortName} · pose ${axis}`,
                    // Pose axes are arm pick/place moves (invasive), not motor setpoints.
                    physical_type: 'invasive_discrete',
                });
            }
        });
    }
    return options;
}

function addVariable(entry) {
    const b = builder();
    if (b.variables.some((v) => v.id === entry.id)) return;
    b.variables.push(enrichVariableSearchBounds({ ...entry }));
    syncMaxDeltaWithVariableBounds(b);
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
    (b.variables || []).forEach((v) => enrichVariableSearchBounds(v));
    syncMaxDeltaWithVariableBounds(b);
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
            variables: b.variables.map((v) => {
                enrichVariableSearchBounds(v);
                const isMotor = String(v.path || '').includes('nominal_motor_positions');
                const isPose = String(v.path || '').includes('nominal_pose');
                const physical_type =
                    v.physical_type ||
                    (isPose ? 'invasive_discrete' : 'continuous');
                const unit =
                    v.unit ||
                    (isMotor || String(v.path || '').endsWith('.rotation') ? 'deg' : 'mm');
                const row = {
                    id: v.id,
                    tag_id: v.tag_id,
                    path: v.path,
                    kind: 'continuous',
                    physical_type,
                    unit,
                    bounds: {
                        min: Number(v.boundsMin),
                        max: Number(v.boundsMax),
                    },
                    delta: v.boundsDelta !== false,
                };
                if (physical_type === 'invasive_discrete') {
                    row.touch_and_go = {
                        required: true,
                        gripper_tag: v.tag_id,
                        measure_only_while_released: true,
                        settle_ms_after_release: 450,
                    };
                }
                return row;
            }),
            objective: {
                type: 'weighted_sum',
                minimize: true,
                terms,
            },
            solver,
            // Sparse camera preview on edge progress (not every eval).
            telemetry: {
                stream: 'optimization-ensemble',
                include: ['camera_image'],
                camera_every_n: Number(b.telemetryCameraEveryN) > 0
                    ? Number(b.telemetryCameraEveryN)
                    : 5,
                camera_jpeg_quality: 70,
                camera_jpeg_scale: 0.25,
            },
        },
    };
}

function buildJobSubmitOptions(b) {
    const opts = {
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

function liveFeedBlockingOptimizeTags(command) {
    const comps = store.labState?.components || {};
    const tags = new Set();
    const params = command?.parameters || {};
    (params.capture?.before_each_eval || []).forEach((s) => {
        if (s?.tag_id) tags.add(String(s.tag_id));
    });
    (params.objective?.terms || []).forEach((t) => {
        const tid = t?.source?.tag_id || t?.tag_id;
        if (tid) tags.add(String(tid));
    });
    (builder().objectiveTerms || []).forEach((t) => {
        if (t?.tag_id) tags.add(String(t.tag_id));
        if (t?.source?.tag_id) tags.add(String(t.source.tag_id));
    });
    return [...tags].filter((tid) => isLiveFeedActive(comps[tid], 'stream'));
}

async function submitEnsembleOptimizationJob(command) {
    const b = builder();
    const liveBlocks = liveFeedBlockingOptimizeTags(command);
    if (liveBlocks.length) {
        const list = liveBlocks.join(', ');
        _log(
            `Cannot start OPTIMIZE: live feed is on for ${list}. End live feed on those cameras first (Hide pop-out is not enough).`,
            'error',
        );
        return {
            ok: false,
            error: `live feed active on ${list} — End live feed before OPTIMIZE`,
        };
    }

    const graph = buildAuthoringObjectiveGraph(b);
    const termSummaries = (graph.terms || []).map((t, i) => {
        const src = t.source || {};
        return (
            `#${i} id=${t.id} keys=[${Object.keys(t).join(',')}] ` +
            `field=${t.field || '-'} kernel=${src.kernel_id || t.kernel_id || '-'} ` +
            `has_source=${Boolean(t.source)}`
        );
    });
    _log(`Compile objective graph (${termSummaries.length} term(s))…`, 'info');
    termSummaries.forEach((line) => _log(`  ${line}`, 'info'));

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
        const detail = compileResult.detail || compileResult.preflight;
        _log(`Failed: ${err}`, 'error');
        if (detail?.errors?.length) {
            _log(`Compile errors: ${JSON.stringify(detail.errors).slice(0, 800)}`, 'error');
        }
        console.warn('[optimize] compile/preflight failed', { err, detail, graph });
        return { ok: false, error: err, detail };
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
    store.ensembleLossTrace = lossPointsFromTrace(trace);
}

/** Camera tag used by objective kernels / capture (for live OPTIMIZE preview). */
function resolveOptimizationCameraTag(ctx) {
    const sess = store.labState?.optimization_session;
    if (sess?.preview_camera_tag) return String(sess.preview_camera_tag);
    const cam = ctx?.lastEval?.camera_image;
    if (cam && typeof cam === 'object' && cam.tag_id) return String(cam.tag_id);
    const b = builder();
    for (const t of b.objectiveTerms || []) {
        if (t.tag_id && (t.field === 'camera_image' || t.kernelId)) {
            return String(t.tag_id);
        }
    }
    return null;
}

function optimizationPreviewEpoch(ctx, tagId) {
    const cam = ctx?.lastEval?.camera_image;
    if (cam && typeof cam === 'object') {
        const prov = cam.provenance && typeof cam.provenance === 'object' ? cam.provenance : {};
        const ep = cam.epoch_ms ?? prov.epoch_ms;
        if (ep != null) return String(ep);
        if (ctx?.lastEval?.eval != null) return `eval-${ctx.lastEval.eval}`;
    }
    if (!tagId) return '';
    const comp = store.labState?.components?.[tagId];
    const mv = measurableValue(comp, 'camera_image');
    if (mv && typeof mv === 'object') {
        const prov = mv.provenance && typeof mv.provenance === 'object' ? mv.provenance : {};
        const ep = mv.epoch_ms ?? prov.epoch_ms;
        if (ep != null) return String(ep);
    }
    return '';
}

function formatOptCameraPreviewHtml(ctx) {
    const tagId = resolveOptimizationCameraTag(ctx);
    if (!tagId) {
        return `<div class="opt-telemetry-camera opt-telemetry-camera--idle">
            Camera preview: add a camera objective to see sparse frames during the run
        </div>`;
    }
    const epoch = optimizationPreviewEpoch(ctx, tagId);
    const hasFrame = Boolean(epoch) || Boolean(ctx?.lastEval?.camera_image);
    if (!hasFrame && ctx?.running) {
        const everyN = Number(builder().telemetryCameraEveryN) > 0
            ? Number(builder().telemetryCameraEveryN)
            : 5;
        return `<div class="opt-telemetry-camera opt-telemetry-camera--idle">
            Camera preview for ${tagId}: waiting for first sparse frame (eval 1 / new-best / every ${everyN})
        </div>`;
    }
    if (!hasFrame) {
        return `<div class="opt-telemetry-camera opt-telemetry-camera--idle">
            No camera frame in this run’s trace
        </div>`;
    }
    const src = withBackendQuery(
        `/api/components/${encodeURIComponent(tagId)}/camera-image?t=${encodeURIComponent(epoch || Date.now())}`,
    );
    return `<div class="opt-telemetry-camera" data-tag="${tagId}" data-epoch="${epoch}">
        <div class="opt-telemetry-camera__head">
            <strong>${tagId}</strong>
            <span>sparse preview · not every eval</span>
        </div>
        <img class="opt-telemetry-camera__img" alt="OPTIMIZE capture ${tagId}" src="${src}" />
    </div>`;
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
        _optTelemetrySig = '';
        return;
    }
    el.hidden = false;
    syncEnsembleLossTraceFromState();

    // While Accept is in flight, keep the button DOM stable so polls cannot
    // cancel the click / reset the disabled state mid-request.
    if (_optAcceptInFlight) return;

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
    const advancing =
        ctx.queued ? 'Waiting in queue…' : ctx.running ? 'Advancing' : 'Idle';

    const statusLabel = ctx.queued
        ? 'Job queued'
        : ctx.running
          ? 'Running'
          : ctx.result?.early_stopped
            ? 'Stopped — good enough'
            : 'Completed';
    const jobId = builder().activeJobId || store.labState?.active_job_id || '';
    const sessionHref = optimizeSessionHref({ jobId, maxEvals });
    const canAccept = !!(ctx.running && jobId);

    // Lab-state polls ~500ms. Replacing innerHTML each tick destroys the
    // <a>/<button> under the cursor and cancels clicks — skip no-op redraws.
    const sig = [
        statusLabel,
        evalN,
        maxEvals,
        best,
        lastLoss,
        advancing,
        pct,
        jobId,
        canAccept ? '1' : '0',
        sessionHref,
    ].join('|');
    if (sig === _optTelemetrySig && el.dataset.optLiveBound === '1') {
        const bar = el.querySelector('.opt-telemetry-progress > span');
        if (bar) bar.style.width = `${pct}%`;
        return;
    }
    _optTelemetrySig = sig;

    el.innerHTML = `
        <div class="opt-telemetry-head">
            <strong>${statusLabel}</strong>
            <span>eval ${evalN}/${maxEvals} · best ${best}</span>
            ${
                canAccept
                    ? `<button type="button" class="opt-accept-btn" data-opt-accept="${_escapeHtml(jobId)}" title="Finish now as success and keep best actuators">Accept · good enough</button>`
                    : ''
            }
        </div>
        <div class="opt-telemetry-progress" title="Evaluation progress"><span style="width:${pct}%"></span></div>
        <div class="opt-telemetry-sub">${advancing} · latest loss ${lastLoss}</div>
        <a class="opt-session-link" href="${_escapeHtml(sessionHref)}" target="_blank" rel="noopener" data-opt-session-link="1">
            Open full session page
            <span class="material-icons-round" style="font-size:14px;">open_in_new</span>
        </a>
        <p class="opt-session-link-hint">Tunables, kernels, camera frames, and iteration log live there — opens immediately, even before eval 1.</p>
    `;
    el.dataset.optLiveBound = '1';
}

function bindOptimizationLiveDelegates(root) {
    if (_optLiveDelegatesBound || !root) return;
    const panel = root.querySelector('#opt-mode-live-panel') || root;
    // pointerdown opens reliably even if a poll redraws before click completes.
    panel.addEventListener('pointerdown', (ev) => {
        const link = ev.target?.closest?.('[data-opt-session-link]');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href) return;
        ev.preventDefault();
        window.open(href, '_blank', 'noopener');
    });
    panel.addEventListener('click', async (ev) => {
        const acceptBtn = ev.target?.closest?.('[data-opt-accept]');
        if (!acceptBtn || acceptBtn.disabled || _optAcceptInFlight) return;
        const id = acceptBtn.getAttribute('data-opt-accept');
        if (!id) return;
        ev.preventDefault();
        _optAcceptInFlight = true;
        acceptBtn.disabled = true;
        acceptBtn.textContent = 'Accepting…';
        _log('Accepting optimization as good enough (keep best)…', 'info');
        const res = await acceptJob(id);
        if (!res.ok) {
            _log(`Accept failed: ${res.error}`, 'error');
            _optAcceptInFlight = false;
            _optTelemetrySig = '';
            acceptBtn.disabled = false;
            acceptBtn.textContent = 'Accept · good enough';
            renderOptimizationTelemetry(root);
            return;
        }
        _log('Accept signaled — waiting for settle / commit…', 'info');
        // Keep in-flight until the run leaves OPTIMIZING so polls do not rebuild the button.
        const clearWhenSettled = () => {
            const st = store.labState?.system_status;
            if (st && st !== 'OPTIMIZING') {
                _optAcceptInFlight = false;
                _optTelemetrySig = '';
                return;
            }
            setTimeout(clearWhenSettled, 400);
        };
        setTimeout(clearWhenSettled, 400);
    });
    _optLiveDelegatesBound = true;
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
        _edgeKernelCatalog = null;
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
    const sessionHref = optimizeSessionHref({ maxEvals: evals });
    card.innerHTML = `
        <div class="opt-card__title">Last run saved</div>
        <div class="opt-card__meta">Best loss ${loss} · ${evals} evals${when ? ` · ${when}` : ''}</div>
        <button type="button" class="btn btn-secondary btn--sidebar-inline opt-view-last-btn" style="margin-top:8px;width:100%;">
            View last results
        </button>
        <a class="opt-session-link" href="${_escapeHtml(sessionHref)}" target="_blank" rel="noopener" style="margin-top:8px;">
            Open full session page
            <span class="material-icons-round" style="font-size:14px;">open_in_new</span>
        </a>
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
    if (!_optLossChart || _optLossChart.canvas !== canvas) {
        _optLossChart?.destroy?.();
        _optLossChart = attachLossChart(canvas, { compact: true });
    }
    syncEnsembleLossTraceFromState();
    _optLossChart?.setPoints(store.ensembleLossTrace || []);
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
        const camSensors = sensors.filter(({ tagId }) =>
            getOptimizableMeasurablesForTag(tagId).some((m) => m.field === 'camera_image'),
        );

        const presetCard = document.createElement('div');
        presetCard.className = 'opt-card';
        presetCard.innerHTML = '<div class="opt-card__title">What should we optimize?</div>';
        const presetHint = document.createElement('p');
        presetHint.className = 'opt-hint';
        presetHint.textContent =
            'Pick a camera, then add one goal. Each goal uses a kernel from the active edge.';
        presetCard.appendChild(presetHint);

        const presetTagSel = document.createElement('select');
        presetTagSel.className = 'opt-select';
        presetTagSel.innerHTML = '<option value="">Camera…</option>';
        camSensors.forEach(({ tagId, label }) => {
            const opt = document.createElement('option');
            opt.value = tagId;
            opt.textContent = label;
            presetTagSel.appendChild(opt);
        });
        if (camSensors.length === 1) presetTagSel.value = camSensors[0].tagId;
        presetCard.appendChild(createFormField('Camera', presetTagSel, true));

        const presetList = document.createElement('div');
        presetList.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:10px;';
        presetCard.appendChild(presetList);
        body.appendChild(presetCard);

        const renderPresetButtons = (kernels) => {
            presetList.innerHTML = '';
            const available = new Set(
                (kernels || [])
                    .filter((k) => k && k.artifact_present !== false)
                    .map((k) => k.id),
            );
            if (!camSensors.length) {
                const miss = document.createElement('p');
                miss.className = 'opt-hint';
                miss.textContent = 'Place a camera with camera_image to use these goals.';
                presetList.appendChild(miss);
                return;
            }
            Object.entries(EDGE_OBJECTIVE_PRESETS).forEach(([presetId, cfg]) => {
                const kernelId = cfg.kernelId || presetId;
                const present = available.has(kernelId);
                const row = document.createElement('button');
                row.type = 'button';
                row.className = 'btn btn-secondary';
                row.dataset.kernelPresent = present ? '1' : '0';
                row.style.cssText =
                    'display:flex;flex-direction:column;align-items:flex-start;gap:2px;' +
                    'width:100%;text-align:left;padding:10px 12px;white-space:normal;height:auto;';
                row.disabled = !present || !presetTagSel.value;
                const title = document.createElement('span');
                title.style.cssText = 'font-weight:600;font-size:13px;line-height:1.3;';
                title.textContent = cfg.label;
                const summary = document.createElement('span');
                summary.className = 'opt-hint';
                summary.style.cssText = 'margin:0;font-size:12px;line-height:1.35;opacity:0.9;';
                summary.textContent = cfg.summary || '';
                const meta = document.createElement('span');
                meta.style.cssText =
                    'font-size:11px;opacity:0.65;font-family:ui-monospace,monospace;margin-top:2px;';
                meta.textContent = present
                    ? `Kernel: ${kernelId}`
                    : `Kernel: ${kernelId} (not on this edge)`;
                row.appendChild(title);
                row.appendChild(summary);
                row.appendChild(meta);
                row.title = present
                    ? `Add objective using ${presetId}`
                    : `${kernelId} not on this edge (or artifact missing)`;
                row.onclick = () => addEdgeKernelPreset(presetTagSel.value, presetId);
                presetList.appendChild(row);
            });
            const syncPresetEnabled = () => {
                const noCam = !presetTagSel.value;
                presetList.querySelectorAll('button').forEach((btn) => {
                    btn.disabled = noCam || btn.dataset.kernelPresent !== '1';
                });
            };
            presetTagSel.onchange = syncPresetEnabled;
            syncPresetEnabled();
        };

        if (_edgeKernelCatalog) {
            renderPresetButtons(_edgeKernelCatalog);
        } else {
            const loading = document.createElement('p');
            loading.className = 'opt-hint';
            loading.textContent = 'Loading edge kernels…';
            presetList.appendChild(loading);
            ensureEdgeKernelCatalog().then((kernels) => {
                if (builder().stage === 1) renderPresetButtons(kernels);
            });
        }
    } else {
        const empty = document.createElement('p');
        empty.className = 'opt-hint';
        empty.textContent = 'No placed components with a camera image measurable.';
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
    (b.variables || []).forEach((v) => enrichVariableSearchBounds(v));

    appendSelectedVariablesBar(body, b.variables, removeVariable);
    appendVariableSearchBoundsEditor(body, b);

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
            onChange: (inp) => {
                term.weight = parseFloat(inp.value) || 0;
            },
        });
        grid.appendChild(createFormField('Weight', wIn));

        if (term.kernelId === 'builtin.roi_centroid' || (!term.kernelId && term.field === 'camera_image')) {
            if (!term.centroidTarget) {
                term.centroidTarget = defaultCentroidTargetForCamera(term.tag_id);
            }
            ['x', 'y'].forEach((axis) => {
                const inp = createOptInput({
                    value: term.centroidTarget?.[axis] ?? 0,
                    onChange: (el) => {
                        if (!term.centroidTarget) {
                            term.centroidTarget = defaultCentroidTargetForCamera(term.tag_id);
                        }
                        term.centroidTarget[axis] = parseFloat(el.value) || 0;
                    },
                });
                grid.appendChild(createFormField(`Target ${axis.toUpperCase()}`, inp));
            });
            const tip = document.createElement('p');
            tip.className = 'opt-hint';
            tip.style.cssText = 'grid-column:1/-1;margin:4px 0 0;';
            tip.textContent = cameraCenterDefaultHint(term.tag_id);
            grid.appendChild(tip);
        }
        if (term.metric === 'ratio_to_ref' || term.metric === 'ratio_from_ref') {
            const tip = document.createElement('p');
            tip.className = 'opt-hint';
            tip.style.cssText = 'grid-column:1/-1;margin:4px 0 0;';
            tip.textContent =
                term.metric === 'ratio_from_ref'
                    ? 'Loss = current flux / first-eval flux (~1 at start, lower = darker). Same beam_power kernel as maximize.'
                    : 'Loss = first-eval flux / current flux (~1 at start, lower = brighter). No Normalize min/max — the first reading is the scale.';
            grid.appendChild(tip);
        } else if (term.metric === 'one_minus_normalized' || term.field === 'output_power_readback_mw') {
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
        meta.textContent = term.kernelId
            ? `Kernel: ${term.kernelId} · Metric: ${term.metric}`
            : `Metric: ${term.metric}`;
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
    (b.variables || []).forEach((v) => enrichVariableSearchBounds(v));
    syncMaxDeltaWithVariableBounds(b);
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
            'Post-move wait (ms)',
            createOptInput({
                value: b.settleMs,
                min: 0,
                onChange: (inp) => {
                    b.settleMs = parseInt(inp.value, 10) || 0;
                },
            }),
        ),
    );
    budgetGrid.appendChild(
        createFormField(
            'Camera preview every N evals',
            createOptInput({
                value: b.telemetryCameraEveryN ?? 5,
                min: 1,
                onChange: (inp) => {
                    const n = parseInt(inp.value, 10);
                    b.telemetryCameraEveryN = Number.isFinite(n) && n >= 1 ? n : 5;
                },
            }),
        ),
    );
    budgetCard.appendChild(budgetGrid);

    const stopLabel = document.createElement('label');
    stopLabel.className = 'opt-job-option';
    const stopCb = document.createElement('input');
    stopCb.type = 'checkbox';
    stopCb.checked = b.stopLossEnabled !== false;
    stopCb.addEventListener('change', () => {
        b.stopLossEnabled = stopCb.checked;
        stopField.hidden = !stopCb.checked;
    });
    stopLabel.appendChild(stopCb);
    stopLabel.appendChild(
        document.createTextNode(' Stop early when loss is good enough'),
    );
    budgetCard.appendChild(stopLabel);

    const stopField = document.createElement('div');
    stopField.className = 'opt-form-grid';
    stopField.hidden = b.stopLossEnabled === false;
    stopField.style.marginTop = '6px';
    stopField.appendChild(
        createFormField(
            'Stop when loss ≤',
            createOptInput({
                value: b.stopLoss ?? 0.1,
                min: 0,
                step: 0.01,
                onChange: (inp) => {
                    const n = parseFloat(inp.value);
                    b.stopLoss = Number.isFinite(n) && n >= 0 ? n : 0.1;
                },
            }),
        ),
    );
    budgetCard.appendChild(stopField);

    const settleHint = document.createElement('p');
    settleHint.className = 'opt-hint';
    settleHint.textContent =
        'Pause after each actuator move before the next camera capture (mechanics settle — not exposure).';
    budgetCard.appendChild(settleHint);
    const camHint = document.createElement('p');
    camHint.className = 'opt-hint';
    camHint.textContent =
        'Sparse JPEG preview: first eval, each new best, and every Nth eval — not on the closed-loop critical path.';
    budgetCard.appendChild(camHint);
    const stopHint = document.createElement('p');
    stopHint.className = 'opt-hint';
    stopHint.textContent =
        'Early-stop uses normalized total loss (align ≈ FOV fraction). Example: 0.1 ≈ beam within 10% of the frame diagonal from target.';
    budgetCard.appendChild(stopHint);
    body.appendChild(budgetCard);

    const safetyCard = document.createElement('div');
    safetyCard.className = 'opt-card';
    safetyCard.innerHTML = '<div class="opt-card__title">Safety & abort</div>';

    const keepLabel = document.createElement('label');
    keepLabel.className = 'opt-job-option';
    const keepCb = document.createElement('input');
    keepCb.type = 'checkbox';
    keepCb.checked = b.keepBest !== false;
    keepCb.addEventListener('change', () => {
        b.keepBest = keepCb.checked;
    });
    keepLabel.appendChild(keepCb);
    keepLabel.appendChild(document.createTextNode(' Keep best on completion / abort'));
    safetyCard.appendChild(keepLabel);

    const rollLabel = document.createElement('label');
    rollLabel.className = 'opt-job-option';
    const rollCb = document.createElement('input');
    rollCb.type = 'checkbox';
    rollCb.checked = Boolean(b.rollbackOnFail);
    rollCb.addEventListener('change', () => {
        b.rollbackOnFail = rollCb.checked;
    });
    rollLabel.appendChild(rollCb);
    rollLabel.appendChild(
        document.createTextNode(' On abort, rollback actuators to start (x0)'),
    );
    safetyCard.appendChild(rollLabel);

    const deltaLabel = document.createElement('label');
    deltaLabel.className = 'opt-job-option';
    const deltaCb = document.createElement('input');
    deltaCb.type = 'checkbox';
    deltaCb.checked = b.maxDeltaEnabled !== false;
    deltaCb.addEventListener('change', () => {
        b.maxDeltaEnabled = deltaCb.checked;
        deltaGrid.hidden = !deltaCb.checked;
    });
    deltaLabel.appendChild(deltaCb);
    deltaLabel.appendChild(
        document.createTextNode(' Max Δ from applied start (refuse oversized steps)'),
    );
    safetyCard.appendChild(deltaLabel);

    const deltaGrid = document.createElement('div');
    deltaGrid.className = 'opt-form-grid';
    deltaGrid.hidden = b.maxDeltaEnabled === false;
    deltaGrid.appendChild(
        createFormField(
            'Max Δ deg',
            createOptInput({
                value: b.maxDeltaDeg ?? 1,
                step: 0.1,
                min: 0,
                onChange: (inp) => {
                    b.maxDeltaDeg = parseFloat(inp.value) || 0;
                },
            }),
        ),
    );
    deltaGrid.appendChild(
        createFormField(
            'Max Δ mm',
            createOptInput({
                value: b.maxDeltaMm ?? 5,
                step: 0.5,
                min: 0,
                onChange: (inp) => {
                    b.maxDeltaMm = parseFloat(inp.value) || 0;
                },
            }),
        ),
    );
    safetyCard.appendChild(deltaGrid);

    const dbgLabel = document.createElement('label');
    dbgLabel.className = 'opt-job-option';
    const dbgCb = document.createElement('input');
    dbgCb.type = 'checkbox';
    dbgCb.checked = b.showStageDebug !== false;
    dbgCb.addEventListener('change', () => {
        b.showStageDebug = dbgCb.checked;
    });
    dbgLabel.appendChild(dbgCb);
    dbgLabel.appendChild(
        document.createTextNode(' Show capture / kernel / actuate debug in live panel'),
    );
    safetyCard.appendChild(dbgLabel);

    const safetyHint = document.createElement('p');
    safetyHint.className = 'opt-hint';
    safetyHint.textContent =
        'Continuous blocks also require optical-path clearance (arm not holding). Failures show in stage debug.';
    safetyCard.appendChild(safetyHint);
    body.appendChild(safetyCard);

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
        grid.appendChild(
            createFormField(
                'Max evals (this block)',
                createOptInput({
                    value: block.max_evals,
                    step: 1,
                    min: 1,
                    onChange: (inp) => {
                        block.max_evals = parseFloat(inp.value) || 0;
                    },
                }),
            ),
        );
        card.appendChild(grid);

        const advanced = document.createElement('details');
        advanced.style.marginTop = '8px';
        const summary = document.createElement('summary');
        summary.className = 'opt-hint';
        summary.style.cssText = 'cursor:pointer;user-select:none;';
        summary.textContent = 'Advanced solver (COBYLA step sizes)';
        advanced.appendChild(summary);
        const advGrid = document.createElement('div');
        advGrid.className = 'opt-form-grid';
        advGrid.style.marginTop = '8px';
        [
            [
                'passes',
                'Passes',
                block.passes,
                1,
                'How many times to restart the local search on this block.',
            ],
            [
                'rhobeg_u',
                'Initial step size',
                block.rhobeg_u,
                0.001,
                _rhobegPhysicalTip(b, block.rhobeg_u),
            ],
            [
                'rhoend_u',
                'Final step size',
                block.rhoend_u,
                0.001,
                'Stop refining when steps get this small (fraction of the search box).',
            ],
        ].forEach(([key, label, val, step, tip]) => {
            const field = createFormField(
                label,
                createOptInput({
                    value: val,
                    step,
                    onChange: (inp) => {
                        block[key] = parseFloat(inp.value) || 0;
                    },
                }),
            );
            if (tip) field.title = tip;
            advGrid.appendChild(field);
        });
        advanced.appendChild(advGrid);
        const advHint = document.createElement('p');
        advHint.className = 'opt-hint';
        advHint.textContent =
            'Leave defaults unless the solver stalls or steps are too large/small.';
        advanced.appendChild(advHint);
        card.appendChild(advanced);
        body.appendChild(card);
    });

    const addBlock = document.createElement('details');
    addBlock.style.marginTop = '4px';
    const addSummary = document.createElement('summary');
    addSummary.className = 'opt-hint';
    addSummary.style.cssText = 'cursor:pointer;user-select:none;';
    addSummary.textContent = 'Advanced: split variables into another block';
    addBlock.appendChild(addSummary);
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'btn btn-secondary btn--sidebar-inline';
    addBtn.style.marginTop = '8px';
    addBtn.textContent = '+ Split into another block';
    addBtn.onclick = () => {
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
    addBlock.appendChild(addBtn);
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
            <div class="opt-stat"><span class="opt-stat-val">${Number(b.telemetryCameraEveryN) > 0 ? Number(b.telemetryCameraEveryN) : 5}</span><span class="opt-stat-label">Cam every N</span></div>
        `;
    } else {
        stats.innerHTML =
            '<p class="opt-hint" style="grid-column:1/-1;margin:0">Incomplete plan — fill earlier stages before running.</p>';
    }
    body.appendChild(stats);

    const planCard = document.createElement('div');
    planCard.className = 'opt-card';
    planCard.innerHTML = '<div class="opt-card__title">Plan (capture → kernel → actuate)</div>';
    const planList = document.createElement('ul');
    planList.className = 'opt-hint';
    planList.style.cssText = 'margin:8px 0 0;padding-left:1.2em;';
    buildRunPlanSummary(b).forEach((line) => {
        const li = document.createElement('li');
        li.textContent = line;
        planList.appendChild(li);
    });
    planCard.appendChild(planList);
    body.appendChild(planCard);

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
                    if (!result.ok) {
                        // submitEnsembleOptimizationJob already logged compile detail;
                        // keep a short final line for non-compile failures too.
                        if (!String(result.error || '').includes('schema') &&
                            !String(result.error || '').includes('preflight') &&
                            !String(result.error || '').includes('compile')) {
                            _log(`Failed: ${result.error}`, 'error');
                        }
                    } else void fetchLabState();
                },
            },
        ]),
    );
}

function labelForVariableId(id) {
    const v = builder().variables.find((x) => x.id === id);
    return v?.label || id;
}

function variableUnit(v) {
    if (!v) return '';
    if (v.unit) return String(v.unit);
    const path = String(v.path || '');
    if (path.includes('motor') || path.endsWith('.rotation')) return 'deg';
    if (path.includes('nominal_pose')) return 'mm';
    return '';
}

function formatTunableNumber(val, digits = 3) {
    const n = Number(val);
    if (!Number.isFinite(n)) return String(val ?? '—');
    const abs = Math.abs(n);
    if (abs !== 0 && (abs < 0.001 || abs >= 1000)) return n.toExponential(2);
    return n.toFixed(digits);
}

/** Ordered variable ids for live tables: builder order, then any extra keys in values. */
function orderedVariableIds(valuesList) {
    const b = builder();
    const seen = new Set();
    const ids = [];
    (b.variables || []).forEach((v) => {
        if (v?.id && !seen.has(v.id)) {
            seen.add(v.id);
            ids.push(v.id);
        }
    });
    (valuesList || []).forEach((values) => {
        if (!values || typeof values !== 'object') return;
        Object.keys(values).forEach((id) => {
            if (!seen.has(id)) {
                seen.add(id);
                ids.push(id);
            }
        });
    });
    return ids;
}

function variableColumnMeta(id) {
    const v = builder().variables.find((x) => x.id === id);
    if (!v) {
        return { id, title: id, sub: '', unit: '' };
    }
    const tag = componentDisplayLabel(v.tag_id).split(' (')[0];
    return {
        id,
        title: shortTunableLabel(v),
        sub: tag,
        unit: variableUnit(v),
        path: v.path || '',
    };
}

function formatEvalValuesSummary(values, maxItems = 5) {
    if (!values || typeof values !== 'object') return '';
    return orderedVariableIds([values])
        .filter((id) => id in values)
        .slice(0, maxItems)
        .map((id) => {
            const meta = variableColumnMeta(id);
            const unit = meta.unit ? ` ${meta.unit}` : '';
            const prefix = meta.sub ? `${meta.sub}·` : '';
            return `${prefix}${meta.title}=${formatTunableNumber(values[id], 2)}${unit}`;
        })
        .join(' · ');
}

/** Prominent current-eval tunables grid with Δ vs previous eval. */
function formatCurrentTunablesHtml(values, prevValues) {
    if (!values || typeof values !== 'object') {
        return `<div class="opt-telemetry-tunables opt-telemetry-tunables--idle">
            Tunables appear after the first eval
        </div>`;
    }
    const ids = orderedVariableIds([values, prevValues]);
    if (!ids.length) {
        return `<div class="opt-telemetry-tunables opt-telemetry-tunables--idle">
            No variable values on this eval
        </div>`;
    }
    const chips = ids
        .map((id) => {
            const meta = variableColumnMeta(id);
            const raw = values[id];
            const n = Number(raw);
            const shown = formatTunableNumber(raw, 3);
            const unit = meta.unit ? `<span class="opt-tunable-unit">${meta.unit}</span>` : '';
            let deltaHtml = '';
            if (prevValues && typeof prevValues === 'object' && id in prevValues) {
                const prev = Number(prevValues[id]);
                if (Number.isFinite(n) && Number.isFinite(prev)) {
                    const d = n - prev;
                    if (Math.abs(d) < 1e-9) {
                        deltaHtml = `<span class="opt-tunable-delta opt-tunable-delta--flat">Δ 0</span>`;
                    } else {
                        const sign = d > 0 ? '+' : '';
                        const cls = d > 0 ? 'up' : 'down';
                        deltaHtml = `<span class="opt-tunable-delta opt-tunable-delta--${cls}">Δ ${sign}${formatTunableNumber(d, 3)}</span>`;
                    }
                }
            }
            const title = [meta.path || id, meta.sub].filter(Boolean).join(' · ');
            return `<div class="opt-tunable-chip" title="${title}">
                <div class="opt-tunable-chip__label">
                    <strong>${meta.title}</strong>
                    ${meta.sub ? `<span>${meta.sub}</span>` : ''}
                </div>
                <div class="opt-tunable-chip__value">${shown}${unit}</div>
                ${deltaHtml}
            </div>`;
        })
        .join('');
    return `<div class="opt-telemetry-tunables">
        <div class="opt-telemetry-tunables__head">
            <strong>This eval — tunables</strong>
            <span>Δ vs previous eval</span>
        </div>
        <div class="opt-telemetry-tunables__grid">${chips}</div>
    </div>`;
}

function formatTraceHistoryTableHtml(rowsChronological) {
    // rowsChronological: oldest → newest; we display newest first.
    const display = [...(rowsChronological || [])].reverse();
    if (!display.length) {
        return `<div class="opt-telemetry-table-wrap">
            <table class="opt-telemetry-table">
                <thead><tr><th>Eval</th><th>Loss</th><th>Best</th></tr></thead>
                <tbody><tr><td colspan="3" style="color:#64748b">Waiting for iterations…</td></tr></tbody>
            </table>
        </div>`;
    }
    const varIds = orderedVariableIds(display.map((r) => r.values));
    const bestLosses = display
        .map((r) => (r.best_loss != null ? Number(r.best_loss) : null))
        .filter((n) => n != null && Number.isFinite(n));
    const globalBest = bestLosses.length ? Math.min(...bestLosses) : null;

    const headVars = varIds
        .map((id) => {
            const meta = variableColumnMeta(id);
            const unit = meta.unit ? ` (${meta.unit})` : '';
            return `<th title="${meta.path || id}">${meta.sub ? `${meta.sub} ` : ''}${meta.title}${unit}</th>`;
        })
        .join('');

    const body = display
        .map((row, idx) => {
            // prev in chronological sense = next in reversed display (older).
            const older = display[idx + 1];
            const prevVals = older?.values;
            const isBest =
                globalBest != null &&
                row.best_loss != null &&
                Math.abs(Number(row.best_loss) - globalBest) < 1e-12 &&
                row.loss != null &&
                Math.abs(Number(row.loss) - globalBest) < 1e-9;
            const newBest =
                row.best_loss != null &&
                row.loss != null &&
                Math.abs(Number(row.loss) - Number(row.best_loss)) < 1e-9;
            const st = stageMarks(row.stages);
            const varCells = varIds
                .map((id) => {
                    const values = row.values || {};
                    if (!(id in values)) return '<td class="opt-telemetry-var">—</td>';
                    const n = Number(values[id]);
                    let delta = '';
                    if (prevVals && id in prevVals && Number.isFinite(n)) {
                        const d = n - Number(prevVals[id]);
                        if (Number.isFinite(d) && Math.abs(d) >= 1e-9) {
                            const sign = d > 0 ? '+' : '';
                            delta = `<small class="opt-telemetry-var-delta">${sign}${formatTunableNumber(d, 2)}</small>`;
                        }
                    }
                    return `<td class="opt-telemetry-var"><span>${formatTunableNumber(values[id], 3)}</span>${delta}</td>`;
                })
                .join('');
            const rowCls = [
                isBest ? 'opt-telemetry-row--best' : '',
                newBest ? 'opt-telemetry-row--new-best' : '',
            ]
                .filter(Boolean)
                .join(' ');
            const lossShown =
                row.loss != null && Number.isFinite(Number(row.loss))
                    ? Number(row.loss).toFixed(4)
                    : '—';
            const bestShown =
                row.best_loss != null && Number.isFinite(Number(row.best_loss))
                    ? Number(row.best_loss).toFixed(4)
                    : '—';
            return `<tr class="${rowCls}">
                <td>${row.eval ?? '—'}</td>
                <td>${lossShown}${newBest ? ' <span class="opt-best-pill">best</span>' : ''}</td>
                <td>${bestShown}</td>
                ${varCells}
                <td class="opt-telemetry-stages">${st}</td>
            </tr>`;
        })
        .join('');

    return `<div class="opt-telemetry-table-wrap">
        <table class="opt-telemetry-table opt-telemetry-table--wide">
            <thead>
                <tr>
                    <th>Eval</th>
                    <th>Loss</th>
                    <th>Best</th>
                    ${headVars}
                    <th>Stages</th>
                </tr>
            </thead>
            <tbody>${body}</tbody>
        </table>
    </div>`;
}

function stageMarks(stages) {
    if (!stages || typeof stages !== 'object') return '—';
    const core = ['capture', 'kernel', 'actuate']
        .map((name) => {
            const part = stages[name];
            if (!part || typeof part !== 'object') return `${name[0]}?`;
            if (part.ok === true) return `${name[0]}✓`;
            if (part.ok === false) return `${name[0]}✗`;
            return `${name[0]}·`;
        })
        .join(' ');
    const extras = [];
    if (stages.presence && typeof stages.presence === 'object') {
        const absent = Object.values(stages.presence).some(
            (v) => v && typeof v === 'object' && v.presence_ok === false,
        );
        extras.push(absent ? 'p✗' : 'p✓');
    }
    if (stages.actuate_restore && typeof stages.actuate_restore === 'object') {
        extras.push(stages.actuate_restore.ok === false ? 'rst✗' : 'rst✓');
    }
    if (stages.policy?.early_stop || stages.policy?.would_early_stop) {
        extras.push(stages.policy?.early_stop ? 'stop✓' : 'stop·');
    }
    if (stages.telemetry?.want_camera) {
        extras.push(stages.telemetry?.stashed ? 'cam✓' : 'cam✗');
    }
    return extras.length ? `${core} ${extras.join(' ')}` : core;
}

/** Click-to-pin stage chip id (survives live-panel re-renders). */
let _pinnedStageChipId = null;

function _escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _stageChip(name, part, detail) {
    const ok = part?.ok;
    const cls = ok === true ? 'ok' : ok === false ? 'fail' : 'unknown';
    const mark = ok === true ? '✓' : ok === false ? '✗' : '·';
    const status = ok === true ? 'ok' : ok === false ? 'FAIL' : '—';
    const pinned = _pinnedStageChipId === name ? ' is-pinned' : '';
    const detailHtml = detail
        ? `<div class="opt-stage-chip__detail">${_escapeHtml(detail)}</div>`
        : '<div class="opt-stage-chip__detail opt-stage-chip__detail--empty">No detail yet</div>';
    return `<button type="button" class="opt-stage-chip opt-stage-chip--${cls}${pinned}" data-stage-chip="${_escapeHtml(name)}" aria-pressed="${pinned ? 'true' : 'false'}">
        <span class="opt-stage-chip__mark" aria-hidden="true">${mark}</span>
        <span class="opt-stage-chip__name">${_escapeHtml(name)}</span>
        <span class="opt-stage-chip__panel">
            <strong>${_escapeHtml(name)}</strong>
            <span class="opt-stage-chip__status">${status}</span>
            ${detailHtml}
        </span>
    </button>`;
}

function bindStageDebugChips(root) {
    const wrap = root?.querySelector?.('.opt-stage-debug--chips');
    if (!wrap) return;
    wrap.querySelectorAll('[data-stage-chip]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const id = btn.getAttribute('data-stage-chip');
            _pinnedStageChipId = _pinnedStageChipId === id ? null : id;
            wrap.querySelectorAll('[data-stage-chip]').forEach((el) => {
                const on = el.getAttribute('data-stage-chip') === _pinnedStageChipId;
                el.classList.toggle('is-pinned', on);
                el.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        });
    });
}

function formatStagesDebugHtml(stages) {
    if (!stages || typeof stages !== 'object') {
        return `<div class="opt-stage-debug opt-stage-debug--idle">
            Stage debug: waiting for first eval (hover / click chips after first eval)
        </div>`;
    }
    const cards = [];

    ['capture', 'kernel', 'actuate'].forEach((name) => {
        const part = stages[name] || {};
        let detail = '';
        if (name === 'capture' && Array.isArray(part.frames) && part.frames.length) {
            detail = part.frames
                .map((f) => `${f.capture_id || '?'} ${JSON.stringify(f.shape || [])}`)
                .join('; ');
        } else if (name === 'kernel' && part.terms && typeof part.terms === 'object') {
            detail = Object.entries(part.terms)
                .map(([id, t]) => {
                    if (t?.error) return `${id}: ERR ${t.error}`;
                    if (t?.kind === 'features') {
                        return `${id}: ${t.kernel_id || ''} n=${t.n} [${(t.preview || []).join(',')}]`;
                    }
                    if (t?.scalar != null) return `${id}: ${Number(t.scalar).toFixed(4)}`;
                    return `${id}: ${t?.kind || '—'}`;
                })
                .join(' · ');
        } else if (name === 'actuate') {
            if (part.refused) detail = `refused: ${part.refused}`;
            else if (part.delta_from_x0) {
                detail = Object.entries(part.delta_from_x0)
                    .slice(0, 4)
                    .map(([id, d]) => `${id} Δ=${Number(d).toFixed(3)}`)
                    .join(' · ');
            }
        }
        if (part.error) detail = `${detail ? `${detail} · ` : ''}${part.error}`;
        cards.push(_stageChip(name, part, detail));
    });

    if (stages.presence && typeof stages.presence === 'object') {
        const rows = Object.entries(stages.presence).map(([id, p]) => {
            if (!p || typeof p !== 'object') return `${id}: —`;
            const ok = p.presence_ok !== false;
            return `${id}: peak=${Number(p.peak).toFixed(3)} ref=${Number(p.peak_ref).toFixed(3)} ratio=${p.min_peak_ratio ?? '—'} ${ok ? 'PRESENT' : 'ABSENT'}`;
        });
        const absent = Object.values(stages.presence).some(
            (v) => v && typeof v === 'object' && v.presence_ok === false,
        );
        cards.push(
            _stageChip(
                'presence',
                { ok: !absent },
                rows.join(' · ') || 'no latch yet',
            ),
        );
    }

    const fov = stages.scales || stages.policy?.fov;
    if (fov && typeof fov === 'object') {
        cards.push(
            _stageChip(
                'scales',
                { ok: true },
                `${fov.width || '?'}×${fov.height || '?'} diag=${Number(fov.diagonal || 0).toFixed(1)} width_scale=${Number(fov.width_scale || 0).toFixed(1)}`,
            ),
        );
    }

    if (stages.policy && typeof stages.policy === 'object') {
        const p = stages.policy;
        const bits = [];
        if (p.loss != null) bits.push(`loss=${Number(p.loss).toFixed(4)}`);
        if (p.stop_loss != null) {
            bits.push(`stop≤${Number(p.stop_loss).toFixed(3)}`);
            if (p.early_stop) bits.push('EARLY_STOP');
            else if (p.would_early_stop) bits.push('at threshold');
        } else {
            bits.push('stop=off');
        }
        if (p.early_stop_reason === 'operator_accept') bits.push('operator ACCEPT');
        if (p.presence_absent) bits.push('beam ABSENT→cap');
        if (p.presence_restore) {
            const r = p.presence_restore;
            const du = r.du_from_last_present != null ? Number(r.du_from_last_present).toFixed(3) : '?';
            bits.push(r.ok === false ? `restore FAIL (Δu=${du})` : `restore last-good (Δu=${du})`);
        }
        if (p.terms && typeof p.terms === 'object') {
            bits.push(
                Object.entries(p.terms)
                    .map(([id, t]) => {
                        const scale = t.rms_scale_px ?? t.value_scale_px;
                        const scaleBit = scale != null ? ` /${Number(scale).toFixed(0)}` : '';
                        const pres =
                            t.presence_ok === false
                                ? ' ABSENT'
                                : t.presence_ok === true
                                  ? ' present'
                                  : '';
                        return `${id}=${t.loss != null ? Number(t.loss).toFixed(3) : '—'}${scaleBit}${pres}`;
                    })
                    .join(' · '),
            );
        }
        if (p.error) bits.push(p.error);
        cards.push(_stageChip('policy', p, bits.join(' · ')));
    }

    if (stages.telemetry && typeof stages.telemetry === 'object') {
        const t = stages.telemetry;
        const bits = [];
        if (!t.want_camera) bits.push('skip (not first/new-best/Nth)');
        else {
            bits.push(`reason=${t.reason || '?'}`);
            bits.push(t.stashed ? 'stashed' : 'NOT stashed');
            if (t.tag) bits.push(`tag=${t.tag}`);
            if (t.path) bits.push(t.path);
            if (t.committed_to_lab_state) bits.push('lab-state ok');
            if (t.has_tensor === false) bits.push('no tensor');
        }
        if (t.every_n != null) bits.push(`every_n=${t.every_n}`);
        if (t.error) bits.push(t.error);
        cards.push(_stageChip('telemetry', t, bits.join(' · ')));
    }

    return `<div class="opt-stage-debug opt-stage-debug--chips" title="Hover for detail · click to pin">${cards.join('')}</div>`;
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

    if (last.early_stopped) {
        const early = document.createElement('p');
        early.className = 'opt-hint';
        early.style.color = '#34d399';
        early.textContent =
            last.early_stop_reason === 'operator_accept'
                ? 'Stopped early — you accepted as good enough (kept best actuators).'
                : 'Stopped early — loss reached the good-enough threshold (solver.stop_loss).';
        body.appendChild(early);
    }

    const sessionLink = document.createElement('a');
    sessionLink.className = 'opt-session-link';
    sessionLink.href = optimizeSessionHref({
        maxEvals: last.evals ?? last.trace?.length ?? null,
    });
    sessionLink.target = '_blank';
    sessionLink.rel = 'noopener';
    sessionLink.innerHTML =
        'Open full session page <span class="material-icons-round" style="font-size:14px;">open_in_new</span>';
    body.appendChild(sessionLink);
    const sessionHint = document.createElement('p');
    sessionHint.className = 'opt-session-link-hint';
    sessionHint.textContent =
        'Iteration history, tunables, kernels, camera frames, and stage debug open there.';
    body.appendChild(sessionHint);

    if (last.aborted || last.refusal) {
        const refuse = document.createElement('p');
        refuse.className = 'opt-hint';
        refuse.style.color = '#f87171';
        refuse.textContent = last.refusal
            ? `Session refusal: ${last.refusal}`
            : 'Session aborted (see full session page or job cancel).';
        body.appendChild(refuse);
    }

    const vals = last.final_values || {};
    const valKeys = Object.keys(vals);
    if (valKeys.length) {
        const setCard = document.createElement('div');
        setCard.className = 'opt-results-card';
        setCard.innerHTML = '<h4>Applied setpoints</h4>';
        const table = document.createElement('table');
        table.className = 'opt-telemetry-table';
        table.innerHTML = '<thead><tr><th>Variable</th><th>Component</th><th>Value</th></tr></thead>';
        const tbody = document.createElement('tbody');
        orderedVariableIds([vals]).forEach((id) => {
            if (!(id in vals)) return;
            const meta = variableColumnMeta(id);
            const tr = document.createElement('tr');
            const n = Number(vals[id]);
            const unit = meta.unit ? ` ${meta.unit}` : '';
            tr.innerHTML = `<td>${meta.title}</td><td>${meta.sub || '—'}</td><td>${Number.isFinite(n) ? formatTunableNumber(n, 4) : vals[id]}${unit}</td>`;
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
    hint.textContent = 'Summary stays here; open the session page for the full analysis.';
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

    bindOptimizationLiveDelegates(root);
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

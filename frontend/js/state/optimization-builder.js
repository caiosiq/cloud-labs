/** Client-side state for staged ensemble / optimization mode (UI only). */

import { getCatalogRow } from '../component-model.js';
import { store } from './store.js';

export function createOptimizationBuilder() {
    return {
        active: false,
        stage: 1,
        /** @type {Array<ObjectiveTermState>} */
        objectiveTerms: [],
        /** @type {Array<{
         *   id:string,
         *   tag_id:string,
         *   path:string,
         *   label:string,
         *   physical_type:string,
         *   unit?: string,
         *   boundsDelta?: boolean,
         *   boundsMin?: number,
         *   boundsMax?: number,
         * }>} */
        variables: [],
        maxEvals: 200,
        settleMs: 0,
        /** On abort / end: leave actuators at best-so-far (default). */
        keepBest: true,
        /** On abort: roll actuators back to start (x0) instead of keep_best. */
        rollbackOnFail: false,
        /** Guard steps vs applied start (VC / x0). */
        maxDeltaEnabled: true,
        /** Default matches motor search ±45° so safety does not silently clip the box. */
        maxDeltaDeg: 45.0,
        maxDeltaMm: 10.0,
        /** Show capture / kernel / actuate debug panel during + after runs. */
        showStageDebug: true,
        /** Sparse camera JPEG every N evals (plus first + new-best). */
        telemetryCameraEveryN: 5,
        /** Early-stop when total normalized loss ≤ this (0.1 ≈ 10% of FOV for align). */
        stopLossEnabled: true,
        stopLoss: 0.1,
        /** @type {Array<SolverBlockState>} */
        solverBlocks: [],
        runCompleted: false,
        lastSeenResultAt: null,
        maxEvalsForRun: 200,
        /** True while a run started in this session is in flight or awaiting results UI */
        awaitingRunResults: false,
        /** User explicitly opened persisted results (not auto-loaded on enter) */
        viewingLastRun: false,
        /** @type {string|null} Active Job Manager id for the current run */
        activeJobId: null,
        /** Reconcile bench to applied commit snapshot before job steps (default on). */
        reconcileBeforeRun: true,
        /** Request post-job commit via Job Manager on_success hook. */
        commitAfterRun: false,
        /** Optional message for post-job commit; auto-generated when empty. */
        commitMessage: '',
        /** Captured from job.result after run completes. */
        lastPostCommit: null,
        lastPostCommitError: null,
        postCommitFetched: false,
        /** Latest job.progress from polling (init / post_commit phases). */
        lastJobProgress: null,
        /** @type {string} Filter text for variables stage list */
        variableSearchQuery: '',
    };
}

/**
 * @typedef {Object} ObjectiveTermState
 * @property {string} id
 * @property {string} tag_id
 * @property {string} field
 * @property {string} metric
 * @property {number} weight
 * @property {string} label
 * @property {{x:number,y:number}} [centroidTarget]
 * @property {number} [normalizeMin]
 * @property {number} [normalizeMax]
 * @property {string} [kernelId] Edge catalog kernel id (torchscript_features)
 * @property {number|number[]} [featureIndex]
 */

/**
 * @typedef {Object} SolverBlockState
 * @property {string} id
 * @property {string[]} variable_ids
 * @property {number} max_evals
 * @property {number} passes
 * @property {number} trust_region_u
 * @property {number} rhobeg_u
 * @property {number} rhoend_u
 */

export const OPTIMIZATION_STAGES = [
    { id: 1, title: 'Objective', hint: 'Choose what the camera should optimize (one goal per term).' },
    { id: 2, title: 'Variables', hint: 'Pick tunables and set each search box (relative Δ or absolute).' },
    { id: 3, title: 'Tune', hint: 'Set weights and targets for each objective term.' },
    { id: 4, title: 'Solver', hint: 'Budget, settle, safety Max Δ, and COBYLA step fractions.' },
    { id: 5, title: 'Run', hint: 'Review capture → kernel → actuate plan, then start.' },
    { id: 6, title: 'Results', hint: 'Best loss, setpoints, stage debug, and iteration trace.' },
];

/** Fallback when a camera has no ``parameters.resolution`` in the library. */
export const ASSUMED_CAMERA_RESOLUTION = { width: 1920, height: 1080 };

/**
 * Parse library-declared camera resolution for a tag.
 * @returns {{ width: number, height: number, declared: string|null, assumed: boolean }}
 */
export function parseDeclaredCameraResolution(tagId) {
    const row = getCatalogRow(tagId);
    const raw = row?.parameters?.resolution;
    if (typeof raw === 'string') {
        const m = raw.trim().match(/^(\d+)\s*[x×]\s*(\d+)$/i);
        if (m) {
            const width = parseInt(m[1], 10);
            const height = parseInt(m[2], 10);
            if (width > 0 && height > 0) {
                return { width, height, declared: `${width}x${height}`, assumed: false };
            }
        }
    }
    return {
        width: ASSUMED_CAMERA_RESOLUTION.width,
        height: ASSUMED_CAMERA_RESOLUTION.height,
        declared: null,
        assumed: true,
    };
}

/** Default pixel target = geometric center of the camera frame. */
export function defaultCentroidTargetForCamera(tagId) {
    const { width, height } = parseDeclaredCameraResolution(tagId);
    return { x: width / 2, y: height / 2 };
}

/** Short Tune-stage note under target X/Y. */
export function cameraCenterDefaultHint(tagId) {
    const info = parseDeclaredCameraResolution(tagId);
    if (info.assumed) {
        return (
            'Defaults to center of camera. No declared resolution in the library; ' +
            `assuming ${ASSUMED_CAMERA_RESOLUTION.width}×${ASSUMED_CAMERA_RESOLUTION.height}.`
        );
    }
    return (
        'Defaults to center of camera. Declared resolution on this camera is ' +
        `${info.width}×${info.height}.`
    );
}

/**
 * Default search box for a tunable path (physical units).
 * Motors use a wide relative window so polarizer/Malus runs are not stuck at ±3°.
 * @param {string} path
 * @returns {{ delta: boolean, min: number, max: number, unit: string }}
 */
export function defaultSearchBoundsForPath(path) {
    const p = String(path || '');
    if (p.includes('nominal_motor_positions')) {
        return { delta: true, min: -45, max: 45, unit: 'deg' };
    }
    if (p.endsWith('.rotation') || p.includes('nominal_pose.rotation')) {
        return { delta: true, min: -15, max: 15, unit: 'deg' };
    }
    if (p.includes('nominal_pose')) {
        return { delta: true, min: -10, max: 10, unit: 'mm' };
    }
    return { delta: true, min: -5, max: 5, unit: '' };
}

/**
 * Attach search-bounds fields to a variable chip entry (idempotent).
 * @param {object} entry
 */
export function enrichVariableSearchBounds(entry) {
    if (!entry || typeof entry !== 'object') return entry;
    const defaults = defaultSearchBoundsForPath(entry.path);
    if (entry.boundsDelta == null) entry.boundsDelta = defaults.delta;
    if (entry.boundsMin == null) entry.boundsMin = defaults.min;
    if (entry.boundsMax == null) entry.boundsMax = defaults.max;
    if (!entry.unit) entry.unit = defaults.unit;
    return entry;
}

/** Physical span of a variable's authored search box. */
export function variableSearchSpan(v) {
    if (!v) return 0;
    const lo = Number(v.boundsMin);
    const hi = Number(v.boundsMax);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return 0;
    return Math.abs(hi - lo);
}

/**
 * Approximate initial COBYLA step in physical units: rhobeg_u × span.
 * @param {object} v
 * @param {number} rhobegU
 */
export function approxInitialStepPhysical(v, rhobegU) {
    const span = variableSearchSpan(v);
    const rho = Number(rhobegU);
    if (!(span > 0) || !Number.isFinite(rho) || rho <= 0) return null;
    return span * rho;
}

/**
 * Raise max-Δ safety so it does not clip inside the authored search box.
 * @param {ReturnType<typeof createOptimizationBuilder>} builder
 */
export function syncMaxDeltaWithVariableBounds(builder) {
    if (!builder || builder.maxDeltaEnabled === false) return;
    let needDeg = Number(builder.maxDeltaDeg ?? 0);
    let needMm = Number(builder.maxDeltaMm ?? 0);
    (builder.variables || []).forEach((v) => {
        enrichVariableSearchBounds(v);
        const lo = Number(v.boundsMin);
        const hi = Number(v.boundsMax);
        if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
        const reach = v.boundsDelta
            ? Math.max(Math.abs(lo), Math.abs(hi))
            : Math.max(Math.abs(hi - lo), Math.abs(lo), Math.abs(hi));
        const unit = String(v.unit || '');
        if (unit === 'deg') needDeg = Math.max(needDeg, reach);
        else if (unit === 'mm') needMm = Math.max(needMm, reach);
    });
    builder.maxDeltaDeg = needDeg;
    builder.maxDeltaMm = needMm;
}

/** Physical measurables that may appear as ensemble objective terms (not outputs like optimization score). */
export const MEASURABLE_OBJECTIVE_CONFIG = {
    camera_image: {
        label: 'Legacy camera centroid (no kernel)',
        defaultMetric: 'rms_distance_px',
        defaultWeight: 1.0,
    },
    output_power_readback_mw: {
        label: 'Hardware power readback',
        defaultMetric: 'one_minus_normalized',
        defaultWeight: 1.0,
    },
};

/**
 * Edge-catalog objective presets (Phase 5). Shown when the active edge lists the
 * kernel with ``artifact_present``. Terms capture ``measurables.camera_image``.
 *
 * @type {Record<string, {
 *   label: string,
 *   summary: string,
 *   metric: string,
 *   weight?: number,
 *   featureIndex?: number|number[],
 *   needsTargetPx?: boolean,
 *   normalizeMin?: number,
 *   normalizeMax?: number,
 * }>}
 */
export const EDGE_OBJECTIVE_PRESETS = {
    'builtin.roi_centroid': {
        label: 'Center beam on a pixel',
        summary:
            'Move the bright spot to a target pixel. Loss is FOV-normalized (~0–1) and goes bad if the beam leaves the camera.',
        kernelId: 'builtin.roi_centroid',
        metric: 'rms_distance',
        featureIndex: [0, 1],
        needsTargetPx: true,
        latchPeakRef: true,
        minPeakRatio: 0.5,
        peakFeatureIndex: 2,
        lossCap: 2,
        normalizeByFov: true,
    },
    'builtin.beam_power': {
        label: 'Maximize brightness',
        summary:
            'Increase total beam flux vs the first-eval reading (loss = ref/flux; ~1 at start, lower is brighter). No fixed normalize max.',
        kernelId: 'builtin.beam_power',
        metric: 'ratio_to_ref',
        featureIndex: 0,
        latchValueRef: true,
    },
    'minimize.beam_power': {
        label: 'Minimize brightness',
        summary:
            'Decrease total beam flux vs the first-eval reading (loss = flux/ref; ~1 at start, lower is darker). Same kernel as maximize.',
        kernelId: 'builtin.beam_power',
        metric: 'ratio_from_ref',
        featureIndex: 0,
        latchValueRef: true,
    },
    'builtin.gaussian_beam_fit': {
        label: 'Tighten the beam',
        summary: 'Make the spot narrower (width / FOV scale, ~0–1).',
        kernelId: 'builtin.gaussian_beam_fit',
        metric: 'minimize_value',
        featureIndex: 3,
        normalizeByFov: true,
        lossCap: 2,
    },
    'builtin.beam_shift': {
        label: 'Push beam off-center',
        summary: 'Maximize distance from the frame center — opposite of centering.',
        kernelId: 'builtin.beam_shift',
        metric: 'minimize_value',
        featureIndex: 2,
        // Negative weight: ensemble minimizes → maximize magnitude.
        weight: -1,
        normalizeByFov: true,
        lossCap: 2,
    },
};

/** @deprecated */
export const MEASURABLE_FIELD_PRESETS = Object.entries(MEASURABLE_OBJECTIVE_CONFIG).map(
    ([field, cfg]) => ({
        field,
        label: cfg.label,
        defaultMetric: cfg.defaultMetric,
        needsRecord: field === 'camera_image',
    }),
);

export function componentDisplayLabel(tagId) {
    if (!tagId) return '';
    const row = getCatalogRow(tagId);
    const name = row?.name || tagId;
    return name === tagId ? tagId : `${name} (${tagId})`;
}

export function getCatalogMeasurablesDeclaration(tagId) {
    const row = getCatalogRow(tagId);
    return row?.capabilities?.statecontrol?.measurables || {};
}

export function isOptimizableObjectiveField(fieldId) {
    return Object.prototype.hasOwnProperty.call(MEASURABLE_OBJECTIVE_CONFIG, fieldId);
}

/** True while the staged optimization builder is active (planning, not necessarily running). */
export function isOptimizationPlanningActive() {
    return Boolean(store.optimizationBuilder?.active);
}

/**
 * Canvas / sidebar highlight role for one tag during optimization planning.
 * @returns {'objective'|'variable'|'both'|'scope'|null}
 */
export function getOptimizationHighlightForTag(tagId) {
    const b = store.optimizationBuilder;
    if (!b?.active || !tagId) return null;
    const isObjective = b.objectiveTerms.some((t) => t.tag_id === tagId);
    const isVariable = b.variables.some((v) => v.tag_id === tagId);
    if (isObjective && isVariable) return 'both';
    if (isObjective) return 'objective';
    if (isVariable) return 'variable';
    return 'scope';
}

/** @param {'objective'|'variable'|'both'|'scope'} role */
export function optimizationHighlightColor(role) {
    switch (role) {
        case 'objective':
            return '#22d3ee';
        case 'variable':
            return '#fbbf24';
        case 'both':
            return '#34d399';
        default:
            return '#a78bfa';
    }
}

export function getOptimizableMeasurablesForTag(tagId) {
    const declared = getCatalogMeasurablesDeclaration(tagId);
    return Object.entries(declared)
        .filter(([fieldId]) => isOptimizableObjectiveField(fieldId))
        .map(([fieldId, descriptor]) => {
            const cfg = MEASURABLE_OBJECTIVE_CONFIG[fieldId];
            return {
                field: fieldId,
                descriptor: descriptor || {},
                label: measurableFieldLabel(fieldId, descriptor),
                defaultMetric: cfg.defaultMetric,
                defaultWeight: cfg.defaultWeight,
            };
        })
        .sort((a, b) => a.label.localeCompare(b.label));
}

export function measurableFieldLabel(fieldId, descriptor = {}) {
    const cfg = MEASURABLE_OBJECTIVE_CONFIG[fieldId];
    let label = cfg?.label || String(fieldId).replace(/_/g, ' ');
    const unit = descriptor?.unit;
    if (unit) label += ` (${unit})`;
    return label;
}

export function sanitizeObjectiveTerms(terms) {
    return terms.filter((t) => {
        if (t.kernelId) {
            return Boolean(t.tag_id && t.field === 'camera_image');
        }
        return getOptimizableMeasurablesForTag(t.tag_id).some((m) => m.field === t.field);
    });
}

export function createObjectiveTerm(tagId, field, existingTerms = []) {
    if (!isOptimizableObjectiveField(field)) return null;
    const cfg = MEASURABLE_OBJECTIVE_CONFIG[field];
    const declared = getCatalogMeasurablesDeclaration(tagId)[field] || {};
    const id = `term_${tagId}_${field}`;
    const existing = existingTerms.find((t) => t.id === id);
    const maxDefault =
        field === 'output_power_readback_mw' && Number(declared?.max) > 0
            ? Number(declared.max)
            : 1.0;
    return {
        id,
        tag_id: tagId,
        field,
        metric: cfg?.defaultMetric || 'one_minus_normalized',
        weight: existing?.weight ?? cfg?.defaultWeight ?? 0.5,
        label: `${componentDisplayLabel(tagId)} · ${measurableFieldLabel(field, declared)}`,
        centroidTarget:
            existing?.centroidTarget ??
            (field === 'camera_image'
                ? defaultCentroidTargetForCamera(tagId)
                : { x: 512, y: 384 }),
        normalizeMin: existing?.normalizeMin ?? 0,
        normalizeMax: existing?.normalizeMax ?? maxDefault,
    };
}

/**
 * Build an objective term from an edge-catalog kernel preset.
 * @param {string} tagId Camera component tag
 * @param {string} presetId Key in EDGE_OBJECTIVE_PRESETS (may differ from kernel id)
 * @param {Array} [existingTerms]
 */
export function createEdgeKernelObjectiveTerm(tagId, presetId, existingTerms = []) {
    const preset = EDGE_OBJECTIVE_PRESETS[presetId];
    if (!preset || !tagId) return null;
    const kernelId = preset.kernelId || presetId;
    const id = `term_${tagId}_${String(presetId).replace(/\./g, '_')}`;
    const existing = existingTerms.find((t) => t.id === id);
    const defaultTarget = defaultCentroidTargetForCamera(tagId);
    return {
        id,
        tag_id: tagId,
        field: 'camera_image',
        kernelId,
        presetId,
        metric: preset.metric,
        weight: existing?.weight ?? preset.weight ?? 1.0,
        label: `${componentDisplayLabel(tagId)} · ${preset.label}`,
        featureIndex: preset.featureIndex,
        centroidTarget: existing?.centroidTarget ?? defaultTarget,
        normalizeMin: existing?.normalizeMin ?? preset.normalizeMin ?? 0,
        normalizeMax: existing?.normalizeMax ?? preset.normalizeMax ?? 1,
        latchPeakRef: existing?.latchPeakRef ?? preset.latchPeakRef ?? false,
        minPeakRatio: existing?.minPeakRatio ?? preset.minPeakRatio ?? 0.5,
        peakFeatureIndex: existing?.peakFeatureIndex ?? preset.peakFeatureIndex ?? 2,
        latchValueRef: existing?.latchValueRef ?? preset.latchValueRef ?? false,
        lossCap: existing?.lossCap ?? preset.lossCap ?? 2,
        normalizeByFov: existing?.normalizeByFov ?? preset.normalizeByFov ?? false,
    };
}

export function createDefaultSolverBlock(variableIds, maxEvals = 200, id = 'block_main') {
    return {
        id,
        variable_ids: [...variableIds],
        max_evals: Math.min(100, maxEvals),
        passes: 1,
        trust_region_u: 0.3,
        rhobeg_u: 0.1,
        rhoend_u: 0.001,
    };
}

function _physicalTypeForVar(builder, vid) {
    const v = (builder.variables || []).find((x) => x.id === vid);
    return v?.physical_type || 'continuous';
}

/** Motors (continuous) and pose (invasive) must not share a solver block. */
function _splitMixedPhysicalBlocks(builder) {
    const next = [];
    (builder.solverBlocks || []).forEach((block) => {
        const motors = [];
        const pose = [];
        (block.variable_ids || []).forEach((vid) => {
            if (_physicalTypeForVar(builder, vid) === 'invasive_discrete') pose.push(vid);
            else motors.push(vid);
        });
        if (motors.length && pose.length) {
            if (motors.length) {
                next.push({
                    ...block,
                    id: `${block.id}_motors`,
                    variable_ids: motors,
                });
            }
            if (pose.length) {
                next.push({
                    ...block,
                    id: `${block.id}_pose`,
                    variable_ids: pose,
                });
            }
        } else {
            next.push(block);
        }
    });
    builder.solverBlocks = next.filter((b) => (b.variable_ids || []).length);
}

export function syncSolverBlocksFromVariables(builder) {
    const ids = builder.variables.map((v) => v.id);
    if (!ids.length) {
        builder.solverBlocks = [];
        return;
    }
    if (!builder.solverBlocks.length) {
        const motors = builder.variables
            .filter((v) => (v.physical_type || 'continuous') === 'continuous')
            .map((v) => v.id);
        const pose = builder.variables
            .filter((v) => v.physical_type === 'invasive_discrete')
            .map((v) => v.id);
        builder.solverBlocks = [];
        if (motors.length) {
            builder.solverBlocks.push(
                createDefaultSolverBlock(motors, builder.maxEvals, 'block_motors'),
            );
        }
        if (pose.length) {
            builder.solverBlocks.push(
                createDefaultSolverBlock(pose, builder.maxEvals, 'block_pose'),
            );
        }
        if (!builder.solverBlocks.length) {
            builder.solverBlocks = [createDefaultSolverBlock(ids, builder.maxEvals)];
        }
        return;
    }
    builder.solverBlocks.forEach((block) => {
        block.variable_ids = block.variable_ids.filter((vid) => ids.includes(vid));
    });
    const assigned = new Set(builder.solverBlocks.flatMap((b) => b.variable_ids));
    ids.filter((vid) => !assigned.has(vid)).forEach((vid) => {
        const ptype = _physicalTypeForVar(builder, vid);
        let target = builder.solverBlocks.find((b) => {
            const sample = b.variable_ids[0];
            if (!sample) return true;
            return _physicalTypeForVar(builder, sample) === ptype;
        });
        if (!target) {
            target = createDefaultSolverBlock(
                [],
                builder.maxEvals,
                ptype === 'invasive_discrete' ? 'block_pose' : 'block_motors',
            );
            builder.solverBlocks.push(target);
        }
        target.variable_ids.push(vid);
    });
    builder.solverBlocks = builder.solverBlocks.filter((b) => b.variable_ids.length);
    _splitMixedPhysicalBlocks(builder);
    if (!builder.solverBlocks.length) {
        builder.solverBlocks = [createDefaultSolverBlock(ids, builder.maxEvals)];
    }
}

export function buildObjectiveTermPayload(term) {
    if (term.kernelId) {
        const source = {
            tag_id: term.tag_id,
            kind: 'torchscript_features',
            kernel_id: term.kernelId,
            from: 'measurables.camera_image',
        };
        if (term.featureIndex != null) {
            source.feature_index = term.featureIndex;
        }
        if (term.centroidTarget && (term.metric === 'rms_distance' || term.metric === 'rms_distance_px')) {
            source.target_px =
                term.centroidTarget || defaultCentroidTargetForCamera(term.tag_id);
        }
        if (term.latchPeakRef !== false && (term.metric === 'rms_distance' || term.metric === 'rms_distance_px' || term.metric === 'beam_presence')) {
            source.latch_peak_ref = true;
            source.min_peak_ratio = Number(term.minPeakRatio ?? 0.5);
            source.peak_feature_index = Number(term.peakFeatureIndex ?? 2);
        }
        if (term.normalizeByFov || term.metric === 'rms_distance' || term.metric === 'rms_distance_px') {
            const { width, height } = parseDeclaredCameraResolution(term.tag_id);
            const diag = Math.hypot(width, height);
            const widthScale = Math.min(width, height) / 4;
            source.loss_cap = Number(term.lossCap ?? 2);
            if (term.metric === 'rms_distance' || term.metric === 'rms_distance_px') {
                source.rms_scale_px = diag;
                source.frame_hw = [height, width];
            }
            if (term.normalizeByFov && term.metric === 'minimize_value') {
                source.normalize_by_fov = true;
                source.value_scale_px = widthScale;
                source.frame_hw = [height, width];
                source.loss_cap = Number(term.lossCap ?? 2);
            }
        }
        if (term.metric === 'one_minus_normalized') {
            source.normalize = {
                min: Number(term.normalizeMin ?? 0),
                max: Number(term.normalizeMax ?? 1),
            };
        }
        if (term.metric === 'ratio_to_ref' || term.metric === 'ratio_from_ref' || term.latchValueRef) {
            source.latch_value_ref = true;
            if (term.featureIndex != null) {
                source.feature_index = term.featureIndex;
            } else {
                source.feature_index = 0;
            }
        }
        return {
            id: term.id,
            weight: term.weight,
            metric: term.metric || 'minimize_value',
            source,
        };
    }
    const cfg = MEASURABLE_OBJECTIVE_CONFIG[term.field];
    if (!cfg) return null;
    if (term.field === 'camera_image') {
        return {
            id: term.id,
            weight: term.weight,
            metric: term.metric || cfg.defaultMetric,
            source: {
                tag_id: term.tag_id,
                kind: 'derived_centroid',
                from: 'measurables.camera_image',
                target_px:
                    term.centroidTarget || defaultCentroidTargetForCamera(term.tag_id),
            },
        };
    }
    const path = `measurables.${term.field}`;
    return {
        id: term.id,
        weight: term.weight,
        metric: term.metric || cfg.defaultMetric,
        source: {
            tag_id: term.tag_id,
            kind: 'measurable_scalar',
            path,
            normalize: {
                min: Number(term.normalizeMin ?? 0),
                max: Number(term.normalizeMax ?? 1),
            },
        },
    };
}

export function buildObjectiveTermsPayload(builder) {
    return builder.objectiveTerms
        .map((t) => buildObjectiveTermPayload(t))
        .filter(Boolean);
}

export function buildSolverPayload(builder) {
    syncSolverBlocksFromVariables(builder);
    /** @type {object[]} */
    const constraints = [];
    if (builder.maxDeltaEnabled) {
        constraints.push({
            type: 'max_delta_from_start',
            enabled: true,
            limits: {
                deg: Number(builder.maxDeltaDeg ?? 45.0),
                mm: Number(builder.maxDeltaMm ?? 10.0),
            },
        });
    }
    return {
        type: 'block_cobyla',
        max_total_evals: builder.maxEvals,
        keep_best: builder.keepBest !== false,
        rollback_on_fail: Boolean(builder.rollbackOnFail),
        settle_ms: builder.settleMs,
        ...(builder.stopLossEnabled !== false &&
        Number.isFinite(Number(builder.stopLoss)) &&
        Number(builder.stopLoss) >= 0
            ? { stop_loss: Number(builder.stopLoss) }
            : {}),
        constraints,
        blocks: builder.solverBlocks.map((b) => ({
            id: b.id,
            variable_ids: [...b.variable_ids],
            max_evals: b.max_evals,
            trust_region_u: b.trust_region_u,
            rhobeg_u: b.rhobeg_u,
            rhoend_u: b.rhoend_u,
            passes: b.passes,
        })),
    };
}

/** Human-readable plan lines for the Run stage. */
export function buildRunPlanSummary(builder) {
    const lines = [];
    const kernels = builder.objectiveTerms
        .map((t) => t.kernelId)
        .filter(Boolean);
    const meas = builder.objectiveTerms.filter((t) => !t.kernelId);
    if (kernels.length) {
        lines.push(`Kernels: ${[...new Set(kernels)].join(', ')}`);
    }
    if (meas.length) {
        lines.push(
            `Legacy measurables: ${meas.map((t) => `${t.tag_id}.${t.field}`).join(', ')}`,
        );
    }
    lines.push(
        `Variables: ${builder.variables.length} · max evals ${builder.maxEvals} · settle ${builder.settleMs} ms`,
    );
    (builder.variables || []).forEach((v) => {
        enrichVariableSearchBounds(v);
        const unit = v.unit || '';
        const mode = v.boundsDelta !== false ? 'Δ' : 'abs';
        lines.push(
            `Search ${v.label || v.id}: ${mode} [${Number(v.boundsMin)}, ${Number(v.boundsMax)}] ${unit}`.trim(),
        );
    });
    if (builder.stopLossEnabled !== false && Number.isFinite(Number(builder.stopLoss))) {
        lines.push(
            `Early-stop when loss ≤ ${Number(builder.stopLoss)} (normalized; 0.1 ≈ 10% FOV for align)`,
        );
    } else {
        lines.push('Early-stop: off (run until max evals)');
    }
    lines.push(
        `Camera preview every ${Number(builder.telemetryCameraEveryN) > 0 ? Number(builder.telemetryCameraEveryN) : 5} evals (plus first + new-best)`,
    );
    if (builder.maxDeltaEnabled) {
        lines.push(
            `Max Δ from start: ±${builder.maxDeltaDeg} deg / ±${builder.maxDeltaMm} mm`,
        );
    } else {
        lines.push('Max Δ from start: off');
    }
    lines.push(
        builder.rollbackOnFail
            ? 'Abort → rollback to start (x0)'
            : builder.keepBest !== false
              ? 'Abort → keep best'
              : 'Abort → leave last trial',
    );
    lines.push('Loop stages: capture → kernel → actuate (debug in Results / live panel)');
    return lines;
}

/** Authoring-time objective graph for Phase E compiler.

 * Kernel terms use pass-through ``source`` (runtime IR) so ``ObjectiveGraphSpec``
 * accepts them (``extra=forbid`` rejects legacy ``kernel_id`` / ``feature_index``
 * siblings). Field terms stay on the measurable authoring path.
 */
export function buildAuthoringObjectiveGraph(builder) {
    return {
        version: 1,
        type: 'weighted_sum',
        minimize: true,
        terms: builder.objectiveTerms.map((t) => {
            if (t.kernelId) {
                const runtime = buildObjectiveTermPayload(t);
                if (!runtime) {
                    return {
                        id: t.id,
                        tag_id: t.tag_id,
                        field: t.field,
                        weight: t.weight,
                        metric: t.metric,
                    };
                }
                return {
                    id: runtime.id,
                    tag_id: t.tag_id,
                    weight: runtime.weight,
                    metric: runtime.metric,
                    source: runtime.source,
                };
            }
            const term = {
                id: t.id,
                tag_id: t.tag_id,
                field: t.field,
                weight: t.weight,
                metric: t.metric,
            };
            if (t.field === 'camera_image') {
                // Field-lowering path only accepts rms_distance_px.
                if (term.metric === 'rms_distance') term.metric = 'rms_distance_px';
                if (t.centroidTarget) term.target_px = { ...t.centroidTarget };
            }
            if (t.field === 'output_power_readback_mw' || t.metric === 'one_minus_normalized') {
                term.normalize = {
                    min: Number(t.normalizeMin ?? 0),
                    max: Number(t.normalizeMax ?? 1),
                };
            }
            return term;
        }),
    };
}

/** @deprecated use createObjectiveTerm */
export function buildObjectiveTermEntry(field, tagId, descriptor, existingTerms = []) {
    return createObjectiveTerm(tagId, field, existingTerms);
}

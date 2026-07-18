/** Client-side state for staged ensemble / optimization mode (UI only). */

import { getCatalogRow } from '../component-model.js';
import { store } from './store.js';

export function createOptimizationBuilder() {
    return {
        active: false,
        stage: 1,
        /** @type {Array<ObjectiveTermState>} */
        objectiveTerms: [],
        /** @type {Array<{id:string, tag_id:string, path:string, label:string, physical_type:string}>} */
        variables: [],
        maxEvals: 200,
        settleMs: 0,
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
    { id: 1, title: 'Objective', hint: 'Pick measurables; use Rec on camera terms to snapshot centroids.' },
    { id: 2, title: 'Variables', hint: 'Search or pick tunables — chips below or on canvas panels.' },
    { id: 3, title: 'Tune', hint: 'Set weights and targets for each objective term.' },
    { id: 4, title: 'Solver', hint: 'COBYLA budget and trust-region settings.' },
    { id: 5, title: 'Run', hint: 'Review the plan and start the session.' },
    { id: 6, title: 'Results', hint: 'Best loss, setpoints, and iteration trace.' },
];

/** Physical measurables that may appear as ensemble objective terms (not outputs like optimization score). */
export const MEASURABLE_OBJECTIVE_CONFIG = {
    camera_image: {
        label: 'Camera frame (centroid)',
        defaultMetric: 'rms_distance_px',
        defaultWeight: 1.0,
    },
    output_power_readback_mw: {
        label: 'Output power',
        defaultMetric: 'one_minus_normalized',
        defaultWeight: 1.0,
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
    return terms.filter((t) =>
        getOptimizableMeasurablesForTag(t.tag_id).some((m) => m.field === t.field),
    );
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
        centroidTarget: existing?.centroidTarget ?? { x: 512, y: 384 },
        normalizeMin: existing?.normalizeMin ?? 0,
        normalizeMax: existing?.normalizeMax ?? maxDefault,
    };
}

export function createDefaultSolverBlock(variableIds, maxEvals = 200) {
    return {
        id: 'block_main',
        variable_ids: [...variableIds],
        max_evals: Math.min(100, maxEvals),
        passes: 2,
        trust_region_u: 0.3,
        rhobeg_u: 0.1,
        rhoend_u: 0.001,
    };
}

export function syncSolverBlocksFromVariables(builder) {
    const ids = builder.variables.map((v) => v.id);
    if (!ids.length) {
        builder.solverBlocks = [];
        return;
    }
    if (!builder.solverBlocks.length) {
        builder.solverBlocks = [createDefaultSolverBlock(ids, builder.maxEvals)];
        return;
    }
    builder.solverBlocks.forEach((block) => {
        block.variable_ids = block.variable_ids.filter((vid) => ids.includes(vid));
    });
    const assigned = new Set(builder.solverBlocks.flatMap((b) => b.variable_ids));
    ids.filter((vid) => !assigned.has(vid)).forEach((vid) => {
        if (builder.solverBlocks[0]) builder.solverBlocks[0].variable_ids.push(vid);
    });
    builder.solverBlocks = builder.solverBlocks.filter((b) => b.variable_ids.length);
    if (!builder.solverBlocks.length) {
        builder.solverBlocks = [createDefaultSolverBlock(ids, builder.maxEvals)];
    }
}

export function buildObjectiveTermPayload(term) {
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
                target_px: term.centroidTarget || { x: 512, y: 384 },
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
    return {
        type: 'block_cobyla',
        max_total_evals: builder.maxEvals,
        keep_best: true,
        settle_ms: builder.settleMs,
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

/** Authoring-time objective graph for Phase E compiler (field-based terms). */
export function buildAuthoringObjectiveGraph(builder) {
    return {
        version: 1,
        type: 'weighted_sum',
        minimize: true,
        terms: builder.objectiveTerms.map((t) => {
            const term = {
                id: t.id,
                tag_id: t.tag_id,
                field: t.field,
                weight: t.weight,
                metric: t.metric,
            };
            if (t.field === 'camera_image' && t.centroidTarget) {
                term.target_px = { ...t.centroidTarget };
            }
            if (t.field === 'output_power_readback_mw') {
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

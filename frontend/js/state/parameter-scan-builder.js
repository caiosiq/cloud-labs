/**
 * Authoring state for Parameter Scan (1D tunable sweep) — sibling of OPTIMIZE,
 * not a COBYLA mode. MVP: one axis + one camera measure (beam CoM).
 */
import { store } from './store.js';
import { getCatalogRow } from '../component-model.js';
import { defaultSearchBoundsForPath } from './optimization-builder.js';

export const PARAMETER_SCAN_STAGES = [
    { id: 1, title: 'Measure', hint: 'Choose the camera and what to record at each step.' },
    { id: 2, title: 'Axis', hint: 'Pick one tunable to sweep (motors preferred for MVP).' },
    { id: 3, title: 'Range', hint: 'Set min / max / step (or point count) and settle time.' },
    { id: 4, title: 'Run', hint: 'Preview the grid, then start the scan loop.' },
    { id: 5, title: 'Results', hint: 'Table and plot of measurement vs tunable.' },
];

export const SCAN_MEASURE_PRESETS = [
    {
        id: 'beam_com',
        label: 'Beam CoM (full frame)',
        kernel_id: 'builtin.beam_com',
        feature_names: ['cx', 'cy', 'peak'],
    },
    {
        id: 'roi_centroid',
        label: 'ROI centroid',
        kernel_id: 'builtin.roi_centroid',
        feature_names: ['cx', 'cy', 'peak'],
    },
];

export function createParameterScanBuilder() {
    return {
        active: false,
        stage: 1,
        cameraTagId: '',
        measurePresetId: 'beam_com',
        /** Which kernel feature to report min/max for (e.g. ``cy``). */
        trackFeature: 'cy',
        /** @type {{ id: string, tag_id: string, path: string, label: string, physical_type?: string, unit?: string }|null} */
        axis: null,
        /** Tag id of expanded Axis accordion (canvas highlight while browsing). */
        axisBrowseTagId: '',
        /** Absolute values in physical units (same as SET_MOTOR_SETPOINT angle). */
        mode: 'absolute', // 'absolute' | 'relative'
        min: -5,
        max: 5,
        step: 1,
        /** When > 0, overrides step with linspace of N points inclusive. */
        pointCount: 0,
        settleMs: 500,
        restoreStart: true,
        running: false,
        abortRequested: false,
        /** @type {string|null} */
        activeScanId: null,
        /** @type {string|null} */
        lastCameraUrl: null,
        /** @type {[number, number]|null} */
        lastFrameHw: null,
        /** @type {number[]|null} last successful kernel features (keep crosshair between steps) */
        lastFeatures: null,
        axisSearchQuery: '',
        /** @type {Array<{ i: number, value: number, features: number[], feature_names: string[], ok: boolean, error?: string, cameraUrl?: string }>} */
        results: [],
        statusText: '',
        startValue: null,
    };
}

export function getScanMeasurePreset(id) {
    return SCAN_MEASURE_PRESETS.find((p) => p.id === id) || SCAN_MEASURE_PRESETS[0];
}

/** Ensure ``trackFeature`` is valid for the current measure preset. */
export function syncTrackFeatureForPreset(builder) {
    if (!builder) return;
    const preset = getScanMeasurePreset(builder.measurePresetId);
    const names = preset.feature_names || [];
    if (!names.includes(builder.trackFeature)) {
        builder.trackFeature = names.includes('cy')
            ? 'cy'
            : names[0] || 'cy';
    }
}

/**
 * Min / max of the tracked feature across successful scan rows.
 * @param {ReturnType<typeof createParameterScanBuilder>} b
 * @returns {{ feature: string, min: number|null, max: number|null, atMin: number|null, atMax: number|null, n: number }}
 */
export function trackFeatureStats(b) {
    const feature = String(b?.trackFeature || 'cy');
    const rows = Array.isArray(b?.results) ? b.results : [];
    let min = null;
    let max = null;
    let atMin = null;
    let atMax = null;
    let n = 0;
    rows.forEach((r) => {
        if (!r?.ok) return;
        const names = Array.isArray(r.feature_names) ? r.feature_names : [];
        let idx = names.indexOf(feature);
        if (idx < 0) {
            const preset = getScanMeasurePreset(b.measurePresetId);
            idx = (preset.feature_names || []).indexOf(feature);
        }
        if (idx < 0) return;
        const v = Number(r.features?.[idx]);
        if (!Number.isFinite(v)) return;
        n += 1;
        if (min == null || v < min) {
            min = v;
            atMin = Number(r.value);
        }
        if (max == null || v > max) {
            max = v;
            atMax = Number(r.value);
        }
    });
    return { feature, min, max, atMin, atMax, n };
}

/**
 * Canvas highlight role for Parameter Scan planning.
 * Purple dotted ring around the axis component (selected or accordion-focused).
 * @returns {'scan'|null}
 */
export function getParameterScanHighlightForTag(tagId) {
    const b = store.parameterScanBuilder;
    if (!b?.active || !tagId) return null;
    if (b.axis?.tag_id === tagId) return 'scan';
    if (b.axisBrowseTagId === tagId) return 'scan';
    return null;
}

export function isParameterScanPlanningActive() {
    return Boolean(store.parameterScanBuilder?.active);
}

/**
 * Inclusive linspace for the scan axis.
 * @param {ReturnType<typeof createParameterScanBuilder>} b
 * @returns {number[]}
 */
export function buildScanGrid(b) {
    const lo = Number(b.min);
    const hi = Number(b.max);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
    const nPts = Math.floor(Number(b.pointCount) || 0);
    if (nPts >= 2) {
        if (lo === hi) return [lo];
        const out = [];
        for (let i = 0; i < nPts; i += 1) {
            const t = i / (nPts - 1);
            out.push(lo + (hi - lo) * t);
        }
        return out.map((x) => roundScanValue(x));
    }
    const step = Number(b.step);
    if (!Number.isFinite(step) || step === 0) return [];
    const dir = hi >= lo ? 1 : -1;
    const absStep = Math.abs(step);
    const out = [];
    let v = lo;
    const guard = 500;
    for (let i = 0; i < guard; i += 1) {
        out.push(roundScanValue(v));
        if (dir > 0 && v >= hi - 1e-9) break;
        if (dir < 0 && v <= hi + 1e-9) break;
        v += dir * absStep;
        if (dir > 0 && v > hi + absStep * 0.5) break;
        if (dir < 0 && v < hi - absStep * 0.5) break;
    }
    const last = out[out.length - 1];
    if (out.length && Math.abs(last - hi) > 1e-6) {
        out.push(roundScanValue(hi));
    }
    return out;
}

function roundScanValue(x) {
    const n = Number(x);
    if (!Number.isFinite(n)) return n;
    return Math.round(n * 1e6) / 1e6;
}

/**
 * Apply relative offsets to an absolute base (current tunable).
 * @param {number[]} relativeGrid
 * @param {number} base
 */
export function absoluteGridFromRelative(relativeGrid, base) {
    const b0 = Number(base);
    if (!Number.isFinite(b0)) return relativeGrid.slice();
    return relativeGrid.map((d) => roundScanValue(b0 + Number(d)));
}

export function seedRangeFromAxisPath(path) {
    const d = defaultSearchBoundsForPath(path);
    return {
        min: d.min,
        max: d.max,
        step: Math.max(Math.abs(d.max - d.min) / 10, d.unit === 'deg' ? 0.5 : 0.5),
        unit: d.unit || '',
    };
}

export function scanAxisIsMotor(axis) {
    return String(axis?.path || '').includes('nominal_motor_positions');
}

export function motorIdFromAxisPath(path) {
    const m = String(path || '').match(/nominal_motor_positions\.([^.]+)/);
    return m ? m[1] : null;
}

export function componentLabel(tagId) {
    return getCatalogRow(tagId)?.name || tagId;
}

export function formatScanSummary(b) {
    const grid = buildScanGrid(b);
    const preset = getScanMeasurePreset(b.measurePresetId);
    const axis = b.axis;
    const lines = [
        `Measure: ${preset.label} on ${b.cameraTagId || '—'}`,
        `Track min/max: ${b.trackFeature || 'cy'}`,
        `Axis: ${axis?.label || '—'} (${b.mode})`,
        `Range: ${b.min} → ${b.max}` +
            (b.pointCount >= 2 ? ` · ${b.pointCount} pts` : ` · step ${b.step}`),
        `Points: ${grid.length} · settle ${b.settleMs} ms`,
        b.restoreStart ? 'Restore start value after scan' : 'Leave at last setpoint',
    ];
    return lines;
}

/**
 * Capability contract helpers (supports_teleop / supports_live_video / supports_optimization).
 */
import { getCatalogRow, catalogDeclaresTablePose } from './component-model.js';
import { normalizeCapabilities } from './component-state.js';
import { store } from './state/store.js';

function capsFor(tagIdOrRow) {
    const row = typeof tagIdOrRow === 'string' ? getCatalogRow(tagIdOrRow) : tagIdOrRow;
    return row?.capabilities || {};
}

function rowFor(tagIdOrRow) {
    return typeof tagIdOrRow === 'string' ? getCatalogRow(tagIdOrRow) : tagIdOrRow;
}

function tagIdFrom(tagIdOrRow) {
    if (typeof tagIdOrRow === 'string') return tagIdOrRow;
    return tagIdOrRow?.tag_id || null;
}

export function catalogIsPlaceableOnTable(tagIdOrRow) {
    const row = rowFor(tagIdOrRow);
    const props = row?.properties || {};
    return props.placeable_on_table === true;
}

/** Bench-fixed cameras, lasers, overhead streams — not breadboard optics. */
export function catalogIsFixedInstrument(tagIdOrRow) {
    const row = rowFor(tagIdOrRow);
    if (!row) return true;
    if (catalogIsPlaceableOnTable(row)) return false;
    const props = row.properties || {};
    if (props.fixture === true) return true;
    if (props.stream_source === 'overhead') return true;
    const t = row.type;
    if (t === 'OPTICAL_CAMERA' || t === 'CEILING_CAMERA' || t === 'LASER_SOURCE') {
        return true;
    }
    return false;
}

export function catalogHasMotors(tagIdOrRow) {
    const row = rowFor(tagIdOrRow);
    const mids = row?.motor_ids;
    return Array.isArray(mids) && mids.length > 0;
}

/** Breadboard-placeable table components eligible for NEWTON. */
export function catalogIsOptimizePlaceable(tagIdOrRow) {
    const tid = tagIdFrom(tagIdOrRow);
    if (!tid || !catalogDeclaresTablePose(tid)) return false;
    if (catalogIsFixedInstrument(tagIdOrRow)) return false;
    const row = rowFor(tagIdOrRow);
    const t = row?.type;
    if (t === 'OPTICAL_CAMERA' || t === 'CEILING_CAMERA' || t === 'LASER_SOURCE') {
        return catalogIsPlaceableOnTable(row);
    }
    return true;
}

const NEWTON_STRATEGY = {
    label: 'Newton Strategy',
    loss_metrics: ['centroid_match'],
    parameters: {
        axis: { default: 'x', enum: ['x', 'y'] },
        tolerance_ratio: { default: 0.05 },
        video_exposure: { default: 0.2 },
    },
};

const COBYLA_STRATEGY = {
    label: 'COBYLA Alignment',
    loss_metrics: ['reference_match'],
    parameters: {
        video_exposure: { default: 0.2 },
    },
};

/** NEWTON for all placeable targets; COBYLA only when ``motor_ids`` is set. */
export function buildOptimizeStrategies(tagIdOrRow) {
    if (!catalogIsOptimizePlaceable(tagIdOrRow)) return {};
    const strategies = { NEWTON: { ...NEWTON_STRATEGY } };
    if (catalogHasMotors(tagIdOrRow)) {
        strategies.COBYLA = { ...COBYLA_STRATEGY };
    }
    return strategies;
}

export function supportsTeleop(tagIdOrRow) {
    const caps = capsFor(tagIdOrRow);
    if (caps.supports_teleop === false) return false;
    if (caps.supports_teleop === true) return true;
    const prims = caps.primitives || [];
    return prims.includes('START_TELEOP') || Object.keys(caps.telemetry?.teleop || {}).length > 0;
}

export function supportsLiveVideo(tagIdOrRow) {
    const caps = capsFor(tagIdOrRow);
    if (caps.supports_live_video === false) return false;
    if (caps.supports_live_video === true) return true;
    const lf = caps.telemetry?.live_feed || {};
    return Object.keys(lf).length > 0;
}

export function supportsOptimization(tagIdOrRow) {
    const caps = capsFor(tagIdOrRow);
    if (caps.supports_optimization === false) return false;
    if (catalogIsOptimizePlaceable(tagIdOrRow)) return true;
    if (caps.supports_optimization === true) return true;
    return Array.isArray(caps.primitives) && caps.primitives.includes('OPTIMIZE');
}

/** Default gripper/overhead camera tags for optimize sensor picker. */
export function defaultOptimizeSensorPool() {
    return Object.entries(store.catalogMap || {})
        .filter(([, row]) => row?.type === 'OPTICAL_CAMERA')
        .map(([id]) => id)
        .filter((id) => supportsLiveVideo(store.catalogMap[id]) || id.startsWith('tag_2'));
}

/**
 * Per-panel live pose / motor + loss readout during autonomous OPTIMIZE.
 */
import { store } from './state/store.js';
import { getCatalogRow } from './component-model.js';

/** @type {Map<string, (tick: object|null) => void>} */
const _listeners = new Map();

export function registerOptimizeReadout(tagId, fn) {
    if (!tagId || typeof fn !== 'function') return;
    _listeners.set(tagId, fn);
    fn(store.optimizeTick);
}

export function notifyOptimizeReadouts(tagId) {
    const fn = _listeners.get(tagId);
    if (!fn) return;
    try { fn(store.optimizeTick); } catch (_e) { /* noop */ }
}

export function clearOptimizeReadouts(tagId) {
    if (tagId) _listeners.delete(tagId);
    else _listeners.clear();
}

export function fmtOptimizeNum(v, digits = 2) {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

const BASE_FIELDS = [
    { key: 'iteration', label: 'Step' },
    { key: 'loss', label: 'Loss' },
];

const POSE_FIELDS = [
    { key: 'x', label: 'X (mm)' },
    { key: 'y', label: 'Y (mm)' },
    { key: 'rotation', label: 'Rot (°)' },
];

export function resolveOptimizeMotorIds(tagId) {
    const row = getCatalogRow(tagId || store.optimizeActiveTarget);
    const mids = row?.motor_ids;
    return Array.isArray(mids) ? mids.map(String) : [];
}

export function optimizeReadoutMode(tagId) {
    const tid = tagId || store.optimizeActiveTarget;
    const strat = String(
        store.optimizeSession?.strategy
        || store.optimizeFormPrefs?.[tid]?.strategy
        || store.optimizeRunningStrategy
        || '',
    ).toUpperCase();
    if (!strat.includes('COBYLA')) return 'pose';
    return resolveOptimizeMotorIds(tid).length ? 'motors' : 'pose';
}

function motorFieldDefs(motorIds) {
    return motorIds.map((id) => ({
        key: `motor_${id}`,
        label: `M${id}`,
        motorId: String(id),
    }));
}

function dashAllFields(valueEls, mode, motorIds) {
    const tail = mode === 'motors' ? motorFieldDefs(motorIds) : POSE_FIELDS;
    [...BASE_FIELDS, ...tail].forEach(({ key }) => {
        if (valueEls[key]) valueEls[key].textContent = '—';
    });
}

/**
 * Shared Step / Loss / pose-or-motor grid for automated-actions + sidebar preview.
 * @returns {{ root: HTMLElement, update: Function, showIdle: Function }}
 */
export function createOptimizeLiveReadoutGrid({ motorIds = [] } = {}) {
    const box = document.createElement('div');
    box.className = 'opt-live-readout';
    box.hidden = true;

    const grid = document.createElement('div');
    grid.className = 'opt-live-readout__grid';
    box.appendChild(grid);

    const valueEls = {};
    let mode = 'pose';
    let activeMotorIds = motorIds.map(String);

    function ensureFields(nextMode, nextMotorIds) {
        const motorKeys = motorFieldDefs(nextMotorIds);
        const want = [...BASE_FIELDS, ...(nextMode === 'motors' ? motorKeys : POSE_FIELDS)];
        const wantKeys = new Set(want.map((f) => f.key));
        Object.keys(valueEls).forEach((k) => {
            if (!wantKeys.has(k)) delete valueEls[k];
        });
        grid.replaceChildren();
        want.forEach(({ key, label }) => {
            const cell = document.createElement('div');
            cell.className = 'opt-live-readout__cell';
            const lbl = document.createElement('span');
            lbl.className = 'opt-live-readout__label';
            lbl.textContent = label;
            const val = document.createElement('span');
            val.className = 'opt-live-readout__value';
            val.textContent = '—';
            cell.appendChild(lbl);
            cell.appendChild(val);
            grid.appendChild(cell);
            valueEls[key] = val;
        });
        mode = nextMode;
        activeMotorIds = nextMotorIds.map(String);
    }

    ensureFields('pose', activeMotorIds);

    function applyTick(tick) {
        if (valueEls.iteration) {
            valueEls.iteration.textContent = tick.iteration != null ? String(tick.iteration) : '—';
        }
        if (valueEls.loss) {
            valueEls.loss.textContent = fmtOptimizeNum(tick.loss, 4);
        }
        const pose = tick.pose || {};
        const mp = tick.motor_positions;
        if (mode === 'motors') {
            activeMotorIds.forEach((id) => {
                const el = valueEls[`motor_${id}`];
                if (!el) return;
                const raw = mp && typeof mp === 'object' ? mp[id] : undefined;
                el.textContent = fmtOptimizeNum(raw, 1);
            });
        } else if (valueEls.x) {
            valueEls.x.textContent = fmtOptimizeNum(pose.x, 2);
            valueEls.y.textContent = fmtOptimizeNum(pose.y, 2);
            valueEls.rotation.textContent = fmtOptimizeNum(pose.rotation, 2);
        }
    }

    function showIdle({ mode: modeOverride, motorIds: motorIdsOverride } = {}) {
        const nextMode = modeOverride || mode;
        const nextMotorIds = nextMode === 'motors'
            ? ((motorIdsOverride && motorIdsOverride.length)
                ? motorIdsOverride.map(String)
                : activeMotorIds)
            : activeMotorIds;
        if (nextMode !== mode || nextMotorIds.join(',') !== activeMotorIds.join(',')) {
            ensureFields(nextMode, nextMotorIds);
        }
        box.hidden = false;
        dashAllFields(valueEls, nextMode, nextMotorIds);
    }

    function update(tick, { clear = false, mode: modeOverride } = {}) {
        if (clear) {
            box.hidden = true;
            ensureFields('pose', motorIds.map(String));
            dashAllFields(valueEls, 'pose', activeMotorIds);
            return;
        }
        if (!tick) return;

        let nextMode = modeOverride || mode;
        if (!modeOverride && tick.motor_positions && Object.keys(tick.motor_positions).length) {
            nextMode = 'motors';
        }
        const mpKeys = tick.motor_positions ? Object.keys(tick.motor_positions) : activeMotorIds;
        const nextMotorIds = nextMode === 'motors'
            ? (mpKeys.length ? mpKeys : activeMotorIds)
            : activeMotorIds;
        if (nextMode !== mode || nextMotorIds.join(',') !== activeMotorIds.join(',')) {
            ensureFields(nextMode, nextMotorIds);
        }
        box.hidden = false;
        applyTick(tick);
    }

    return { root: box, update, showIdle };
}

function syncOptimizeReadout(tagId, tick, { update, showIdle }) {
    const mode = optimizeReadoutMode(tagId);
    if (tick) {
        update(tick, { mode });
    } else if (mode === 'motors') {
        showIdle({ mode, motorIds: resolveOptimizeMotorIds(tagId) });
    } else {
        update(null, { clear: true });
    }
}

export function buildOptimizeLiveReadout(tagId) {
    const motorIds = resolveOptimizeMotorIds(tagId);
    const { root, update, showIdle } = createOptimizeLiveReadoutGrid({ motorIds });
    registerOptimizeReadout(tagId, (tick) => {
        syncOptimizeReadout(tagId, tick, { update, showIdle });
    });
    syncOptimizeReadout(tagId, store.optimizeTick, { update, showIdle });
    return root;
}

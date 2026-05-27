import { store } from '../state/store.js';
import { getCatalogRow } from '../component-model.js';
import { sendCommand } from '../api/commands.js';
import { log } from '../ui/log.js';
import { supportsLiveVideo, supportsOptimization } from '../catalog-support.js';
import {
    armOptimizeSession,
    cobylaReferenceReady,
    defaultOptimizeSensor,
    getOptimizeFormPrefs,
    getOptimizeStrategies,
    isOptimizePendingOrActive,
    listOptimizeSensorCandidates,
    saveOptimizeFormPrefs,
    supportsOptimizeLiveCanvas,
} from '../optimize-session.js';
import {
    applyOptimizeSession,
    buildOptimizeSessionDescriptor,
    clearOptimizeSession,
} from '../ui/optimization-sidebar.js';
import { buildOptimizeLiveReadout, notifyOptimizeReadouts } from '../optimize-live-readout.js';
import { runButton } from './shared.js';

function labelForSensor(sensorId) {
    const row = getCatalogRow(sensorId);
    return (row && row.name) || sensorId;
}

function fieldRow(labelText, controlEl) {
    const wrap = document.createElement('div');
    wrap.className = 'opt-field';
    const label = document.createElement('label');
    label.className = 'opt-field__label';
    label.textContent = labelText;
    controlEl.className = `${controlEl.className} opt-field__control`.trim();
    wrap.appendChild(label);
    wrap.appendChild(controlEl);
    return wrap;
}

function styledSelect() {
    return document.createElement('select');
}

function styledNumberInput(step, min, max) {
    const input = document.createElement('input');
    input.type = 'number';
    input.step = String(step);
    if (min != null) input.min = String(min);
    if (max != null) input.max = String(max);
    return input;
}

function strategyParamDefaults(strategies, strategyKey) {
    const def = strategies[strategyKey] || {};
    return def.parameters && typeof def.parameters === 'object' ? def.parameters : {};
}

export function renderAutomatedActionsSection(ctx) {
    const inner = renderOptimize(ctx);
    if (!inner) return null;

    const block = document.createElement('div');
    block.className = 'automated-actions-section';
    block.dataset.section = 'automated-actions';

    const header = document.createElement('div');
    header.className = 'automated-actions-section__header';
    header.innerHTML =
        '<span class="material-icons-round automated-actions-section__icon">auto_awesome</span>'
        + '<span class="automated-actions-section__title">Automated Actions</span>';
    block.appendChild(header);
    block.appendChild(inner);
    return block;
}

export function renderOptimize(ctx) {
    const { tagId, placementState, catalogRow, hooks } = ctx;
    if (placementState === 'STORED') return null;
    if (!supportsOptimization(catalogRow || tagId)) return null;

    const strategies = getOptimizeStrategies(tagId);
    const strategyKeys = Object.keys(strategies);
    if (!strategyKeys.length) return null;

    const sensors = listOptimizeSensorCandidates(tagId);
    if (!sensors.length) return null;

    const saved = getOptimizeFormPrefs(tagId) || {};
    const running = isOptimizePendingOrActive(tagId);
    const liveCanvas = supportsOptimizeLiveCanvas(tagId);
    const liveVideo = supportsLiveVideo(catalogRow || tagId);

    const body = document.createElement('div');
    body.className = 'automated-actions-section__body';

    const hint = document.createElement('p');
    hint.className = 'opt-hint';
    function refreshHint() {
        if (running && store.optimizeRunningStrategy) {
            hint.textContent = `Running ${store.optimizeRunningStrategy} — live values update here and in the sidebar preview.`;
            hint.style.color = '#c4b5fd';
            return;
        }
        hint.style.color = '';
        if (liveVideo) {
            hint.textContent =
                'Starts optimization on this component; live sensor feed appears in the sidebar.';
        } else if (stratSelect?.value === 'COBYLA') {
            hint.textContent = 'Live motor steps and loss update here and in the sidebar preview.';
        } else if (liveCanvas) {
            hint.textContent = 'Live pose on canvas; loss streams to the sidebar chart.';
        } else {
            hint.textContent = 'Blocking optimize — status spinner until complete.';
        }
    }
    body.appendChild(hint);

    const sensorSelect = styledSelect();
    sensors.forEach((sid) => {
        const opt = document.createElement('option');
        opt.value = sid;
        opt.textContent = labelForSensor(sid);
        sensorSelect.appendChild(opt);
    });
    sensorSelect.value =
        saved.sensor && sensors.includes(saved.sensor)
            ? saved.sensor
            : (defaultOptimizeSensor(tagId) || sensors[0]);
    sensorSelect.disabled = running;
    body.appendChild(fieldRow('Sensor camera', sensorSelect));

    const stratSelect = styledSelect();
    strategyKeys.forEach((key) => {
        const def = strategies[key] || {};
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = def.label || key;
        stratSelect.appendChild(opt);
    });
    stratSelect.value =
        saved.strategy && strategyKeys.includes(saved.strategy)
            ? saved.strategy
            : strategyKeys[0];
    stratSelect.disabled = running;
    body.appendChild(fieldRow('Strategy', stratSelect));
    refreshHint();

    const metricSelect = styledSelect();
    function refreshMetrics() {
        metricSelect.replaceChildren();
        const def = strategies[stratSelect.value] || {};
        const metrics = Array.isArray(def.loss_metrics) ? def.loss_metrics : ['centroid_match'];
        const preferred = saved.lossMetric && metrics.includes(saved.lossMetric)
            ? saved.lossMetric
            : metrics[0];
        metrics.forEach((m) => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m.replace(/_/g, ' ');
            metricSelect.appendChild(opt);
        });
        metricSelect.value = preferred;
    }
    metricSelect.disabled = running;
    stratSelect.addEventListener('change', () => {
        refreshMetrics();
        refreshStrategyFields();
        refreshStartState();
        refreshHint();
        persistForm();
        notifyOptimizeReadouts(tagId);
    });
    sensorSelect.addEventListener('change', () => {
        refreshStartState();
        persistForm();
    });
    metricSelect.addEventListener('change', persistForm);
    refreshMetrics();
    body.appendChild(fieldRow('Loss metric', metricSelect));

    const axisSelect = styledSelect();
    ['x', 'y'].forEach((axis) => {
        const opt = document.createElement('option');
        opt.value = axis;
        opt.textContent = axis.toUpperCase();
        axisSelect.appendChild(opt);
    });
    axisSelect.value =
        saved.axis === 'y' || saved.axis === 'x' ? saved.axis : 'x';
    axisSelect.disabled = running;
    const axisRow = fieldRow('Table axis (pixel X)', axisSelect);

    const toleranceInput = styledNumberInput(0.001, 0.001, 0.5);
    toleranceInput.value = String(
        typeof saved.toleranceRatio === 'number' && Number.isFinite(saved.toleranceRatio)
            ? saved.toleranceRatio
            : 0.05,
    );
    toleranceInput.disabled = running;
    const toleranceRow = fieldRow('Tolerance ratio', toleranceInput);

    const exposureInput = styledNumberInput(0.001, 0.001, 30);
    exposureInput.value = String(
        typeof saved.videoExposure === 'number' && Number.isFinite(saved.videoExposure)
            ? saved.videoExposure
            : 0.2,
    );
    exposureInput.disabled = running;
    const exposureRow = fieldRow('Video exposure (s)', exposureInput);

    function refreshStrategyFields() {
        const strategy = stratSelect.value;
        const defaults = strategyParamDefaults(strategies, strategy);
        const isNewton = strategy === 'NEWTON';
        axisRow.hidden = !isNewton;
        toleranceRow.hidden = !isNewton;
        if (isNewton) {
            const axisDef = defaults.axis || {};
            const allowed = Array.isArray(axisDef.enum) ? axisDef.enum : ['x', 'y'];
            if (!allowed.includes(axisSelect.value)) {
                axisSelect.value = allowed.includes(saved.axis) ? saved.axis : (allowed[0] || 'x');
            }
            if (typeof defaults.tolerance_ratio?.default === 'number') {
                toleranceInput.placeholder = String(defaults.tolerance_ratio.default);
            }
        }
        if (typeof defaults.video_exposure?.default === 'number' && !saved.videoExposure) {
            exposureInput.placeholder = String(defaults.video_exposure.default);
        }
    }

    axisSelect.addEventListener('change', persistForm);
    toleranceInput.addEventListener('change', persistForm);
    exposureInput.addEventListener('change', persistForm);
    refreshStrategyFields();
    body.appendChild(axisRow);
    body.appendChild(toleranceRow);
    body.appendChild(exposureRow);

    const refWarning = document.createElement('p');
    refWarning.className = 'opt-ref-warning';
    refWarning.hidden = true;
    body.appendChild(refWarning);

    body.appendChild(buildOptimizeLiveReadout(tagId));

    const startBtn = runButton(
        running ? 'Optimizing…' : 'Start Optimization',
        running ? 'hourglass_top' : 'play_arrow',
    );
    startBtn.classList.remove('btn-primary');
    startBtn.classList.add('btn-optimize');

    function persistForm() {
        const prefs = {
            sensor: sensorSelect.value,
            strategy: stratSelect.value,
            lossMetric: metricSelect.value,
            videoExposure: parseFloat(exposureInput.value),
        };
        if (stratSelect.value === 'NEWTON') {
            prefs.axis = axisSelect.value;
            prefs.toleranceRatio = parseFloat(toleranceInput.value);
        }
        saveOptimizeFormPrefs(tagId, prefs);
    }

    function refreshStartState() {
        if (running) {
            startBtn.disabled = true;
            refWarning.hidden = true;
            return;
        }
        const sensorId = sensorSelect.value;
        const strategy = stratSelect.value;
        const needsRef = strategy === 'COBYLA';
        const ready = !needsRef || cobylaReferenceReady();
        startBtn.disabled = !ready;
        if (needsRef && !ready) {
            refWarning.hidden = false;
            refWarning.textContent =
                'COBYLA needs a lab optimization reference — record a camera capture and pin it from measurables.';
        } else {
            refWarning.hidden = true;
            refWarning.textContent = '';
        }
    }

    refreshStartState();

    startBtn.onclick = async () => {
        if (running) return;

        const sensorId = sensorSelect.value;
        const strategy = stratSelect.value;
        const lossMetric = metricSelect.value;

        if (strategy === 'COBYLA' && !cobylaReferenceReady()) {
            const logFn = hooks?.log || log;
            logFn('COBYLA blocked: pin a lab optimization reference first.', 'error');
            refreshStartState();
            return;
        }

        persistForm();
        store.optimizeRunningStrategy = strategies[strategy]?.label || strategy;

        const params = {
            strategy,
            sensor_component: sensorId,
            loss_metric: lossMetric,
            video_exposure: parseFloat(exposureInput.value) || 0.2,
        };
        if (strategy === 'NEWTON') {
            params.axis = axisSelect.value === 'y' ? 'y' : 'x';
            params.tolerance_ratio = parseFloat(toleranceInput.value) || 0.05;
        }

        store.optimizeActiveTarget = tagId;
        store.optimizeActiveSensor = sensorId;
        applyOptimizeSession(buildOptimizeSessionDescriptor(tagId, params));
        armOptimizeSession(tagId);

        const logFn = hooks?.log || log;
        logFn(`OPTIMIZE ${tagId} via ${sensorId} (${strategy})`, 'info');

        const result = await sendCommand({
            action: 'OPTIMIZE',
            target_id: tagId,
            parameters: params,
        });

        if (!result?.ok) {
            store.optimizeActiveTarget = null;
            store.optimizeActiveSensor = null;
            store.optimizeRunningStrategy = null;
            clearOptimizeSession();
        } else {
            startBtn.disabled = true;
            startBtn.innerHTML =
                '<span class="material-icons-round" style="font-size:14px;vertical-align:middle;">hourglass_top</span> Optimizing…';
            hint.textContent = `Running ${store.optimizeRunningStrategy} — live values update here and in the sidebar preview.`;
            hint.style.color = '#c4b5fd';
            refWarning.hidden = true;
            notifyOptimizeReadouts(tagId);
        }
    };

    body.appendChild(startBtn);
    return body;
}

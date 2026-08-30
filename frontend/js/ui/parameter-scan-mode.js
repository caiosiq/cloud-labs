/**
 * Parameter Scan mode — 1D tunable sweep UI (sibling of Optimization).
 *
 * Authoring mirrors OPTIMIZE stages; the loop is a deterministic for-loop
 * (set → settle → RECORD → EVAL_KERNEL), not COBYLA. MVP runs motors via
 * SET_MOTOR_SETPOINT from the Twin client; pose axes are authorable but
 * not yet runnable.
 */
import { store } from '../state/store.js';
import { getCatalogRow, isBreadboardIntent } from '../component-model.js';
import { applyComponentTelemetryFromServer, getTunables, isLiveFeedActive } from '../component-state.js';
import { sendCommand } from '../api/commands.js';
import { labClient } from '../cloudlabs/client.js';
import { probeKernel } from '../api/kernels.js';
import { fetchLabState } from '../state/lab-state.js';
import { componentDisplayLabel } from '../state/optimization-builder.js';
import {
    PARAMETER_SCAN_STAGES,
    SCAN_MEASURE_PRESETS,
    createParameterScanBuilder,
    getScanMeasurePreset,
    buildScanGrid,
    absoluteGridFromRelative,
    seedRangeFromAxisPath,
    scanAxisIsMotor,
    motorIdFromAxisPath,
    formatScanSummary,
    syncTrackFeatureForPreset,
    trackFeatureStats,
} from '../state/parameter-scan-builder.js';
import {
    newParameterScanId,
    parameterScanSessionHref,
    publishParameterScanSnapshot,
} from '../state/parameter-scan-bus.js';
import { getSelectedBackendId, withBackendQuery } from '../state/backend-selection.js';
import { activateWorkspaceTab } from './workspace-tabs.js';
import { render } from '../canvas/render.js';
import { syncComponentSidebarHighlights } from './updateUI.js';
import { syncBenchChromeHighlights } from './bench-chrome-bar.js';

let _log = () => {};

function builder() {
    if (!store.parameterScanBuilder) {
        store.parameterScanBuilder = createParameterScanBuilder();
    }
    return store.parameterScanBuilder;
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function refreshScanVisuals() {
    render();
    syncComponentSidebarHighlights();
    syncBenchChromeHighlights();
}

function listCameraTags() {
    const comps = store.labState?.components || {};
    const tags = [];
    Object.keys(comps).forEach((tagId) => {
        const row = getCatalogRow(tagId);
        const meas = row?.capabilities?.statecontrol?.measurables || {};
        if (meas.camera_image || row?.type === 'CAMERA' || /camera/i.test(row?.name || '')) {
            tags.push(tagId);
        }
    });
    // Also include any PLACED camera-like from catalog with camera_image capability
    Object.keys(store.catalogMap || {}).forEach((tagId) => {
        if (tags.includes(tagId)) return;
        const row = getCatalogRow(tagId);
        const meas = row?.capabilities?.statecontrol?.measurables || {};
        if (meas.camera_image) tags.push(tagId);
    });
    return tags.sort();
}

function listTunableOptions(tagId) {
    const comp = store.labState?.components?.[tagId];
    const shortName = componentDisplayLabel(tagId);
    const options = [];
    const tun = getTunables(comp) || {};
    const motors = tun.nominal_motor_positions;
    const catalogMids = getCatalogRow(tagId)?.motor_ids;
    const midKeys = new Set();
    if (Array.isArray(catalogMids)) catalogMids.forEach((m) => midKeys.add(String(m)));
    if (motors && typeof motors === 'object') {
        Object.keys(motors).forEach((m) => midKeys.add(String(m)));
    }
    midKeys.forEach((mid) => {
        options.push({
            id: `scan_${tagId}_m${mid}`,
            tag_id: tagId,
            path: `tunables.nominal_motor_positions.${mid}`,
            label: `${shortName} · motor ${mid}`,
            physical_type: 'continuous',
            unit: 'deg',
        });
    });
    const pose = tun.nominal_pose;
    if (pose && typeof pose === 'object') {
        ['x', 'y', 'rotation'].forEach((axis) => {
            if (axis in pose) {
                options.push({
                    id: `scan_${tagId}_${axis}`,
                    tag_id: tagId,
                    path: `tunables.nominal_pose.${axis}`,
                    label: `${shortName} · pose ${axis}`,
                    physical_type: 'invasive_discrete',
                    unit: axis === 'rotation' ? 'deg' : 'mm',
                });
            }
        });
    }
    return options;
}

function shortTunableLabel(opt) {
    const path = String(opt?.path || '');
    const tail = path.split('.').pop() || '';
    if (path.includes('motor')) return `Motor ${tail}`;
    if (tail === 'rotation') return 'Rotation';
    if (tail === 'x' || tail === 'y') return tail.toUpperCase();
    return tail.charAt(0).toUpperCase() + tail.slice(1);
}

function selectAxis(entry) {
    const b = builder();
    if (!entry) {
        b.axis = null;
        renderAll();
        refreshScanVisuals();
        return;
    }
    b.axis = { ...entry };
    b.axisBrowseTagId = entry.tag_id;
    const seeded = seedRangeFromAxisPath(entry.path);
    b.min = seeded.min;
    b.max = seeded.max;
    b.step = seeded.step;
    b.unit = seeded.unit;
    if (b.mode !== 'absolute' && b.mode !== 'relative') b.mode = 'relative';
    renderAll();
    refreshScanVisuals();
}

function cameraImageUrl(tagId, epochMs) {
    const t = epochMs || Date.now();
    return withBackendQuery(
        `/api/components/${encodeURIComponent(tagId)}/camera-image?t=${encodeURIComponent(t)}`,
    );
}

function publishScan(partial) {
    const b = builder();
    const scanId = b.activeScanId;
    if (!scanId) return;
    const preset = getScanMeasurePreset(b.measurePresetId);
    const stats = trackFeatureStats(b);
    const lastFeatures =
        partial.lastFeatures !== undefined
            ? partial.lastFeatures
            : b.lastFeatures != null
              ? b.lastFeatures
              : null;
    const lastCameraUrl =
        partial.lastCameraUrl || b.lastCameraUrl || null;
    const lastFrameHw =
        partial.lastFrameHw || b.lastFrameHw || null;
    publishParameterScanSnapshot(scanId, {
        status: b.running ? 'running' : partial.status || 'idle',
        step: partial.step ?? b.results.length,
        total: partial.total ?? null,
        cameraTagId: b.cameraTagId,
        axisLabel: b.axis?.label || '',
        axisPath: b.axis?.path || '',
        kernelId: preset.kernel_id,
        measureLabel: preset.label,
        feature_names: preset.feature_names,
        trackFeature: b.trackFeature,
        trackStats: stats,
        results: b.results.slice(),
        lastCameraUrl,
        lastFrameHw,
        lastFeatures,
        statusText: partial.statusText ?? b.statusText,
        ...partial,
        // Re-assert after spread so callers cannot accidentally wipe overlays.
        lastCameraUrl: partial.lastCameraUrl !== undefined ? partial.lastCameraUrl : lastCameraUrl,
        lastFrameHw: partial.lastFrameHw !== undefined ? partial.lastFrameHw : lastFrameHw,
        lastFeatures: partial.lastFeatures !== undefined ? partial.lastFeatures : lastFeatures,
    });
}

function openScanSessionPage(scanId) {
    const href = parameterScanSessionHref({
        scanId,
        backendId: getSelectedBackendId(),
    });
    window.open(href, '_blank', 'noopener');
}

function readCurrentAxisValue(axis) {
    if (!axis) return null;
    const comp = store.labState?.components?.[axis.tag_id];
    const tun = getTunables(comp) || {};
    if (scanAxisIsMotor(axis)) {
        const mid = motorIdFromAxisPath(axis.path);
        const v = tun.nominal_motor_positions?.[mid];
        return Number.isFinite(Number(v)) ? Number(v) : null;
    }
    if (String(axis.path).includes('nominal_pose')) {
        const key = axis.path.split('.').pop();
        const v = tun.nominal_pose?.[key];
        return Number.isFinite(Number(v)) ? Number(v) : null;
    }
    return null;
}

function setStatus(text) {
    const b = builder();
    b.statusText = text || '';
    const el = document.getElementById('scan-mode-run-status');
    if (el) el.textContent = b.statusText;
}

function renderStageNav() {
    const b = builder();
    const nav = document.getElementById('scan-mode-stage-nav');
    const hint = document.getElementById('scan-mode-stage-hint');
    if (!nav) return;
    nav.innerHTML = PARAMETER_SCAN_STAGES.map((s) => {
        const sel = s.id === b.stage ? ' is-active' : '';
        const done = s.id < b.stage ? ' is-done' : '';
        return `<button type="button" class="opt-stage-tab${sel}${done}" data-scan-stage="${s.id}">${s.id}. ${s.title}</button>`;
    }).join('');
    nav.querySelectorAll('[data-scan-stage]').forEach((btn) => {
        btn.addEventListener('click', () => {
            if (builder().running) return;
            builder().stage = Number(btn.getAttribute('data-scan-stage'));
            renderAll();
        });
    });
    if (hint) {
        const stage = PARAMETER_SCAN_STAGES.find((s) => s.id === b.stage);
        hint.textContent = stage?.hint || '';
    }
}

function renderMeasureStage(body) {
    const b = builder();
    syncTrackFeatureForPreset(b);
    const cams = listCameraTags();
    const preset = getScanMeasurePreset(b.measurePresetId);
    const featNames = preset.feature_names || ['cx', 'cy', 'peak'];
    body.innerHTML = `
        <div class="opt-hint" style="padding:0 12px 8px;">
            Pick the science camera and kernel. Each scan step will RECORD then eval the kernel
            (same science path as Measure beam CoM / OPTIMIZE — not the live preview JPEG).
        </div>
        <div style="padding:0 12px 10px;">
            <label class="scan-field">
                <span>Camera</span>
                <select data-scan-camera>
                    <option value="">— select —</option>
                    ${cams
                        .map(
                            (t) =>
                                `<option value="${t}" ${b.cameraTagId === t ? 'selected' : ''}>${getCatalogRow(t)?.name || t} (${t})</option>`,
                        )
                        .join('')}
                </select>
            </label>
            <label class="scan-field" style="margin-top:8px;">
                <span>Measure</span>
                <select data-scan-measure>
                    ${SCAN_MEASURE_PRESETS.map(
                        (p) =>
                            `<option value="${p.id}" ${b.measurePresetId === p.id ? 'selected' : ''}>${p.label}</option>`,
                    ).join('')}
                </select>
            </label>
            <label class="scan-field" style="margin-top:8px;">
                <span>Track min / max of</span>
                <select data-scan-track>
                    ${featNames
                        .map(
                            (n) =>
                                `<option value="${n}" ${b.trackFeature === n ? 'selected' : ''}>${n}</option>`,
                        )
                        .join('')}
                </select>
            </label>
            <p class="opt-hint" style="margin-top:6px;">
                After the scan, Results will show the minimum and maximum <code>${_escape(b.trackFeature)}</code>
                across all successful steps (and the tunable values where they occurred).
            </p>
        </div>
        <div class="scan-nav-row">
            <button type="button" class="btn btn-primary btn--sidebar-inline" data-scan-next ${b.cameraTagId ? '' : 'disabled'}>Next: Axis</button>
        </div>
    `;
    body.querySelector('[data-scan-camera]')?.addEventListener('change', (e) => {
        b.cameraTagId = e.target.value;
        renderAll();
    });
    body.querySelector('[data-scan-measure]')?.addEventListener('change', (e) => {
        b.measurePresetId = e.target.value;
        syncTrackFeatureForPreset(b);
        renderAll();
    });
    body.querySelector('[data-scan-track]')?.addEventListener('change', (e) => {
        b.trackFeature = e.target.value;
        renderAll();
    });
    body.querySelector('[data-scan-next]')?.addEventListener('click', () => {
        if (!b.cameraTagId) return;
        b.stage = 2;
        renderAll();
    });
}

function renderAxisStage(body) {
    const b = builder();
    body.innerHTML = '';

    const hint = document.createElement('div');
    hint.className = 'opt-hint';
    hint.style.cssText = 'padding:0 12px 8px;';
    hint.innerHTML =
        'Pick a component, then a tunable (same layout as Optimization Variables). ' +
        'MVP <strong>runs</strong> motor axes; pose axes are selectable but not runnable yet.';
    body.appendChild(hint);

    if (b.axis) {
        const bar = document.createElement('div');
        bar.className = 'opt-selected-bar opt-selected-bar--variables';
        bar.style.margin = '0 12px 10px';
        bar.innerHTML = `
            <div class="opt-selected-bar__head">
                <span class="opt-field-label" style="margin:0">Selected axis</span>
                <span class="opt-badge">1</span>
            </div>
            <div class="opt-selected-groups">
                <div class="opt-selected-group">
                    <div class="opt-selected-group__name">${_escape(componentDisplayLabel(b.axis.tag_id))}</div>
                    <div class="opt-chip-row">
                        <span class="opt-chip is-selected opt-chip--compact" title="${_escape(b.axis.label)}">
                            <span class="opt-chip-text">${_escape(shortTunableLabel(b.axis))}${scanAxisIsMotor(b.axis) ? '' : ' · pose'}</span>
                            <button type="button" class="opt-chip-remove" data-scan-clear-axis title="Clear">×</button>
                        </span>
                    </div>
                </div>
            </div>
            <p class="opt-hint" style="margin:6px 0 0;"><code>${_escape(b.axis.path)}</code></p>
        `;
        body.appendChild(bar);
        bar.querySelector('[data-scan-clear-axis]')?.addEventListener('click', () => selectAxis(null));
    }

    const searchWrap = document.createElement('div');
    searchWrap.className = 'opt-search-wrap';
    searchWrap.style.margin = '0 12px 8px';
    searchWrap.innerHTML = `
        <span class="material-icons-round opt-search-icon">search</span>
        <input type="search" class="opt-input opt-search" data-scan-axis-filter placeholder="Filter components or tunables…" autocomplete="off" />
    `;
    body.appendChild(searchWrap);

    const list = document.createElement('div');
    list.className = 'opt-comp-list';
    list.style.padding = '0 12px';

    const comps = store.labState?.components || {};
    const entries = [];
    Object.entries(comps).forEach(([tagId, comp]) => {
        if (!comp) return;
        // Prefer breadboard/placed parts (same as OPTIMIZE); still include others with motors.
        const opts = listTunableOptions(tagId);
        if (!opts.length) return;
        if (!isBreadboardIntent(comp) && !opts.some((o) => scanAxisIsMotor(o))) return;
        const compLabel = componentDisplayLabel(tagId);
        entries.push({
            tagId,
            compLabel,
            opts,
            searchText: [compLabel, tagId, ...opts.map((o) => `${o.label} ${shortTunableLabel(o)}`)]
                .join(' ')
                .toLowerCase(),
        });
    });
    entries.sort((a, c) => a.compLabel.localeCompare(c.compLabel, undefined, { numeric: true }));

    entries.forEach(({ tagId, compLabel, opts, searchText }) => {
        const selected = opts.some((o) => b.axis?.id === o.id);
        const details = document.createElement('details');
        details.className = 'opt-comp-accordion';
        details.dataset.searchText = searchText;
        if (selected || (b.axisSearchQuery && searchText.includes(String(b.axisSearchQuery).trim().toLowerCase()))) {
            details.open = true;
        }
        const summary = document.createElement('summary');
        summary.innerHTML = `<span>${_escape(compLabel)}</span><span class="opt-badge">${selected ? '1' : '0'}/${opts.length}</span>`;
        details.appendChild(summary);

        const inner = document.createElement('div');
        inner.className = 'opt-comp-accordion__body';
        const chipRow = document.createElement('div');
        chipRow.className = 'opt-chip-row';
        opts.forEach((opt) => {
            const isSel = b.axis?.id === opt.id;
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'opt-chip' + (isSel ? ' is-selected' : '');
            chip.textContent =
                shortTunableLabel(opt) + (scanAxisIsMotor(opt) ? '' : ' · pose');
            chip.title = opt.label + (scanAxisIsMotor(opt) ? '' : ' (not runnable in MVP)');
            chip.onclick = () => selectAxis(opt);
            chipRow.appendChild(chip);
        });
        inner.appendChild(chipRow);
        details.appendChild(inner);
        details.addEventListener('toggle', () => {
            if (details.open) {
                b.axisBrowseTagId = tagId;
            } else if (b.axisBrowseTagId === tagId && b.axis?.tag_id !== tagId) {
                b.axisBrowseTagId = b.axis?.tag_id || '';
            }
            refreshScanVisuals();
        });
        list.appendChild(details);
    });

    if (!entries.length) {
        const empty = document.createElement('p');
        empty.className = 'opt-hint';
        empty.textContent = 'No components with tunables found. Place a part with motors on the table.';
        list.appendChild(empty);
    }

    const emptyFilter = document.createElement('p');
    emptyFilter.className = 'opt-hint opt-filter-empty';
    emptyFilter.hidden = true;
    emptyFilter.textContent = 'No components match this filter.';

    body.appendChild(list);
    body.appendChild(emptyFilter);

    const searchInp = searchWrap.querySelector('[data-scan-axis-filter]');
    if (searchInp) {
        searchInp.value = b.axisSearchQuery || '';
        const applyFilter = () => {
            b.axisSearchQuery = searchInp.value;
            const q = searchInp.value.trim().toLowerCase();
            let visible = 0;
            list.querySelectorAll('.opt-comp-accordion').forEach((details) => {
                const compText = details.dataset.searchText || '';
                const compMatch = !q || compText.includes(q);
                let chipVisible = 0;
                details.querySelectorAll('.opt-chip').forEach((chip) => {
                    const chipText = `${chip.textContent} ${chip.title || ''}`.toLowerCase();
                    const show = !q || compMatch || chipText.includes(q);
                    chip.hidden = !show;
                    if (show) chipVisible += 1;
                });
                const showSection = !q || compMatch || chipVisible > 0;
                details.hidden = !showSection;
                if (showSection) visible += 1;
            });
            emptyFilter.hidden = visible > 0 || !entries.length;
        };
        searchInp.addEventListener('input', applyFilter);
        applyFilter();
    }

    const nav = document.createElement('div');
    nav.className = 'scan-nav-row';
    nav.innerHTML = `
        <button type="button" class="btn btn-secondary btn--sidebar-inline" data-scan-back>Back</button>
        <button type="button" class="btn btn-primary btn--sidebar-inline" data-scan-next ${b.axis ? '' : 'disabled'}>Next: Range</button>
    `;
    body.appendChild(nav);
    nav.querySelector('[data-scan-back]')?.addEventListener('click', () => {
        b.stage = 1;
        renderAll();
    });
    nav.querySelector('[data-scan-next]')?.addEventListener('click', () => {
        if (!b.axis) return;
        b.stage = 3;
        renderAll();
    });
}

function renderRangeStage(body) {
    const b = builder();
    const unit = b.axis?.unit || b.unit || '';
    const grid = buildScanGrid(b);
    body.innerHTML = `
        <div style="padding:0 12px 10px;display:flex;flex-direction:column;gap:8px;">
            <label class="scan-field">
                <span>Mode</span>
                <select data-scan-mode>
                    <option value="relative" ${b.mode === 'relative' ? 'selected' : ''}>Relative to current (${unit})</option>
                    <option value="absolute" ${b.mode === 'absolute' ? 'selected' : ''}>Absolute (${unit})</option>
                </select>
            </label>
            <div class="scan-range-row">
                <label class="scan-field"><span>Min</span><input type="number" step="any" data-scan-min value="${b.min}" /></label>
                <label class="scan-field"><span>Max</span><input type="number" step="any" data-scan-max value="${b.max}" /></label>
                <label class="scan-field"><span>Step</span><input type="number" step="any" data-scan-step value="${b.step}" /></label>
            </div>
            <label class="scan-field">
                <span>Or point count (optional, ≥2 overrides step)</span>
                <input type="number" min="0" step="1" data-scan-n value="${b.pointCount || ''}" placeholder="e.g. 11" />
            </label>
            <label class="scan-field">
                <span>Settle after each set (ms)</span>
                <input type="number" min="0" step="50" data-scan-settle value="${b.settleMs}" />
            </label>
            <label class="scan-check">
                <input type="checkbox" data-scan-restore ${b.restoreStart ? 'checked' : ''} />
                Restore start setpoint when finished
            </label>
            <p class="opt-hint">Preview: <strong>${grid.length}</strong> point(s)${unit ? ` · ${unit}` : ''}</p>
            <code class="scan-grid-preview">${grid.slice(0, 24).map((x) => String(x)).join(', ')}${grid.length > 24 ? ' …' : ''}</code>
        </div>
        <div class="scan-nav-row">
            <button type="button" class="btn btn-secondary btn--sidebar-inline" data-scan-back>Back</button>
            <button type="button" class="btn btn-primary btn--sidebar-inline" data-scan-next ${grid.length ? '' : 'disabled'}>Next: Run</button>
        </div>
    `;
    const bindNum = (sel, key) => {
        body.querySelector(sel)?.addEventListener('change', (e) => {
            const v = parseFloat(e.target.value);
            if (Number.isFinite(v)) b[key] = v;
            renderAll();
        });
    };
    body.querySelector('[data-scan-mode]')?.addEventListener('change', (e) => {
        b.mode = e.target.value;
        renderAll();
    });
    bindNum('[data-scan-min]', 'min');
    bindNum('[data-scan-max]', 'max');
    bindNum('[data-scan-step]', 'step');
    body.querySelector('[data-scan-n]')?.addEventListener('change', (e) => {
        const v = parseInt(e.target.value, 10);
        b.pointCount = Number.isFinite(v) && v >= 2 ? v : 0;
        renderAll();
    });
    body.querySelector('[data-scan-settle]')?.addEventListener('change', (e) => {
        const v = parseInt(e.target.value, 10);
        if (Number.isFinite(v) && v >= 0) b.settleMs = v;
    });
    body.querySelector('[data-scan-restore]')?.addEventListener('change', (e) => {
        b.restoreStart = !!e.target.checked;
    });
    body.querySelector('[data-scan-back]')?.addEventListener('click', () => {
        b.stage = 2;
        renderAll();
    });
    body.querySelector('[data-scan-next]')?.addEventListener('click', () => {
        if (!buildScanGrid(b).length) return;
        b.stage = 4;
        renderAll();
    });
}

function renderRunStage(body) {
    const b = builder();
    const summary = formatScanSummary(b);
    const runnable = scanAxisIsMotor(b.axis);
    const sessionHref = b.activeScanId
        ? parameterScanSessionHref({
              scanId: b.activeScanId,
              backendId: getSelectedBackendId(),
          })
        : parameterScanSessionHref({ backendId: getSelectedBackendId() });
    body.innerHTML = `
        <div style="padding:0 12px 10px;">
            <pre class="scan-summary">${summary.map((l) => _escape(l)).join('\n')}</pre>
            ${
                runnable
                    ? ''
                    : `<p class="opt-hint" style="color:#fca5a5;">Selected axis is not a motor — MVP runner cannot actuate it yet.</p>`
            }
            <div id="scan-mode-run-status" class="scan-run-status">${_escape(b.statusText || '')}</div>
            <a class="opt-session-link" href="${_escape(sessionHref)}" target="_blank" rel="noopener" data-scan-session-link="1">
                Open scan session page
                <span class="material-icons-round" style="font-size:14px;">open_in_new</span>
            </a>
            <p class="opt-session-link-hint">Camera frames, CoM plot, and step table update live while the scan runs.</p>
        </div>
        <div class="scan-nav-row">
            <button type="button" class="btn btn-secondary btn--sidebar-inline" data-scan-back ${b.running ? 'disabled' : ''}>Back</button>
            ${
                b.running
                    ? `<button type="button" class="btn btn-secondary btn--sidebar-inline" data-scan-abort>Abort</button>`
                    : `<button type="button" class="btn btn-primary btn--sidebar-inline" data-scan-start ${runnable && b.cameraTagId && b.axis ? '' : 'disabled'}>Start scan</button>`
            }
        </div>
    `;
    body.querySelector('[data-scan-session-link]')?.addEventListener('pointerdown', (ev) => {
        const link = ev.currentTarget;
        const href = link?.getAttribute?.('href');
        if (!href) return;
        ev.preventDefault();
        window.open(href, '_blank', 'noopener');
    });
    body.querySelector('[data-scan-back]')?.addEventListener('click', () => {
        if (b.running) return;
        b.stage = 3;
        renderAll();
    });
    body.querySelector('[data-scan-abort]')?.addEventListener('click', () => {
        b.abortRequested = true;
        setStatus('Abort requested…');
        publishScan({ statusText: 'Abort requested…' });
    });
    body.querySelector('[data-scan-start]')?.addEventListener('click', () => {
        void runParameterScan();
    });
}

function renderResultsStage(body) {
    const b = builder();
    const rows = b.results || [];
    const preset = getScanMeasurePreset(b.measurePresetId);
    const names = preset.feature_names || ['cx', 'cy', 'peak'];
    const stats = trackFeatureStats(b);
    const sessionHref = b.activeScanId
        ? parameterScanSessionHref({
              scanId: b.activeScanId,
              backendId: getSelectedBackendId(),
          })
        : '';
    body.innerHTML = `
        <div style="padding:0 12px 10px;">
            ${
                sessionHref
                    ? `<a class="opt-session-link" href="${_escape(sessionHref)}" target="_blank" rel="noopener">
                        Open scan session page
                        <span class="material-icons-round" style="font-size:14px;">open_in_new</span>
                       </a>`
                    : ''
            }
            <canvas id="scan-mode-chart" width="320" height="160" aria-label="Scan results"></canvas>
            <div class="scan-results-meta">${rows.length ? `${rows.length} steps` : 'No results yet — run a scan.'}</div>
            <div class="scan-results-table-wrap">
                <table class="scan-results-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>value</th>
                            ${names.map((n) => `<th>${_escape(n)}</th>`).join('')}
                            <th>ok</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${
                            rows.length
                                ? rows
                                      .map((r) => {
                                          const feats = Array.isArray(r.features) ? r.features : [];
                                          return `<tr class="${r.ok ? '' : 'is-err'}">
                                            <td>${r.i + 1}</td>
                                            <td>${_fmt(r.value)}</td>
                                            ${names
                                                .map((_, i) => `<td>${_fmt(feats[i])}</td>`)
                                                .join('')}
                                            <td>${r.ok ? '✓' : '✗'}</td>
                                          </tr>`;
                                      })
                                      .join('')
                                : `<tr><td colspan="${3 + names.length}" style="color:#64748b;">—</td></tr>`
                        }
                    </tbody>
                </table>
            </div>
            <div class="scan-track-stats">
                <div class="scan-track-stats__title">Tracked · <code>${_escape(stats.feature)}</code></div>
                ${
                    stats.n
                        ? `<div class="scan-track-stats__row">min ${_fmt(stats.min)} <span class="scan-track-stats__at">@ tunable ${_fmt(stats.atMin)}</span></div>
                           <div class="scan-track-stats__row">max ${_fmt(stats.max)} <span class="scan-track-stats__at">@ tunable ${_fmt(stats.atMax)}</span></div>
                           <div class="scan-track-stats__n">${stats.n} successful step(s)</div>`
                        : `<div class="scan-track-stats__n">No successful ${_escape(stats.feature)} samples yet.</div>`
                }
            </div>
        </div>
        <div class="scan-nav-row">
            <button type="button" class="btn btn-secondary btn--sidebar-inline" data-scan-back>Back to Run</button>
        </div>
    `;
    body.querySelector('[data-scan-back]')?.addEventListener('click', () => {
        b.stage = 4;
        renderAll();
    });
    paintScanChart();
}

function paintScanChart() {
    const canvas = document.getElementById('scan-mode-chart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const rows = (builder().results || []).filter((r) => r.ok);
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#334155';
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
    if (rows.length < 1) {
        ctx.fillStyle = '#64748b';
        ctx.font = '11px sans-serif';
        ctx.fillText('No scan data', 12, 24);
        return;
    }
    const xs = rows.map((r) => Number(r.value));
    const cxs = rows.map((r) => Number(r.features?.[0]));
    const cys = rows.map((r) => Number(r.features?.[1]));
    const xmin = Math.min(...xs);
    const xmax = Math.max(...xs);
    const ys = [...cxs, ...cys].filter((n) => Number.isFinite(n));
    const ymin = ys.length ? Math.min(...ys) : 0;
    const ymax = ys.length ? Math.max(...ys) : 1;
    const pad = 28;
    const xspan = xmax - xmin || 1;
    const yspan = ymax - ymin || 1;
    const toX = (v) => pad + ((v - xmin) / xspan) * (w - pad * 2);
    const toY = (v) => h - pad - ((v - ymin) / yspan) * (h - pad * 2);

    const drawSeries = (vals, color) => {
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        vals.forEach((yv, i) => {
            if (!Number.isFinite(yv)) return;
            const x = toX(xs[i]);
            const y = toY(yv);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        vals.forEach((yv, i) => {
            if (!Number.isFinite(yv)) return;
            ctx.beginPath();
            ctx.arc(toX(xs[i]), toY(yv), 2.5, 0, Math.PI * 2);
            ctx.fill();
        });
    };
    drawSeries(cxs, '#22d3ee');
    drawSeries(cys, '#fbbf24');
    ctx.fillStyle = '#94a3b8';
    ctx.font = '9px sans-serif';
    ctx.fillText('cx', 8, 14);
    ctx.fillStyle = '#22d3ee';
    ctx.fillRect(22, 7, 10, 3);
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('cy', 40, 14);
    ctx.fillStyle = '#fbbf24';
    ctx.fillRect(54, 7, 10, 3);
}

function renderStageBody() {
    const body = document.getElementById('scan-mode-stage-body');
    if (!body) return;
    const b = builder();
    if (b.stage === 1) renderMeasureStage(body);
    else if (b.stage === 2) renderAxisStage(body);
    else if (b.stage === 3) renderRangeStage(body);
    else if (b.stage === 4) renderRunStage(body);
    else renderResultsStage(body);
}

function renderAll() {
    const b = builder();
    const idle = document.getElementById('scan-mode-idle');
    const active = document.getElementById('scan-mode-active');
    const exitBtn = document.getElementById('scan-mode-exit-btn');
    if (idle) idle.hidden = !!b.active;
    if (active) active.hidden = !b.active;
    if (exitBtn) exitBtn.hidden = !b.active;
    document.body.classList.toggle('parameter-scan-mode-active', !!b.active);
    if (b.active) {
        renderStageNav();
        renderStageBody();
    }
    refreshScanVisuals();
}

async function ensureLiveFeedOff(cameraTagId) {
    const entry = store.labState?.components?.[cameraTagId];
    if (!isLiveFeedActive(entry, 'stream')) return;
    setStatus('Ending live feed on camera…');
    const body = await labClient.endLiveFeed(cameraTagId, 'all');
    if (body?.telemetry) {
        applyComponentTelemetryFromServer(cameraTagId, body.telemetry);
    }
}

async function setMotorAbsolute(tagId, motorId, angleDeg) {
    await sendCommand(
        {
            action: 'SET_MOTOR_SETPOINT',
            target_id: tagId,
            parameters: {
                motor_id: String(motorId),
                angle_deg: Number(angleDeg),
            },
        },
        { skipConfirm: true },
    );
}

async function runParameterScan() {
    const b = builder();
    if (b.running) return;
    if (!b.cameraTagId || !b.axis || !scanAxisIsMotor(b.axis)) {
        setStatus('Need a camera and a motor axis to run.');
        return;
    }
    const motorId = motorIdFromAxisPath(b.axis.path);
    if (!motorId) {
        setStatus('Could not parse motor id from axis path.');
        return;
    }

    await fetchLabState().catch(() => {});
    const startVal = readCurrentAxisValue(b.axis);
    b.startValue = startVal;
    let relative = buildScanGrid(b);
    if (!relative.length) {
        setStatus('Empty scan grid.');
        return;
    }
    let absolutes = relative;
    if (b.mode === 'relative') {
        if (!Number.isFinite(startVal)) {
            setStatus('Current motor value unknown — switch to absolute mode or RECORD tunables first.');
            return;
        }
        absolutes = absoluteGridFromRelative(relative, startVal);
    }

    b.activeScanId = newParameterScanId();
    b.lastCameraUrl = null;
    b.lastFrameHw = null;
    b.lastFeatures = null;
    b.running = true;
    b.abortRequested = false;
    b.results = [];
    b.stage = 4;
    setStatus(`Starting scan (${absolutes.length} steps)…`);
    publishScan({
        status: 'running',
        step: 0,
        total: absolutes.length,
        statusText: b.statusText,
    });
    openScanSessionPage(b.activeScanId);
    renderAll();
    document.body.classList.add('parameter-scan-running');

    const preset = getScanMeasurePreset(b.measurePresetId);
    try {
        await ensureLiveFeedOff(b.cameraTagId);

        for (let i = 0; i < absolutes.length; i += 1) {
            if (b.abortRequested) {
                setStatus(`Aborted at step ${i + 1}/${absolutes.length}`);
                publishScan({
                    status: 'aborted',
                    step: i,
                    total: absolutes.length,
                    statusText: b.statusText,
                });
                break;
            }
            const value = absolutes[i];
            setStatus(`Step ${i + 1}/${absolutes.length}: set ${value}…`);
            publishScan({
                status: 'running',
                step: i,
                total: absolutes.length,
                statusText: b.statusText,
            });
            renderAll();

            try {
                await setMotorAbsolute(b.axis.tag_id, motorId, value);
                if (b.settleMs > 0) await sleep(b.settleMs);
                setStatus(`Step ${i + 1}/${absolutes.length}: RECORD + ${preset.kernel_id}…`);
                publishScan({ statusText: b.statusText, step: i, total: absolutes.length });
                const recorded = await labClient.recordMeasurables(b.cameraTagId);
                const epoch =
                    recorded?.epoch_ms ||
                    recorded?.measurables?.camera_image?.provenance?.epoch_ms ||
                    Date.now();
                const camUrl = cameraImageUrl(b.cameraTagId, epoch);
                b.lastCameraUrl = camUrl;
                const frameHw = _shapeHw(recorded?.measurables?.camera_image);
                if (frameHw) b.lastFrameHw = frameHw;

                const probe = await probeKernel(b.cameraTagId, {
                    kernel_id: preset.kernel_id,
                    field: 'camera_image',
                    skipConfirm: true,
                });
                if (!probe.ok) {
                    b.results.push({
                        i,
                        value,
                        features: [],
                        feature_names: preset.feature_names,
                        ok: false,
                        error: probe.error || 'probe failed',
                        cameraUrl: camUrl,
                    });
                    publishScan({
                        status: 'running',
                        step: i + 1,
                        total: absolutes.length,
                        lastCameraUrl: camUrl,
                        lastFrameHw: frameHw || b.lastFrameHw,
                        statusText: `Step ${i + 1} probe failed`,
                    });
                } else {
                    const feats = Array.isArray(probe.result?.features)
                        ? probe.result.features.map(Number)
                        : [];
                    b.lastFeatures = feats;
                    b.results.push({
                        i,
                        value,
                        features: feats,
                        feature_names: probe.result?.feature_names || preset.feature_names,
                        ok: true,
                        cameraUrl: camUrl,
                    });
                    publishScan({
                        status: 'running',
                        step: i + 1,
                        total: absolutes.length,
                        lastCameraUrl: camUrl,
                        lastFrameHw: frameHw || b.lastFrameHw,
                        lastFeatures: feats,
                        statusText: `Step ${i + 1}/${absolutes.length} · cx=${feats[0]?.toFixed?.(1) ?? '—'} cy=${feats[1]?.toFixed?.(1) ?? '—'}`,
                    });
                }
            } catch (err) {
                b.results.push({
                    i,
                    value,
                    features: [],
                    feature_names: preset.feature_names,
                    ok: false,
                    error: String(err?.message || err),
                });
                _log(`Scan step ${i + 1} failed: ${err?.message || err}`, 'error');
                publishScan({
                    status: 'running',
                    step: i + 1,
                    total: absolutes.length,
                    statusText: `Step ${i + 1} failed: ${err?.message || err}`,
                });
            }
            renderAll();
        }

        if (b.restoreStart && Number.isFinite(b.startValue) && !b.abortRequested) {
            setStatus(`Restoring start ${b.startValue}…`);
            publishScan({ statusText: b.statusText });
            try {
                await setMotorAbsolute(b.axis.tag_id, motorId, b.startValue);
            } catch (err) {
                _log(`Scan restore failed: ${err?.message || err}`, 'warn');
            }
        }

        const finalStatus = b.abortRequested ? 'aborted' : 'done';
        setStatus(
            b.abortRequested
                ? `Scan aborted · ${b.results.length} rows`
                : `Scan complete · ${b.results.length} rows`,
        );
        publishScan({
            status: finalStatus,
            step: b.results.length,
            total: absolutes.length,
            statusText: b.statusText,
            lastCameraUrl: b.lastCameraUrl,
        });
        b.stage = 5;
        _log(
            `Parameter scan finished on ${b.axis.label}: ${b.results.length} steps`,
            'info',
        );
    } catch (err) {
        setStatus(`Scan failed: ${err?.message || err}`);
        publishScan({
            status: 'error',
            statusText: b.statusText,
        });
        _log(`Parameter scan failed: ${err?.message || err}`, 'error');
    } finally {
        b.running = false;
        b.abortRequested = false;
        document.body.classList.remove('parameter-scan-running');
        await fetchLabState().catch(() => {});
        renderAll();
    }
}

function enterScanMode() {
    const b = builder();
    b.active = true;
    b.stage = 1;
    b.running = false;
    b.abortRequested = false;
    b.statusText = '';
    syncTrackFeatureForPreset(b);
    renderAll();
    activateWorkspaceTab('parameter-scan');
}

function exitScanMode() {
    const b = builder();
    if (b.running) {
        b.abortRequested = true;
        return;
    }
    store.parameterScanBuilder = createParameterScanBuilder();
    document.body.classList.remove('parameter-scan-mode-active');
    renderAll();
}

function _escape(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _shapeHw(value) {
    if (!value || typeof value !== 'object') return null;
    const shape = value.shape;
    if (Array.isArray(shape) && shape.length >= 2) {
        const h = Number(shape[0]);
        const w = Number(shape[1]);
        if (Number.isFinite(h) && Number.isFinite(w) && h > 0 && w > 0) {
            return /** @type {[number, number]} */ ([h, w]);
        }
    }
    return null;
}

function _fmt(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return '—';
    return Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.001)
        ? x.toExponential(3)
        : x.toFixed(3).replace(/\.?0+$/, '');
}

/**
 * @param {{ log?: Function }} [deps]
 */
export function initParameterScanMode(deps = {}) {
    if (typeof deps.log === 'function') _log = deps.log;
    store.parameterScanBuilder = createParameterScanBuilder();

    document.getElementById('scan-mode-enter-btn')?.addEventListener('click', () => {
        enterScanMode();
    });
    document.getElementById('scan-mode-exit-btn')?.addEventListener('click', () => {
        exitScanMode();
    });
    renderAll();
}

export function refreshParameterScanMode() {
    if (builder().active) renderAll();
}

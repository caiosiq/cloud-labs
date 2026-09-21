/**
 * Dispatch parsed Command Console lines to the same paths as the main UI.
 * Recipe recording: when the Recipe Editor is in Record mode, `sendCommand` → `executeSendCommand`
 * appends steps automatically (same as context panel / canvas). No extra logic needed here.
 * @see coding_on_the_ui.md
 */

import { formatHelp, parseCommandLine } from './command-parse.js';
import { resolveComponentRef } from './command-resolve.js';
import { backendHeaders, withBackendQuery } from './state/backend-selection.js';
import { resolveLaserStitchPose } from './laser-stitch.js';
import { store } from './state/store.js';
import { isHeldTag, isStoredComponent } from './component-model.js';
import { occupiedStorageSlots } from './storage-region.js';
import { formatCompleteLabStateReport } from './lab-state-report.js';

/**
 * @param {string} ref
 * @param {object} deps
 * @param {(text: string, kind?: string) => void} appendLine
 * @returns {string | null}
 */
function resolveTagOrNull(ref, deps, appendLine) {
    const resolved = resolveComponentRef(ref, deps);
    if (!resolved.ok) {
        appendLine(resolved.error, 'error');
        return null;
    }
    if (resolved.matchedBy === 'name') {
        appendLine(`Resolved "${ref}" → ${resolved.tagId}`, 'info');
    }
    return resolved.tagId;
}

function labComponent(tagId) {
    return store.labState?.components?.[tagId] || null;
}

function responseDetail(body, fallback) {
    const detail = body && body.detail !== undefined ? body.detail : null;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
        return detail.message || detail.error || JSON.stringify(detail);
    }
    return fallback;
}

async function fetchSimulationPresets() {
    const response = await fetch(withBackendQuery('/api/runtime-mode/simulation-presets'), {
        headers: backendHeaders(),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(responseDetail(body, `HTTP ${response.status}`));
    }
    const rows = Array.isArray(body.presets) ? body.presets : [];
    store.simulationPresetNames = rows
        .filter((row) => row && row.valid !== false && row.name)
        .map((row) => String(row.name));
    return rows;
}

async function dispatchSimulationPreset(result, deps, appendLine) {
    if (result.type === 'simshow') {
        try {
            const response = await fetch(
                withBackendQuery(
                    `/api/runtime-mode/simulation-presets/${encodeURIComponent(result.selector)}`,
                ),
                { headers: backendHeaders() },
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(responseDetail(body, `HTTP ${response.status}`));
            }
            appendLine(JSON.stringify(body.document, null, 2), 'info');
        } catch (error) {
            appendLine(`Preset read failed: ${error.message || error}`, 'error');
        }
        return;
    }

    if (result.type === 'simwrite') {
        appendLine(`Validating simulation preset "${result.name}"…`, 'info');
        try {
            const response = await fetch(
                withBackendQuery(
                    `/api/runtime-mode/simulation-presets/${encodeURIComponent(result.name)}`,
                ),
                {
                    method: 'PUT',
                    headers: backendHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        document: result.document,
                        overwrite: Boolean(result.overwrite),
                    }),
                },
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(responseDetail(body, `HTTP ${response.status}`));
            }
            if (!store.simulationPresetNames.includes(result.name)) {
                store.simulationPresetNames.push(result.name);
                store.simulationPresetNames.sort();
            }
            appendLine(`Saved authored simulation preset "${result.name}".`, 'info');
        } catch (error) {
            appendLine(`Preset write failed: ${error.message || error}`, 'error');
        }
        return;
    }

    if (result.type === 'simreset' && result.selector.toLowerCase() === 'list') {
        try {
            const rows = await fetchSimulationPresets();
            appendLine('Simulation presets:', 'info');
            appendLine('  current — restart from the current working state', 'info');
            appendLine('  default — restart from lab_view/lab_state.json', 'info');
            if (!rows.length) {
                appendLine('  (no named presets saved)', 'info');
            }
            rows.forEach((row) => {
                const count = Number(row.component_count || 0);
                const suffix = row.valid === false ? ` [invalid: ${row.error || 'bad state'}]` : '';
                appendLine(`  ${row.name} — ${count} component${count === 1 ? '' : 's'}${suffix}`, row.valid === false ? 'warn' : 'info');
            });
        } catch (error) {
            appendLine(error.message || String(error), 'error');
        }
        return;
    }

    if (result.type === 'simsave') {
        appendLine(`Saving simulation preset "${result.name}"…`, 'info');
        try {
            const response = await fetch(
                withBackendQuery(
                    `/api/runtime-mode/simulation-presets/${encodeURIComponent(result.name)}`,
                ),
                {
                    method: 'POST',
                    headers: backendHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ overwrite: Boolean(result.overwrite) }),
                },
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(responseDetail(body, `HTTP ${response.status}`));
            }
            if (!store.simulationPresetNames.includes(result.name)) {
                store.simulationPresetNames.push(result.name);
                store.simulationPresetNames.sort();
            }
            appendLine(`Saved simulation preset "${result.name}".`, 'info');
        } catch (error) {
            appendLine(error.message || String(error), 'error');
        }
        return;
    }

    const selector = result.selector;
    appendLine(`Resetting MuJoCo from "${selector}"…`, 'info');
    try {
        const path = selector.toLowerCase() === 'current'
            ? '/api/runtime-mode/refresh-mujoco'
            : `/api/runtime-mode/simulation-presets/${encodeURIComponent(selector)}/load`;
        const response = await fetch(withBackendQuery(path), {
            method: 'POST',
            headers: backendHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({}),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(responseDetail(body, `HTTP ${response.status}`));
        }
        store.forceGhostSync = true;
        if (typeof deps.fetchLabState === 'function') {
            await deps.fetchLabState();
        }
        const pid = body?.simulator?.pid;
        appendLine(
            `MuJoCo reset from "${selector}"${pid ? ` (PID ${pid})` : ''}.`,
            'info',
        );
    } catch (error) {
        appendLine(error.message || String(error), 'error');
    }
}

async function dispatchSimulationComponent(result, deps, appendLine) {
    const base = '/api/runtime-mode/simulation-components';
    let method = 'GET';
    let path = base;
    let payload = null;
    let mutatesRuntime = false;

    if (result.type === 'simcomponent-show') {
        path = `${base}/${encodeURIComponent(result.tagId)}`;
    } else if (result.type === 'simcomponent-nexttag') {
        path = `${base}/next-tag`;
    } else if (result.type === 'simcomponent-define') {
        method = 'PUT';
        path = `${base}/${encodeURIComponent(result.tagId)}`;
        payload = result.definition;
    } else if (result.type === 'simcomponent-configure') {
        method = 'PATCH';
        path = `${base}/${encodeURIComponent(result.tagId)}`;
        payload = result.definition;
        mutatesRuntime = true;
    } else if (result.type === 'simcomponent-reset') {
        method = 'POST';
        path = `${base}/${encodeURIComponent(result.tagId)}/reset`;
        payload = {};
        mutatesRuntime = true;
    } else if (result.type === 'simcomponent-insert') {
        method = 'POST';
        path = `${base}/${encodeURIComponent(result.tagId)}/insert`;
        payload = result.payload;
        mutatesRuntime = true;
    } else if (result.type === 'simcomponent-remove') {
        method = 'POST';
        path = `${base}/${encodeURIComponent(result.tagId)}/remove`;
        payload = {};
        mutatesRuntime = true;
    } else if (result.type === 'simcomponent-delete') {
        method = 'DELETE';
        path = `${base}/${encodeURIComponent(result.tagId)}`;
    } else if (result.type === 'simclear') {
        method = 'POST';
        path = '/api/runtime-mode/simulation-table/clear';
        payload = { scope: result.scope };
        mutatesRuntime = true;
    }

    try {
        const options = { method, headers: backendHeaders() };
        if (payload !== null) {
            options.headers = backendHeaders({ 'Content-Type': 'application/json' });
            options.body = JSON.stringify(payload);
        }
        const response = await fetch(withBackendQuery(path), options);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(responseDetail(body, `HTTP ${response.status}`));
        }
        if (result.type === 'simcomponent-list') {
            const rows = Array.isArray(body.components) ? body.components : [];
            store.simulationComponentTags = rows
                .map((row) => row && row.tag_id)
                .filter(Boolean)
                .map(String);
            appendLine(`Simulation library (${rows.length} definitions):`, 'info');
            appendLine(
                `  spacing rule: radius₁ + radius₂ + ${body.pair_clearance_margin_mm ?? 5} mm`,
                'info',
            );
            rows.forEach((row) => {
                const active = row.active ? `active:${row.placement || 'yes'}` : 'inactive';
                const parameters = row.parameters && Object.keys(row.parameters).length
                    ? ` parameters=${JSON.stringify(row.parameters)}`
                    : '';
                const footprint = row.housing?.footprint_mm || {};
                const housing = row.housing
                    ? ` housing=${footprint.width}x${footprint.depth}x${row.housing.height_mm}mm` +
                      ` radius=${row.housing.clearance_radius_mm}mm`
                    : '';
                appendLine(
                    `  ${row.tag_id} — ${row.name || 'Unnamed'} (${row.type || 'UNKNOWN'}) ` +
                    `[${active}]${housing}${parameters}`,
                    'info',
                );
            });
        } else if (result.type === 'simcomponent-nexttag') {
            appendLine(`Next available tag: ${body.tag_id}`, 'info');
        } else {
            appendLine(JSON.stringify(body, null, 2), 'info');
        }
        if (mutatesRuntime || result.type === 'simcomponent-define' || result.type === 'simcomponent-delete') {
            if (mutatesRuntime) store.forceGhostSync = true;
            if (typeof deps.fetchCatalogMap === 'function') await deps.fetchCatalogMap();
            if (typeof deps.fetchLabState === 'function') await deps.fetchLabState();
        }
    } catch (error) {
        appendLine(`Simulation component command failed: ${error.message || error}`, 'error');
    }
}

/**
 * Preview breadboard pose on the ghost (works for stored → place as well).
 * @param {object} deps
 * @param {string} tagId
 * @param {number} x
 * @param {number} y
 * @param {number} rot
 */
function previewGhostPose(deps, tagId, x, y, rot) {
    if (!deps.ghostState) return;
    if (!deps.ghostState[tagId]) {
        deps.ghostState[tagId] = { x, y, rotation: rot };
    } else {
        deps.ghostState[tagId].x = x;
        deps.ghostState[tagId].y = y;
        deps.ghostState[tagId].rotation = rot;
    }
    if (typeof deps.render === 'function') deps.render();
}

/**
 * @param {string} tagId
 * @param {{ slot_i?: number, slot_j?: number }} params
 * @param {(text: string, kind?: string) => void} appendLine
 * @returns {boolean}
 */
function validateStoreCommand(tagId, params, appendLine) {
    const comp = labComponent(tagId);
    if (!comp) {
        appendLine(`Component not in lab: ${tagId}`, 'error');
        return false;
    }
    if (isHeldTag(tagId, store.labState || {})) {
        appendLine(`Cannot store ${tagId} while held — place it first.`, 'error');
        return false;
    }
    if (isStoredComponent(comp)) {
        appendLine(`${tagId} is already in storage.`, 'error');
        return false;
    }

    const hasI = params.slot_i !== undefined && params.slot_i !== null;
    const hasJ = params.slot_j !== undefined && params.slot_j !== null;
    if (!hasI && !hasJ) return true;
    if (!hasI || !hasJ) {
        appendLine('store: provide both slot_i and slot_j, or neither for autopack.', 'error');
        return false;
    }

    const i = Number(params.slot_i);
    const j = Number(params.slot_j);
    if (!Number.isInteger(i) || !Number.isInteger(j)) {
        appendLine('store: slot indices must be integers.', 'error');
        return false;
    }

    const spec = store.storageGridSpec;
    if (!spec || !Number.isFinite(spec.nx) || !Number.isFinite(spec.ny)) {
        appendLine('Storage grid not loaded yet — wait for lab layout, then retry.', 'error');
        return false;
    }
    if (i < 0 || j < 0 || i >= spec.nx || j >= spec.ny) {
        appendLine(
            `Cell (${i}, ${j}) is outside the grid (0..${spec.nx - 1}, 0..${spec.ny - 1}).`,
            'error',
        );
        return false;
    }
    const occ = occupiedStorageSlots(tagId);
    if (occ.has(`${i},${j}`)) {
        appendLine(`Cell (${i}, ${j}) is occupied — pick a free square.`, 'error');
        return false;
    }
    return true;
}

/**
 * @param {string} tagId
 * @param {(text: string, kind?: string) => void} appendLine
 * @returns {boolean}
 */
function validatePlaceFromStorage(tagId, appendLine) {
    const comp = labComponent(tagId);
    if (!comp) {
        appendLine(`Component not in lab: ${tagId}`, 'error');
        return false;
    }
    if (!isStoredComponent(comp)) {
        appendLine(
            `${tagId} is not in storage — use move / movelaser for table parts.`,
            'error',
        );
        return false;
    }
    if (isHeldTag(tagId, store.labState || {})) {
        appendLine(`Cannot place ${tagId} while held.`, 'error');
        return false;
    }
    return true;
}

/**
 * @param {string} line
 * @param {object} deps
 * @param {(text: string, kind?: string) => void} appendLine
 */
export async function dispatchConsoleLine(line, deps, appendLine) {
    const parsed = parseCommandLine(line);
    if (!parsed.ok) {
        appendLine(parsed.error, 'error');
        return;
    }

    const { result } = parsed;

    if (result.type === 'help') {
        appendLine(formatHelp(), 'info');
        return;
    }

    if (result.type === 'lasers') {
        const doc = store.laserLinesDoc;
        if (!doc || !Array.isArray(doc.lines) || doc.lines.length === 0) {
            appendLine('No laser lines loaded.', 'warn');
            return;
        }
        const snap = doc.snap_line_id || '(none)';
        appendLine(`Laser lines (snap default: ${snap}):`, 'info');
        doc.lines.forEach((ln) => {
            if (!ln || !ln.id) return;
            const en = ln.enabled !== false ? 'on' : 'off';
            const mark = ln.id === doc.snap_line_id ? ' [snap]' : '';
            const name = ln.name ? ` — ${ln.name}` : '';
            appendLine(`  ${ln.id}${name} (${en})${mark}`, 'info');
        });
        appendLine('Example: movelaser tag_22 200 45 diode', 'info');
        return;
    }

    if (result.type === 'refresh') {
        appendLine('Refreshing poses from camera…', 'info');
        try {
            await deps.runLabPoseRefresh();
            appendLine('Pose refresh finished.', 'info');
        } catch (e) {
            const msg = e && e.message ? e.message : String(e);
            appendLine(`Refresh failed: ${msg}`, 'error');
        }
        return;
    }

    if (result.type === 'labstate') {
        try {
            const response = await fetch(withBackendQuery('/api/lab-state'), {
                headers: backendHeaders(),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(responseDetail(body, `HTTP ${response.status}`));
            }
            if (result.rawJson) {
                appendLine(JSON.stringify(body, null, 2), 'info');
            } else {
                formatCompleteLabStateReport(body, store.catalogMap).forEach((line) => {
                    appendLine(line, 'info');
                });
            }
        } catch (error) {
            appendLine(`Lab-state read failed: ${error.message || error}`, 'error');
        }
        return;
    }

    if (
        result.type === 'simreset' ||
        result.type === 'simsave' ||
        result.type === 'simshow' ||
        result.type === 'simwrite'
    ) {
        await dispatchSimulationPreset(result, deps, appendLine);
        return;
    }

    if (result.type.startsWith('simcomponent-') || result.type === 'simclear') {
        await dispatchSimulationComponent(result, deps, appendLine);
        return;
    }

    if (result.type === 'tunables' || result.type === 'measurables') {
        const tagId = resolveTagOrNull(result.tagRef, deps, appendLine);
        if (!tagId) return;
        const path =
            result.type === 'tunables'
                ? withBackendQuery(`/api/components/${encodeURIComponent(tagId)}/tunables`)
                : withBackendQuery(`/api/components/${encodeURIComponent(tagId)}/measurables`);
        try {
            const r = await fetch(path, { headers: backendHeaders() });
            const text = await r.text();
            let body;
            try {
                body = JSON.parse(text);
            } catch {
                appendLine(text || `HTTP ${r.status}`, r.ok ? 'info' : 'error');
                return;
            }
            if (!r.ok) {
                const detail = body && body.detail !== undefined ? body.detail : text;
                appendLine(typeof detail === 'string' ? detail : JSON.stringify(detail), 'error');
                return;
            }
            appendLine(JSON.stringify(body, null, 2), 'info');
        } catch (e) {
            const msg = e && e.message ? e.message : String(e);
            appendLine(`Request failed: ${msg}`, 'error');
        }
        return;
    }

    if (result.type === 'record') {
        const tagId = resolveTagOrNull(result.tagRef, deps, appendLine);
        if (!tagId) return;
        appendLine(`Record measurables (${tagId})…`, 'info');
        try {
            const r = await fetch(
                withBackendQuery(`/api/components/${encodeURIComponent(tagId)}/measurables/record`),
                {
                    method: 'POST',
                    headers: backendHeaders(),
                },
            );
            const text = await r.text();
            let body;
            try {
                body = JSON.parse(text);
            } catch {
                appendLine(text || `HTTP ${r.status}`, r.ok ? 'info' : 'error');
                return;
            }
            if (!r.ok) {
                const detail = body && body.detail !== undefined ? body.detail : text;
                appendLine(typeof detail === 'string' ? detail : JSON.stringify(detail), 'error');
                return;
            }
            appendLine(JSON.stringify(body.measurables ?? body, null, 2), 'info');
            if (typeof deps.fetchLabState === 'function') {
                await deps.fetchLabState();
            }
        } catch (e) {
            const msg = e && e.message ? e.message : String(e);
            appendLine(`Record failed: ${msg}`, 'error');
        }
        return;
    }

    let command = null;

    if (result.type === 'movelaser') {
        const tagId = resolveTagOrNull(result.tagRef, deps, appendLine);
        if (!tagId) return;
        if (isStoredComponent(labComponent(tagId))) {
            appendLine(
                `${tagId} is in storage — use placelaser (or place) instead of movelaser.`,
                'error',
            );
            return;
        }
        const stitch = resolveLaserStitchPose(result.y, result.rotation, result.lineId);
        if (!stitch.ok) {
            appendLine(stitch.error, 'error');
            return;
        }
        appendLine(
            `Stitch ${tagId} → laser "${stitch.lineName}" (${stitch.lineId}): ` +
                `X=${stitch.x.toFixed(2)} mm (from Y), Y=${stitch.y.toFixed(2)} mm, θ=${stitch.rotation.toFixed(2)}°`,
            'info',
        );
        command = {
            action: 'MOVE_COMPONENT',
            target_id: tagId,
            parameters: {
                target_x: stitch.x,
                target_y: stitch.y,
                rotation: stitch.rotation,
                laser_line_id: stitch.lineId,
                laser_line_name: stitch.lineName,
            },
        };
    } else if (result.type === 'placelaser') {
        const tagId = resolveTagOrNull(result.tagRef, deps, appendLine);
        if (!tagId) return;
        if (!validatePlaceFromStorage(tagId, appendLine)) return;
        const stitch = resolveLaserStitchPose(result.y, result.rotation, result.lineId);
        if (!stitch.ok) {
            appendLine(stitch.error, 'error');
            return;
        }
        appendLine(
            `Place ${tagId} from storage → laser "${stitch.lineName}" (${stitch.lineId}): ` +
                `X=${stitch.x.toFixed(2)} mm (from Y), Y=${stitch.y.toFixed(2)} mm, θ=${stitch.rotation.toFixed(2)}°`,
            'info',
        );
        command = {
            action: 'PLACE_FROM_STORAGE',
            target_id: tagId,
            parameters: {
                target_x: stitch.x,
                target_y: stitch.y,
                rotation: stitch.rotation,
                laser_line_id: stitch.lineId,
                laser_line_name: stitch.lineName,
            },
        };
    } else if (result.type === 'command') {
        command = { ...result.command };
        const ref = command.target_ref || command.target_id;
        if (ref) {
            const tagId = resolveTagOrNull(ref, deps, appendLine);
            if (!tagId) return;
            command.target_id = tagId;
        }
        delete command.target_ref;
    } else {
        appendLine('Internal parse error.', 'error');
        return;
    }

    if (command.action === 'STORE_COMPONENT') {
        if (!validateStoreCommand(command.target_id, command.parameters || {}, appendLine)) {
            return;
        }
        const p = command.parameters || {};
        if (Number.isInteger(p.slot_i) && Number.isInteger(p.slot_j)) {
            appendLine(
                `Store ${command.target_id} → cell (${p.slot_i}, ${p.slot_j})`,
                'info',
            );
        } else {
            appendLine(`Store ${command.target_id} → autopack (edge picks a free cell)`, 'info');
        }
    }

    if (command.action === 'PLACE_FROM_STORAGE') {
        if (!validatePlaceFromStorage(command.target_id, appendLine)) return;
        const { target_x: tx, target_y: ty, rotation: trot } = command.parameters || {};
        if (![tx, ty, trot].every(Number.isFinite)) {
            appendLine('place: invalid target pose.', 'error');
            return;
        }
        const collision = deps.checkCollision(command.target_id, tx, ty, {
            rotation: trot,
            forPlaceFromStorageDrag: true,
        });
        if (collision.detected) {
            appendLine(`Place blocked: collision with ${collision.other}.`, 'error');
            deps.log?.(
                `[Command Console] place cancelled (collision with ${collision.other})`,
                'error',
            );
            return;
        }
        previewGhostPose(deps, command.target_id, tx, ty, trot);
    }

    if (command.action === 'MOVE_COMPONENT') {
        const tag = command.target_id;
        if (!deps.ensureGhostForConsole(tag)) {
            appendLine(`Component not placed or unknown: ${tag}`, 'error');
            return;
        }
        const { target_x: tx, target_y: ty, rotation: trot } = command.parameters;
        const collision = deps.checkCollision(tag, tx, ty, { rotation: trot });
        if (collision.detected) {
            appendLine(`Move blocked: collision with ${collision.other}.`, 'error');
            deps.log(`[Command Console] move cancelled (collision with ${collision.other})`, 'error');
            return;
        }
        previewGhostPose(deps, tag, tx, ty, trot);
    }

    const res = await deps.sendCommand(command);
    if (res && res.ok === false) {
        const err = res.error || 'Command rejected';
        if (err !== 'cancelled') {
            appendLine(err, 'error');
        } else {
            appendLine('Cancelled.', 'info');
        }
        return;
    }
    if (res && res.ok === true) {
        appendLine(res.message ? String(res.message) : 'Accepted.', 'info');
    }
}

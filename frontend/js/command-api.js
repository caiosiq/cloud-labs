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

/**
 * Dispatch parsed Command Console lines to the same paths as the main UI.
 * Recipe recording: when the Recipe Editor is in Record mode, `sendCommand` → `executeSendCommand`
 * appends steps automatically (same as context panel / canvas). No extra logic needed here.
 * @see coding_on_the_ui.md
 */

import { LOSS_METRICS_BY_STRATEGY } from './command-grammar.js';
import { formatHelp, parseCommandLine } from './command-parse.js';
import {
    cobylaReferenceReady,
    defaultOptimizeSensor,
    getOptimizeFormPrefs,
} from './optimize-session.js';

/**
 * @param {string} line
 * @param {object} deps
 * @param {function} deps.sendCommand
 * @param {function} deps.checkCollision
 * @param {function} deps.log
 * @param {function} deps.runLabPoseRefresh
 * @param {function} deps.fetchLabState
 * @param {function} deps.ensureGhostForConsole
 * @param {object} deps.ghostState
 * @param {function} deps.render
 * @param {function} deps.getCatalogEntry
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
        const tagId = result.tagId;
        const path =
            result.type === 'tunables'
                ? `/api/components/${encodeURIComponent(tagId)}/tunables`
                : `/api/components/${encodeURIComponent(tagId)}/measurables`;
        try {
            const r = await fetch(path);
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

    if (result.type === 'storage') {
        try {
            const r = await fetch('/api/lab/storage');
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
        const tagId = result.tagId;
        appendLine(`Record measurables (${tagId})…`, 'info');
        try {
            const r = await fetch(`/api/components/${encodeURIComponent(tagId)}/measurables/record`, {
                method: 'POST',
            });
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

    if (result.type !== 'command') {
        appendLine('Internal parse error.', 'error');
        return;
    }

    let command = result.command;

    if (command.action === 'OPTIMIZE' && command.parameters) {
        const tagId = command.target_id;
        const strat = command.parameters.strategy;
        const p = { ...command.parameters };
        const saved = getOptimizeFormPrefs(tagId) || {};
        const metrics = LOSS_METRICS_BY_STRATEGY[strat] || ['centroid_match'];

        if (!p.sensor_component) {
            p.sensor_component = saved.sensor || defaultOptimizeSensor(tagId);
        }
        if (!p.sensor_component) {
            appendLine(
                'OPTIMIZE needs a sensor tag (e.g. optimize tag_1 COBYLA tag_22).',
                'error'
            );
            return;
        }

        if (!p.loss_metric) {
            p.loss_metric =
                saved.lossMetric && metrics.includes(saved.lossMetric)
                    ? saved.lossMetric
                    : metrics[0];
        } else if (!metrics.includes(p.loss_metric)) {
            appendLine(
                `Loss metric "${p.loss_metric}" is not allowed for ${strat} `
                + `(allowed: ${metrics.join(', ') || 'none'}).`,
                'error',
            );
            return;
        }

        if (typeof saved.videoExposure === 'number' && Number.isFinite(saved.videoExposure)) {
            p.video_exposure = saved.videoExposure;
        }
        if (strat === 'NEWTON') {
            if (saved.axis === 'x' || saved.axis === 'y') {
                p.axis = saved.axis;
            }
            if (typeof saved.toleranceRatio === 'number' && Number.isFinite(saved.toleranceRatio)) {
                p.tolerance_ratio = saved.toleranceRatio;
            }
        }

        if (strat === 'COBYLA' && !cobylaReferenceReady()) {
            appendLine(
                'COBYLA blocked: pin a lab optimization reference first (record camera capture).',
                'error'
            );
            return;
        }

        command = { ...command, parameters: p };
    }

    if (command.action === 'MOVE_COMPONENT') {
        const tag = command.target_id;
        if (!deps.ensureGhostForConsole(tag)) {
            appendLine(`Component not placed or unknown: ${tag}`, 'error');
            return;
        }
        const { target_x: tx, target_y: ty, rotation: trot } = command.parameters;
        const collision = deps.checkCollision(tag, tx, ty);
        if (collision.detected) {
            appendLine(`Move blocked: collision with ${collision.other}.`, 'error');
            deps.log(`[Command Console] move cancelled (collision with ${collision.other})`, 'error');
            return;
        }
        deps.ghostState[tag].x = tx;
        deps.ghostState[tag].y = ty;
        deps.ghostState[tag].rotation = trot;
        deps.render();
    }

    const res = await deps.sendCommand(command);
    if (res && res.ok === false) {
        const err = res.error || 'Command rejected';
        if (err !== 'cancelled') {
            appendLine(err, 'error');
        } else {
            appendLine('Move cancelled.', 'info');
        }
        return;
    }
    if (res && res.ok === true) {
        appendLine(res.message ? String(res.message) : 'Accepted.', 'info');
    }
}

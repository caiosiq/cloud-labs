/**
 * Full-detail OPTIMIZE session rendering (shared by /optimize-session page).
 * Self-contained — does not depend on the staged Optimization mode builder.
 */
import { withBackendQuery } from '../state/backend-selection.js';
import {
    extractKernelOverlays,
    overlayLegendHtml,
} from './kernel-overlay.js';

export function escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

export function formatTunableNumber(val, digits = 3) {
    const n = Number(val);
    if (!Number.isFinite(n)) return String(val ?? '—');
    const abs = Math.abs(n);
    if (abs !== 0 && (abs < 0.001 || abs >= 1000)) return n.toExponential(2);
    return n.toFixed(digits);
}

export function collectTrace(labState) {
    const sess = labState?.optimization_session;
    if (labState?.system_status === 'OPTIMIZING' && sess?.mode === 'ensemble' && Array.isArray(sess.trace)) {
        return sess.trace;
    }
    const last = labState?.last_ensemble_optimization;
    if (last && Array.isArray(last.trace)) return last.trace;
    return [];
}

export function sessionContext(labState) {
    const sess = labState?.optimization_session;
    if (labState?.system_status === 'OPTIMIZING' && sess?.mode === 'ensemble') {
        return {
            running: true,
            queued: false,
            eval: sess.eval ?? 0,
            bestLoss: sess.best_loss,
            lastEval: sess.last_eval,
            sessionId: sess.session_id || sess.session_label || '',
            maxEvals: null,
            result: null,
        };
    }
    // Job accepted / arming before first OPTIMIZING progress frame lands.
    if (
        labState?.system_status === 'OPTIMIZING' ||
        labState?.system_status === 'BUSY' ||
        labState?.active_job_id
    ) {
        const last = labState?.last_ensemble_optimization;
    // Prefer live empty session over stale completed last-run while a job is active.
        if (
            labState?.system_status === 'OPTIMIZING' ||
            labState?.system_status === 'BUSY' ||
            !last
        ) {
            return {
                running: true,
                queued: true,
                eval: sess?.eval ?? 0,
                bestLoss: sess?.best_loss ?? null,
                lastEval: sess?.last_eval ?? null,
                sessionId: sess?.session_id || sess?.session_label || '',
                maxEvals: null,
                result: null,
            };
        }
    }
    const last = labState?.last_ensemble_optimization;
    if (last) {
        const rows = last.trace || [];
        return {
            running: false,
            queued: false,
            eval: last.evals ?? rows.length,
            bestLoss: last.best_loss,
            lastEval: rows.length ? rows[rows.length - 1] : null,
            sessionId: last.session_id || last.session_label || '',
            maxEvals: last.evals ?? rows.length,
            result: last,
        };
    }
    return null;
}

/**
 * Locate the sparse camera preview row (may lag behind latest eval).
 * @returns {{ row: object|null, evalN: number|null, reason: string }}
 */
export function resolveCameraPreviewSource(labState, lastEval) {
    const rows = collectTrace(labState);
    let row = null;
    for (let i = rows.length - 1; i >= 0; i -= 1) {
        const r = rows[i];
        if (r?.camera_image && typeof r.camera_image === 'object') {
            row = r;
            break;
        }
    }
    if (!row && lastEval?.camera_image && typeof lastEval.camera_image === 'object') {
        row = lastEval;
    }
    const cam = row?.camera_image;
    const tel = row?.stages?.telemetry;
    let reason = '';
    if (tel && typeof tel === 'object') {
        reason = String(tel.reason || '').trim();
    }
    if (!reason && cam && typeof cam === 'object') {
        const prov = cam.provenance && typeof cam.provenance === 'object' ? cam.provenance : {};
        reason = String(prov.reason || cam.reason || '').trim();
    }
    const evalN =
        row?.eval != null && Number.isFinite(Number(row.eval))
            ? Number(row.eval)
            : cam?.eval != null && Number.isFinite(Number(cam.eval))
              ? Number(cam.eval)
              : null;
    return { row, evalN, reason, cam };
}

function orderedVarIds(valuesList) {
    const seen = new Set();
    const ids = [];
    (valuesList || []).forEach((values) => {
        if (!values || typeof values !== 'object') return;
        Object.keys(values).forEach((id) => {
            if (!seen.has(id)) {
                seen.add(id);
                ids.push(id);
            }
        });
    });
    return ids;
}

function stageMarks(stages) {
    if (!stages || typeof stages !== 'object') return '—';
    const core = ['capture', 'kernel', 'actuate']
        .map((name) => {
            const part = stages[name];
            if (!part || typeof part !== 'object') return `${name[0]}?`;
            if (part.ok === true) return `${name[0]}✓`;
            if (part.ok === false) return `${name[0]}✗`;
            return `${name[0]}·`;
        })
        .join(' ');
    return core;
}

export function formatTunablesHtml(values, prevValues) {
    if (!values || typeof values !== 'object') {
        return `<div class="osd-idle">Tunables appear after the first eval</div>`;
    }
    const ids = orderedVarIds([values, prevValues]);
    if (!ids.length) return `<div class="osd-idle">No variable values on this eval</div>`;
    const chips = ids
        .map((id) => {
            const n = Number(values[id]);
            const shown = formatTunableNumber(values[id], 3);
            let deltaHtml = '';
            if (prevValues && id in prevValues) {
                const prev = Number(prevValues[id]);
                if (Number.isFinite(n) && Number.isFinite(prev)) {
                    const d = n - prev;
                    if (Math.abs(d) >= 1e-9) {
                        const sign = d > 0 ? '+' : '';
                        deltaHtml = `<span class="osd-delta">Δ ${sign}${formatTunableNumber(d, 3)}</span>`;
                    }
                }
            }
            return `<div class="osd-chip"><strong>${escapeHtml(id)}</strong><div class="osd-chip__val">${shown}</div>${deltaHtml}</div>`;
        })
        .join('');
    return `<div class="osd-tunables"><div class="osd-section-title">This eval — tunables</div><div class="osd-chip-grid">${chips}</div></div>`;
}

export function formatTraceTableHtml(rowsChronological) {
    const display = [...(rowsChronological || [])].reverse();
    if (!display.length) {
        return `<div class="osd-table-wrap"><p class="osd-idle">Waiting for iterations…</p></div>`;
    }
    const varIds = orderedVarIds(display.map((r) => r.values));
    const headVars = varIds.map((id) => `<th>${escapeHtml(id)}</th>`).join('');
    const body = display
        .map((row, idx) => {
            const older = display[idx + 1];
            const prevVals = older?.values;
            const newBest =
                row.best_loss != null &&
                row.loss != null &&
                Math.abs(Number(row.loss) - Number(row.best_loss)) < 1e-9;
            const varCells = varIds
                .map((id) => {
                    const values = row.values || {};
                    if (!(id in values)) return '<td>—</td>';
                    const n = Number(values[id]);
                    let delta = '';
                    if (prevVals && id in prevVals && Number.isFinite(n)) {
                        const d = n - Number(prevVals[id]);
                        if (Number.isFinite(d) && Math.abs(d) >= 1e-9) {
                            const sign = d > 0 ? '+' : '';
                            delta = `<small class="osd-var-delta">${sign}${formatTunableNumber(d, 2)}</small>`;
                        }
                    }
                    return `<td><span>${formatTunableNumber(values[id], 3)}</span>${delta}</td>`;
                })
                .join('');
            const lossShown =
                row.loss != null && Number.isFinite(Number(row.loss))
                    ? Number(row.loss).toFixed(4)
                    : '—';
            const bestShown =
                row.best_loss != null && Number.isFinite(Number(row.best_loss))
                    ? Number(row.best_loss).toFixed(4)
                    : '—';
            return `<tr class="${newBest ? 'osd-row--best' : ''}">
                <td>${row.eval ?? '—'}</td>
                <td>${lossShown}${newBest ? ' <span class="osd-pill">best</span>' : ''}</td>
                <td>${bestShown}</td>
                ${varCells}
                <td class="osd-mono">${stageMarks(row.stages)}</td>
            </tr>`;
        })
        .join('');
    return `<div class="osd-table-wrap">
        <table class="osd-table">
            <thead><tr><th>Eval</th><th>Loss</th><th>Best</th>${headVars}<th>Stages</th></tr></thead>
            <tbody>${body}</tbody>
        </table>
    </div>`;
}

/** Plain-text (TSV) export of iteration history — newest first, matches the table. */
export function formatTraceTableText(rowsChronological) {
    const display = [...(rowsChronological || [])].reverse();
    if (!display.length) return '';
    const varIds = orderedVarIds(display.map((r) => r.values));
    const header = ['Eval', 'Loss', 'Best', ...varIds, 'Stages'];
    const lines = [header.join('\t')];
    for (const row of display) {
        const lossShown =
            row.loss != null && Number.isFinite(Number(row.loss))
                ? Number(row.loss).toFixed(4)
                : '';
        const bestShown =
            row.best_loss != null && Number.isFinite(Number(row.best_loss))
                ? Number(row.best_loss).toFixed(4)
                : '';
        const vals = varIds.map((id) => {
            const values = row.values || {};
            if (!(id in values)) return '';
            return formatTunableNumber(values[id], 6);
        });
        lines.push(
            [row.eval ?? '', lossShown, bestShown, ...vals, stageMarks(row.stages)].join('\t'),
        );
    }
    return `${lines.join('\n')}\n`;
}

export function formatStagesDetailHtml(stages) {
    if (!stages || typeof stages !== 'object') {
        return `<div class="osd-idle">Stage debug waiting for first eval</div>`;
    }
    const cards = [];
    const push = (name, ok, detail) => {
        const cls = ok === true ? 'ok' : ok === false ? 'fail' : 'unknown';
        cards.push(`<div class="osd-stage osd-stage--${cls}"><strong>${escapeHtml(name)}</strong>
            <span>${ok === true ? 'ok' : ok === false ? 'FAIL' : '—'}</span>
            ${detail ? `<pre>${escapeHtml(detail)}</pre>` : ''}
        </div>`);
    };

    ['capture', 'kernel', 'actuate'].forEach((name) => {
        const part = stages[name] || {};
        let detail = '';
        if (name === 'capture' && Array.isArray(part.frames)) {
            detail = part.frames
                .map((f) => `${f.capture_id || '?'} ${JSON.stringify(f.shape || [])}`)
                .join('\n');
        } else if (name === 'kernel' && part.terms) {
            detail = Object.entries(part.terms)
                .map(([id, t]) => {
                    if (t?.error) return `${id}: ERR ${t.error}`;
                    if (t?.kind === 'features') {
                        return `${id}: ${t.kernel_id || ''} n=${t.n} [${(t.preview || []).join(', ')}]`;
                    }
                    if (t?.scalar != null) return `${id}: ${Number(t.scalar).toFixed(4)}`;
                    return `${id}: ${t?.kind || '—'}`;
                })
                .join('\n');
        } else if (name === 'actuate') {
            if (part.refused) detail = `refused: ${part.refused}`;
            else if (part.delta_from_x0) {
                detail = Object.entries(part.delta_from_x0)
                    .map(([id, d]) => `${id} Δ=${Number(d).toFixed(3)}`)
                    .join('\n');
            }
        }
        if (part.error) detail = `${detail ? `${detail}\n` : ''}${part.error}`;
        push(name, part.ok, detail);
    });

    if (stages.presence) {
        const rows = Object.entries(stages.presence).map(([id, p]) => {
            if (!p || typeof p !== 'object') return `${id}: —`;
            const ok = p.presence_ok !== false;
            return `${id}: peak=${Number(p.peak).toFixed(3)} ref=${Number(p.peak_ref).toFixed(3)} ${ok ? 'PRESENT' : 'ABSENT'}`;
        });
        const absent = Object.values(stages.presence).some(
            (v) => v && typeof v === 'object' && v.presence_ok === false,
        );
        push('presence', !absent, rows.join('\n'));
    }
    if (stages.policy) {
        const p = stages.policy;
        const bits = [];
        if (p.loss != null) bits.push(`loss=${Number(p.loss).toFixed(4)}`);
        if (p.stop_loss != null) bits.push(`stop≤${Number(p.stop_loss).toFixed(3)}`);
        if (p.early_stop) bits.push(`EARLY_STOP${p.early_stop_reason ? ` (${p.early_stop_reason})` : ''}`);
        if (p.terms) {
            bits.push(
                Object.entries(p.terms)
                    .map(([id, t]) => `${id}=${t.loss != null ? Number(t.loss).toFixed(3) : '—'}`)
                    .join(' · '),
            );
        }
        push('policy', p.ok !== false, bits.join('\n'));
    }
    if (stages.telemetry) {
        const t = stages.telemetry;
        const bits = [];
        if (!t.want_camera) bits.push('skip (not first/new-best/Nth)');
        else {
            bits.push(`reason=${t.reason || '?'}`);
            bits.push(t.stashed ? 'stashed' : 'NOT stashed');
            if (t.tag) bits.push(`tag=${t.tag}`);
        }
        if (t.every_n != null) bits.push(`every_n=${t.every_n}`);
        push('telemetry', t.ok !== false && !t.error, bits.join('\n'));
    }
    return `<div class="osd-stages">${cards.join('')}</div>`;
}

export function formatTermsHtml(terms) {
    if (!terms || typeof terms !== 'object') {
        return `<div class="osd-idle">Term breakdown after first eval</div>`;
    }
    return `<div class="osd-terms">${Object.entries(terms)
        .map(([id, val]) => {
            const n = Number(val);
            const shown = Number.isFinite(n) ? n.toFixed(4) : String(val);
            return `<span class="osd-term">${escapeHtml(id)}: ${shown}</span>`;
        })
        .join('')}</div>`;
}

export function resolveCameraTag(labState, lastEval) {
    const sess = labState?.optimization_session;
    if (sess?.preview_camera_tag) return String(sess.preview_camera_tag);
    const cam = lastEval?.camera_image;
    if (cam && typeof cam === 'object' && cam.tag_id) return String(cam.tag_id);
    return null;
}

export function formatCameraHtml(labState, lastEval) {
    const tagId = resolveCameraTag(labState, lastEval);
    if (!tagId) {
        return `<div class="osd-idle">No camera tag on this session</div>`;
    }
    const preview = resolveCameraPreviewSource(labState, lastEval);
    const cam = preview.cam;
    let epoch = '';
    if (cam && typeof cam === 'object') {
        const prov = cam.provenance && typeof cam.provenance === 'object' ? cam.provenance : {};
        epoch = String(
            cam.epoch_ms ??
                prov.epoch_ms ??
                (preview.evalN != null ? `eval-${preview.evalN}` : ''),
        );
    }
    if (!epoch && !cam) {
        return `<div class="osd-idle">Camera ${escapeHtml(tagId)}: waiting for sparse preview frame</div>`;
    }
    const src = withBackendQuery(
        `/api/components/${encodeURIComponent(tagId)}/camera-image?t=${encodeURIComponent(epoch || Date.now())}`,
    );
    const evalLabel =
        preview.evalN != null ? `eval ${preview.evalN}` : 'eval unknown';
    const reasonLabel = preview.reason
        ? ` · ${preview.reason.replace(/_/g, ' ')}`
        : '';
    const currentEval =
        lastEval?.eval != null && Number.isFinite(Number(lastEval.eval))
            ? Number(lastEval.eval)
            : null;
    const lagNote =
        currentEval != null &&
        preview.evalN != null &&
        currentEval !== preview.evalN
            ? ` · latest run eval is ${currentEval}`
            : '';

    // Overlay uses the same eval row that owns the sparse camera frame.
    const overlays = extractKernelOverlays(preview.row);
    return `<div class="osd-camera">
        <div class="osd-section-title">${escapeHtml(tagId)} · sparse preview from <strong>${escapeHtml(evalLabel)}</strong>${escapeHtml(reasonLabel)}${escapeHtml(lagNote)}</div>
        <div class="osd-camera-stage">
            <img alt="OPTIMIZE ${escapeHtml(tagId)} ${escapeHtml(evalLabel)}" src="${src}" />
            <canvas class="osd-kernel-overlay" aria-hidden="true"></canvas>
        </div>
        ${overlayLegendHtml(overlays)}
    </div>`;
}

export function formatProgressLogHtml(rows) {
    const display = [...(rows || [])].reverse().slice(0, 80);
    if (!display.length) return `<div class="osd-idle">No progress rows yet</div>`;
    return `<div class="osd-log">${display
        .map((row) => {
            const loss = row.loss != null ? Number(row.loss).toFixed(4) : '—';
            const best = row.best_loss != null ? Number(row.best_loss).toFixed(4) : '—';
            const block = row.block_id || '—';
            const early = row.early_stop ? ' EARLY_STOP' : '';
            return `<div class="osd-log__row"><code>#${row.eval ?? '?'}</code> loss=${loss} best=${best} block=${escapeHtml(block)}${early}</div>`;
        })
        .join('')}</div>`;
}

/**
 * Build the full detail document body HTML for a lab-state snapshot.
 */
export function renderSessionDetailHtml(labState, { jobId = '', maxEvals = null } = {}) {
    const ctx = sessionContext(labState);
    if (!ctx) {
        return `<div class="osd-empty">
            <h2>No active or completed ensemble optimization</h2>
            <p>Start a run from Twin → Optimization, then open this page again (or leave it open — it polls).</p>
        </div>`;
    }
    const rows = collectTrace(labState);
    const last = ctx.lastEval;
    const prev = rows.length >= 2 ? rows[rows.length - 2] : null;
    const evalN = ctx.eval ?? 0;
    const cap = maxEvals || ctx.maxEvals || 200;
    const best =
        ctx.bestLoss != null && Number.isFinite(Number(ctx.bestLoss))
            ? Number(ctx.bestLoss).toFixed(4)
            : '—';
    const lastLoss =
        last?.loss != null && Number.isFinite(Number(last.loss))
            ? Number(last.loss).toFixed(4)
            : '—';
    const status = ctx.queued
        ? 'Starting — waiting for first eval'
        : ctx.running
          ? 'Running'
          : ctx.result?.early_stopped
            ? `Stopped — good enough${ctx.result?.early_stop_reason ? ` (${ctx.result.early_stop_reason})` : ''}`
            : ctx.result?.aborted
              ? 'Aborted'
              : 'Completed';
    const pct = Math.min(100, Math.round((evalN / Math.max(1, cap)) * 100));

    return `
        <section class="osd-hero">
            <div>
                <div class="osd-kicker">${escapeHtml(status)}</div>
                <h1>Optimization session</h1>
                <p class="osd-sub">
                    eval <strong>${evalN}</strong> / ${cap}
                    · latest loss <strong>${lastLoss}</strong>
                    · best <strong>${best}</strong>
                    ${ctx.sessionId ? `· <code>${escapeHtml(ctx.sessionId)}</code>` : ''}
                    ${jobId ? `· job <code>${escapeHtml(jobId)}</code>` : ''}
                </p>
            </div>
            <div class="osd-progress" title="Evaluation progress"><span style="width:${pct}%"></span></div>
        </section>
        <div class="osd-grid">
            <section class="osd-panel">
                <h2>Camera</h2>
                ${formatCameraHtml(labState, last)}
            </section>
            <section class="osd-panel">
                <h2>Tunables (this eval)</h2>
                ${formatTunablesHtml(last?.values, prev?.values)}
            </section>
            <section class="osd-panel">
                <h2>Objective terms</h2>
                ${formatTermsHtml(last?.terms)}
            </section>
            <section class="osd-panel osd-panel--wide">
                <h2>Stage debug (capture / kernel / actuate)</h2>
                ${formatStagesDetailHtml(last?.stages)}
            </section>
            <section class="osd-panel osd-panel--wide">
                <div class="osd-panel__head">
                    <h2>Iteration history</h2>
                    <button type="button" class="osd-btn osd-btn--ghost osd-btn--small" id="osd-save-history" ${rows.length ? '' : 'disabled'}>Save</button>
                </div>
                ${formatTraceTableHtml(rows)}
            </section>
            <section class="osd-panel osd-panel--wide">
                <h2>Progress log</h2>
                ${formatProgressLogHtml(rows)}
            </section>
        </div>
    `;
}

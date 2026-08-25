/**
 * Operations dashboard — backend picker + jobs (Phase G.3).
 */
import { setTrackedJob, renderJobTelemetry } from './job-monitor.js';
import {
    getSelectedBackendId,
    setSelectedBackendId,
    withBackendQuery,
} from '../state/backend-selection.js';

const jobsBody = document.getElementById('jobs-body');
const jobsTable = document.getElementById('jobs-table');
const jobsEmpty = document.getElementById('jobs-empty');
const jobDetail = document.getElementById('job-detail');
const backendsPanel = document.getElementById('backends-panel');
const jobsErrorEl = document.getElementById('jobs-error');

let selectedJobId = null;
/** @type {object[]} */
let cachedBackends = [];
let backendsShellReady = false;
let backendSelectBound = false;

function statusClass(status) {
    return `status status-${status || 'queued'}`;
}

function formatProgress(job) {
    const p = job.progress || {};
    if (p.phase === 'post_commit') {
        return p.message || 'commit';
    }
    if (p.phase === 'init') {
        const idx = p.init_step_index != null ? p.init_step_index + 1 : 0;
        const total = p.init_steps_total || '?';
        return `init ${idx}/${total}`;
    }
    if (job.mode === 'closed_loop') {
        if (job.status === 'succeeded') {
            const best =
                p.best_loss ??
                job.result?.last_ensemble_optimization?.best_loss;
            return best != null ? `best ${Number(best).toExponential(1)}` : 'done';
        }
        const evalN = p.eval != null ? `${p.eval}` : '—';
        return `e${evalN}`;
    }
    if (job.mode === 'compiled_dag') {
        const idx = p.step_index != null ? p.step_index + 1 : 0;
        const total = p.steps_total || '?';
        return `${idx}/${total}`;
    }
    return p.message || '—';
}

function modeLabel(mode) {
    if (mode === 'closed_loop') return 'loop';
    if (mode === 'compiled_dag') return 'dag';
    return mode || '—';
}

async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} → ${res.status}`);
    return res.json();
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function isBackendReady(row) {
    return row && row.availability === 'ready';
}

/** Prefer ready backends only — never leave the UI stuck on an unavailable selection. */
function resolveMonitorBackendId(rows) {
    const ready = rows.filter(isBackendReady);
    let current = getSelectedBackendId();
    if (current && ready.some((b) => b.backend_id === current)) {
        return current;
    }
    // Fail-closed: do not silently bind the first ready backend.
    return null;
}

function backendDetailHtml(selected) {
    const ready = isBackendReady(selected);
    const lease = selected.session_lease;
    const repos = (selected.control_repos || []).join(', ') || '—';
    const status = ready ? selected.system_status || 'ready' : selected.availability;
    const busy = selected.active_job_id
        ? `job ${String(selected.active_job_id).slice(0, 14)}…`
        : selected.queued_jobs
          ? `${selected.queued_jobs} queued`
          : 'idle';
    const cardClass = ready ? 'backend-card' : 'backend-card backend-card--unavailable';
    return `
        <article class="${cardClass}" data-backend-id="${escapeHtml(selected.backend_id)}">
            <h3>${escapeHtml(selected.label || selected.backend_id)}</h3>
            <dl class="backend-grid">
                <dt>ID</dt><dd><code>${escapeHtml(selected.backend_id)}</code></dd>
                <dt>Status</dt><dd class="${ready ? '' : 'backend-status-bad'}">${escapeHtml(status)}</dd>
                <dt>Communicator</dt><dd>${escapeHtml(selected.communicator || '—')} ${
                    selected.lab_mode ? `(${escapeHtml(selected.lab_mode)})` : ''
                }</dd>
                <dt>Repos</dt><dd>${escapeHtml(repos)}</dd>
                <dt>Queue</dt><dd>${escapeHtml(busy)}</dd>
                <dt>Lease</dt><dd>${escapeHtml(lease ? lease.holder : '—')}</dd>
                <dt>Edge</dt><dd class="${
                    selected.edge_offline
                        ? 'backend-status-bad'
                        : selected.edge_attached
                          ? ''
                          : ''
                }">${escapeHtml(
                    selected.edge_attached
                        ? `attached (${selected.edge_agent?.agent_id || 'agent'})`
                        : selected.edge_offline
                          ? `OFFLINE — ${selected.edge_offline.reason || 'stale heartbeat'}`
                          : 'not attached (in-process)'
                )}</dd>
            </dl>
            ${
                selected.edge_offline
                    ? `<p class="backend-unavailable-msg">
                        Edge agent disconnected. Jobs/leases were fail-closed.
                        ${
                            selected.edge_offline.disconnected_at
                                ? `<br><code>${escapeHtml(selected.edge_offline.disconnected_at)}</code>`
                                : ''
                        }
                       </p>`
                    : ''
            }
            ${
                !ready
                    ? `<p class="backend-unavailable-msg">
                        This backend is unavailable and cannot be monitored.
                        ${
                            selected.unavailable_reason
                                ? `<br><code>${escapeHtml(selected.unavailable_reason)}</code>`
                                : ''
                        }
                       </p>`
                    : ''
            }
        </article>`;
}

function syncBackendSelectOptions(select, rows, selectedId) {
    const sig = rows
        .map((b) => `${b.backend_id}:${b.availability}:${b.label || ''}`)
        .join('\0');
    if (select.dataset.backendSig === sig) {
        if (select.value !== selectedId) select.value = selectedId;
        return;
    }
    select.dataset.backendSig = sig;
    select.innerHTML = rows
        .map((b) => {
            const ready = isBackendReady(b);
            const label = ready
                ? b.label || b.backend_id
                : `${b.label || b.backend_id} — unavailable`;
            const sel = b.backend_id === selectedId ? ' selected' : '';
            const dis = ready ? '' : ' disabled';
            return `<option value="${escapeHtml(b.backend_id)}"${sel}${dis}>${escapeHtml(label)}</option>`;
        })
        .join('');
}

function ensureBackendsShell() {
    if (backendsShellReady && backendsPanel.querySelector('#ops-backend-select')) {
        return;
    }
    backendsPanel.innerHTML = `
        <label class="empty" for="ops-backend-select" style="display:block;margin-bottom:6px">
            Monitor backend
        </label>
        <select id="ops-backend-select" style="width:100%;margin-bottom:12px;background:#13151a;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:8px 10px;font-size: var(--text-base)"></select>
        <div id="ops-backend-detail"></div>
        <p class="empty" style="margin-top:12px">
            Twin + jobs below follow this selection. Unavailable backends are listed but not selectable.
            <a href="/twin">Take control (Twin UI)</a> ·
            <a href="/wiki">Wiki</a> ·
            <a href="/wiki#backends">Backends</a>
        </p>`;
    backendsShellReady = true;

    if (!backendSelectBound) {
        backendSelectBound = true;
        backendsPanel.addEventListener('change', (ev) => {
            const select = ev.target;
            if (!(select instanceof HTMLSelectElement) || select.id !== 'ops-backend-select') {
                return;
            }
            const next = select.value;
            const row = cachedBackends.find((b) => b.backend_id === next);
            if (!next || !isBackendReady(row)) {
                // Revert to last ready selection
                const readyId = resolveMonitorBackendId(cachedBackends);
                if (readyId) select.value = readyId;
                return;
            }
            if (next === getSelectedBackendId()) return;
            setSelectedBackendId(next);
            window.location.reload();
        });
    }
}

function renderBackends(data) {
    const rows = data.backends || [];
    cachedBackends = rows;
    if (!rows.length) {
        backendsShellReady = false;
        backendsPanel.textContent = 'No backends registered on this server.';
        return;
    }

    const currentId = resolveMonitorBackendId(rows);
    if (!currentId) {
        ensureBackendsShell();
        const detail = document.getElementById('ops-backend-detail');
        if (detail) {
            detail.innerHTML = `
                <p class="backend-unavailable-msg">
                    No ready backends on this server. Start a mock backend or fix the real lab path.
                </p>`;
        }
        const select = document.getElementById('ops-backend-select');
        if (select) syncBackendSelectOptions(select, rows, '');
        return;
    }

    const selected = rows.find((b) => b.backend_id === currentId) || rows.find(isBackendReady);
    ensureBackendsShell();

    const select = document.getElementById('ops-backend-select');
    const detail = document.getElementById('ops-backend-detail');
    if (!select || !detail || !selected) return;

    const selecting = document.activeElement === select;
    if (!selecting) {
        syncBackendSelectOptions(select, rows, selected.backend_id);
    }

    detail.innerHTML = backendDetailHtml(selected);
}

function renderJobs(data) {
    const jobs = data.jobs || [];
    if (!jobs.length) {
        jobsTable.hidden = true;
        jobsEmpty.hidden = false;
        return;
    }
    jobsEmpty.hidden = true;
    jobsTable.hidden = false;
    jobsBody.innerHTML = jobs
        .map(
            (job) => `
                <tr data-job-id="${job.job_id}" class="${
                    job.job_id === selectedJobId ? 'is-selected' : ''
                }" style="cursor:pointer">
                    <td title="${job.job_id}"><code>${job.job_id.slice(0, 10)}…</code></td>
                    <td><span class="job-mode-pill" title="${job.mode}">${modeLabel(job.mode)}</span></td>
                    <td><span class="${statusClass(job.status)}">${job.status}</span></td>
                    <td title="${formatProgress(job)}">${formatProgress(job)}</td>
                    <td>
                        ${
                            job.status === 'running' || job.status === 'queued'
                                ? `<button type="button" data-cancel="${job.job_id}">✕</button>`
                                : ''
                        }
                    </td>
                </tr>`,
        )
        .join('');

    jobsBody.querySelectorAll('tr[data-job-id]').forEach((row) => {
        row.addEventListener('click', async (ev) => {
            if (ev.target.closest('button')) return;
            selectedJobId = row.dataset.jobId;
            await refreshJobDetail();
        });
    });

    jobsBody.querySelectorAll('button[data-cancel]').forEach((btn) => {
        btn.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            const id = btn.dataset.cancel;
            await fetch(withBackendQuery(`/api/jobs/${id}/cancel`), { method: 'POST' });
            await refresh();
        });
    });
}

function setJobsFetchError(message) {
    if (jobsErrorEl) {
        jobsErrorEl.hidden = !message;
        jobsErrorEl.textContent = message || '';
        return;
    }
    // Fallback: keep jobs empty message informative without wiping backends.
    if (message && jobsEmpty) {
        jobsEmpty.hidden = false;
        jobsEmpty.textContent = message;
        if (jobsTable) jobsTable.hidden = true;
    }
}

async function refreshJobDetail() {
    if (!selectedJobId) {
        setTrackedJob(null);
        renderJobTelemetry(null);
        return;
    }
    try {
        const job = await fetchJson(
            withBackendQuery(`/api/jobs/${encodeURIComponent(selectedJobId)}`),
        );
        setTrackedJob(job);
    } catch (err) {
        setTrackedJob(null);
        renderJobTelemetry(null);
        if (jobDetail) {
            jobDetail.hidden = false;
            jobDetail.innerHTML =
                `<summary class="job-raw-summary">Raw JSON</summary>` +
                `<pre class="job-raw-pre">${String(err)}</pre>`;
        }
    }
}

async function maybeAutoFollowActiveJob(backendsPayload, jobs) {
    const currentId = getSelectedBackendId();
    const row = (backendsPayload.backends || []).find((b) => b.backend_id === currentId);
    if (!isBackendReady(row)) return;
    const activeId = row?.active_job_id;
    if (!activeId) return;

    const running = (jobs.jobs || []).find(
        (j) => j.job_id === activeId && (j.status === 'running' || j.status === 'queued'),
    );
    if (running && !selectedJobId) {
        selectedJobId = activeId;
        await refreshJobDetail();
    } else if (selectedJobId) {
        const current = (jobs.jobs || []).find((j) => j.job_id === selectedJobId);
        if (current) setTrackedJob(current);
    }
}

async function refresh() {
    let backends;
    try {
        backends = await fetchJson('/api/backends');
    } catch (err) {
        // Only wipe picker if the backends list itself fails.
        backendsPanel.textContent = `Could not load backends: ${err.message}`;
        backendsShellReady = false;
        return;
    }

    resolveMonitorBackendId(backends.backends || []);
    renderBackends(backends);

    const readyId = getSelectedBackendId();
    const selected = (backends.backends || []).find((b) => b.backend_id === readyId);
    if (!isBackendReady(selected)) {
        setJobsFetchError('No ready backend selected — jobs are paused until one is available.');
        return;
    }

    try {
        const jobs = await fetchJson(withBackendQuery('/api/jobs?limit=30'));
        setJobsFetchError('');
        if (jobsEmpty && jobsEmpty.textContent.startsWith('Could not')) {
            jobsEmpty.textContent = 'No jobs yet.';
        }
        renderJobs(jobs);
        await maybeAutoFollowActiveJob(backends, jobs);
        if (selectedJobId) await refreshJobDetail();
    } catch (err) {
        setJobsFetchError(`Jobs unavailable: ${err.message}`);
    }
}

refresh();
setInterval(refresh, 800);

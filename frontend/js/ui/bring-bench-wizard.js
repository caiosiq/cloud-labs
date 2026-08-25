/**
 * Checkout compatibility report + "bring bench to config" wizard.
 */
import { fetchCheckoutCompatibilityReport } from '../api/control.js';
import { enableComponentControl } from './inventory-add.js';
import { log } from './log.js';

function issueLabel(issue) {
    const tid = issue.tag_id ? ` ${issue.tag_id}` : '';
    switch (issue.kind) {
        case 'missing_in_runtime':
            return issue.in_library
                ? `Missing on bench${tid} — enable control`
                : `Missing on bench${tid} — not in component library`;
        case 'missing_in_catalog':
            return `Not in active catalog${tid}`;
        case 'extra_in_runtime':
            return `On bench but not in configuration${tid}`;
        case 'catalog_hash_mismatch':
            return 'Active catalog changed since this commit';
        default:
            return issue.kind || 'Issue';
    }
}

function promptBringBenchWizard(report) {
    const issues = Array.isArray(report.issues) ? report.issues : [];
    if (report.ready || issues.length === 0) {
        return Promise.resolve(true);
    }

    return new Promise((resolve) => {
        const existing = document.getElementById('bring-bench-wizard-modal');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'bring-bench-wizard-modal';
        overlay.style.cssText =
            'position:fixed;inset:0;background:rgba(0,0,0,0.82);z-index:3200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

        const card = document.createElement('div');
        card.style.cssText =
            'background:#181b21;border:1px solid #f59e0b;border-radius:8px;padding:24px;width:520px;max-height:82vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,0.7);';

        const title = document.createElement('h2');
        title.style.cssText = 'margin:0 0 8px 0;color:#e2e8f0;font-size:18px;';
        title.textContent = 'Bring bench to configuration';

        const sub = document.createElement('p');
        sub.style.cssText = 'margin:0 0 12px 0;color:#94a3b8;font-size: var(--text-base);line-height:1.5;';
        sub.textContent =
            'The live bench must include every component in the saved configuration before applying on the bench. Resolve missing parts below, then continue.';

        const list = document.createElement('div');
        list.style.cssText =
            'max-height:240px;overflow:auto;margin-bottom:12px;border:1px solid #2a2e36;border-radius:6px;padding:8px;';

        issues.forEach((issue) => {
            const row = document.createElement('div');
            row.style.cssText =
                'padding:8px 4px;border-bottom:1px solid rgba(42,46,54,0.6);font-size: var(--text-sm);color:#cbd5e1;';
            const blocking = issue.blocking ? ' · blocking' : '';
            row.textContent = `${issueLabel(issue)}${blocking}`;
            list.appendChild(row);
        });

        const hashNote = document.createElement('p');
        hashNote.style.cssText = 'margin:0 0 12px 0;color:#64748b;font-size: var(--text-sm);';
        const ch = report.catalog_hash || {};
        if (ch.commit && ch.current && !ch.match) {
            hashNote.textContent = `Catalog hash: commit ${ch.commit.slice(0, 8)}… vs current ${ch.current.slice(0, 8)}…`;
        }

        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;';

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn btn-secondary';
        cancelBtn.style.width = 'auto';
        cancelBtn.textContent = 'Cancel';

        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn btn-secondary';
        addBtn.style.width = 'auto';
        addBtn.textContent = 'Enable control for missing';
        addBtn.disabled = !report.missing_in_runtime?.length;

        const continueBtn = document.createElement('button');
        continueBtn.type = 'button';
        continueBtn.className = 'btn btn-primary';
        continueBtn.style.width = 'auto';
        continueBtn.textContent = 'Continue to apply';
        continueBtn.disabled = report.blocking_count > 0 || !report.ready;

        const finish = () => overlay.remove();

        cancelBtn.onclick = () => {
            finish();
            resolve(false);
        };

        addBtn.onclick = async () => {
            addBtn.disabled = true;
            const missing = report.missing_in_runtime || [];
            for (const tagId of missing) {
                const issue = issues.find((i) => i.tag_id === tagId);
                if (!issue || issue.suggested_action !== 'add_from_inventory') continue;
                await enableComponentControl(tagId);
            }
            finish();
            resolve(await runBringBenchWizard(report.configuration_id));
        };

        continueBtn.onclick = () => {
            finish();
            resolve(true);
        };

        overlay.addEventListener('click', (ev) => {
            if (ev.target === overlay) cancelBtn.click();
        });

        btnRow.appendChild(cancelBtn);
        btnRow.appendChild(addBtn);
        btnRow.appendChild(continueBtn);
        card.appendChild(title);
        card.appendChild(sub);
        card.appendChild(list);
        if (hashNote.textContent) card.appendChild(hashNote);
        card.appendChild(btnRow);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
    });
}

/**
 * Fetch compatibility report and run wizard when the bench is not ready.
 * @param {string} configurationId
 * @returns {Promise<boolean>} false if cancelled or blocking issues remain
 */
export async function runBringBenchWizard(configurationId) {
    try {
        const report = await fetchCheckoutCompatibilityReport(configurationId);
        if (report.blocking_count > 0) {
            log('Configuration includes parts not in the component library.', 'error');
            await promptBringBenchWizard(report);
            return false;
        }
        if (!report.ready) {
            const proceed = await promptBringBenchWizard(report);
            if (!proceed) return false;
            const refreshed = await fetchCheckoutCompatibilityReport(configurationId);
            if (refreshed.blocking_count > 0) return false;
            if (!refreshed.ready) {
                log('Bench still missing configuration components.', 'warn');
                return false;
            }
        }
        return true;
    } catch (err) {
        log(err.message || 'Compatibility check failed', 'error');
        return false;
    }
}

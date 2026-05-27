/**
 * Shared DOM + formatting primitives for widget modules.
 *
 * Widgets stay pure (no static dependencies on app modules) — they get
 * platform escape hatches via the ``hooks`` argument. This file
 * intentionally has no app-state imports: widgets compose from these
 * small primitives + the descriptor passed in by the viewer.
 */

/**
 * Field-label / value row, two-column. Used by every read-only widget.
 * @param {string} label
 * @param {string} value
 * @param {{dim?: boolean, mono?: boolean}} [opts]
 */
export function row(label, value, opts) {
    const dim = !!(opts && opts.dim);
    const mono = !(opts && opts.mono === false);
    const r = document.createElement('div');
    r.style.display = 'flex';
    r.style.justifyContent = 'space-between';
    r.style.gap = '8px';
    r.style.alignItems = 'baseline';
    const k = document.createElement('span');
    k.style.color = '#64748b';
    k.style.fontSize = '10px';
    k.textContent = label;
    const v = document.createElement('span');
    v.style.color = dim ? '#475569' : '#cbd5e1';
    v.style.fontFamily = mono ? 'ui-monospace, monospace' : 'inherit';
    v.style.fontStyle = dim ? 'italic' : 'normal';
    v.style.fontSize = '11px';
    v.textContent = value;
    r.appendChild(k);
    r.appendChild(v);
    return r;
}

/**
 * Build the small "<scope>.<field>" title that every widget shows at top.
 * Helps the operator see which catalog descriptor produced the block.
 */
export function widgetTitle(fieldName, descriptor) {
    const el = document.createElement('div');
    el.style.fontSize = '9px';
    el.style.color = '#94a3b8';
    el.style.fontWeight = '600';
    el.style.letterSpacing = '0.05em';
    el.style.marginBottom = '6px';
    el.style.display = 'flex';
    el.style.justifyContent = 'space-between';
    el.style.gap = '8px';
    const name = document.createElement('span');
    name.textContent = String(fieldName).toUpperCase();
    const tag = document.createElement('span');
    tag.style.color = '#475569';
    tag.style.fontWeight = '400';
    tag.style.fontFamily = 'ui-monospace, monospace';
    tag.textContent = (descriptor && descriptor.widget) || '';
    el.appendChild(name);
    el.appendChild(tag);
    return el;
}

/** Standard widget card chassis (dark panel). */
export function widgetCard() {
    const box = document.createElement('div');
    box.style.background = '#0f1115';
    box.style.border = '1px solid #2a2e36';
    box.style.borderRadius = '6px';
    box.style.padding = '8px';
    box.style.fontSize = '10px';
    box.style.lineHeight = '1.5';
    box.style.color = '#cbd5e1';
    return box;
}

/** Format a finite number with N digits, ``"?"`` otherwise. */
export function fmtNum(n, digits) {
    return Number.isFinite(Number(n)) ? Number(n).toFixed(digits || 0) : '?';
}

/** Default decimal places for table / in-air pose coordinates (mm, °). */
export const POSE_MM_DECIMALS = 2;

/** Format x/y/z/rotation for move, hover, and pose readouts. */
export function fmtPoseMm(n) {
    return fmtNum(n, POSE_MM_DECIMALS);
}

/** Format a printf-style descriptor.format string (e.g. ".3f", "d"). */
export function fmtByDescriptor(value, format) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return '\u2014';
    }
    const num = Number(value);
    if (typeof format === 'string' && format) {
        const m = /^\.(\d+)f$/.exec(format);
        if (m) return num.toFixed(Number(m[1]));
        if (format === 'd') return Math.round(num).toString();
    }
    return String(num);
}

/** Substitute ``{tag_id}`` (and similar) tokens in catalog telemetry URLs. */
export function resolveTokens(template, ctx) {
    if (typeof template !== 'string') return '';
    return template.replace(/\{(\w+)\}/g, (full, key) => {
        const k = key === 'tag_id' ? 'tagId' : key;
        const v = ctx && ctx[k];
        return v !== undefined && v !== null ? String(v) : full;
    });
}

/** Dimmed "no record yet" placeholder used by read-only widgets. */
export function nullPlaceholder(text) {
    const el = document.createElement('div');
    el.style.fontStyle = 'italic';
    el.style.color = '#475569';
    el.style.fontSize = '11px';
    el.textContent = text || '\u2014 null';
    return el;
}

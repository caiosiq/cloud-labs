/**
 * Resolve Command Console component refs (tag id or library name) and
 * tokenize lines with optional double-quoted arguments.
 */

import { isOnTableComponent } from './component-model.js';
import { store } from './state/store.js';

/**
 * Split a console line into tokens; double-quoted segments keep internal spaces.
 * Quotes are stripped from the token value. No escape sequences.
 * @param {string} line
 * @returns {string[]}
 */
export function tokenizeCommandLine(line) {
    const raw = String(line || '').trim();
    if (!raw) return [];
    const tokens = [];
    let i = 0;
    while (i < raw.length) {
        while (i < raw.length && /\s/.test(raw[i])) i += 1;
        if (i >= raw.length) break;
        if (raw[i] === '"') {
            i += 1;
            let buf = '';
            while (i < raw.length && raw[i] !== '"') {
                buf += raw[i];
                i += 1;
            }
            if (i < raw.length && raw[i] === '"') i += 1;
            tokens.push(buf);
            continue;
        }
        let buf = '';
        while (i < raw.length && !/\s/.test(raw[i])) {
            buf += raw[i];
            i += 1;
        }
        tokens.push(buf);
    }
    return tokens;
}

function _norm(s) {
    return String(s || '')
        .trim()
        .toLowerCase()
        .replace(/[\u2013\u2014]/g, '-') // en/em dash → hyphen
        .replace(/\s+/g, ' ');
}

/**
 * @param {object} deps
 * @returns {Record<string, object>}
 */
function _catalogMap(deps) {
    if (deps && typeof deps.getCatalogMap === 'function') {
        return deps.getCatalogMap() || {};
    }
    return store.catalogMap || {};
}

/**
 * @param {object} deps
 * @returns {object | null}
 */
function _labState(deps) {
    if (deps && typeof deps.getLabState === 'function') {
        return deps.getLabState();
    }
    return store.labState;
}

/**
 * Resolve a console component argument to a tag id.
 * Accepts tag ids (`tag_22`) or catalog/library names (case-insensitive).
 * Prefers uniquely matching placed (on-table) components when names collide.
 *
 * @param {string} ref
 * @param {object} deps
 * @returns {{ ok: true, tagId: string, matchedBy: 'tag_id' | 'name' }
 *   | { ok: false, error: string }}
 */
export function resolveComponentRef(ref, deps = {}) {
    const raw = String(ref || '').trim();
    if (!raw) {
        return { ok: false, error: 'Missing component (tag id or name).' };
    }

    const catalog = _catalogMap(deps);
    const lab = _labState(deps);
    const components = (lab && lab.components) || {};

    if (components[raw] || catalog[raw]) {
        return { ok: true, tagId: raw, matchedBy: 'tag_id' };
    }

    // Common alias: "22" → tag_22 when present
    if (!raw.startsWith('tag_')) {
        const asTag = `tag_${raw}`;
        if (components[asTag] || catalog[asTag]) {
            return { ok: true, tagId: asTag, matchedBy: 'tag_id' };
        }
    }

    const needle = _norm(raw);
    /** @type {{ tagId: string, name: string, placed: boolean }[]} */
    const hits = [];
    const seen = new Set();

    const consider = (tagId, name) => {
        if (!tagId || seen.has(tagId)) return;
        const n = _norm(name);
        if (!n || n !== needle) return;
        seen.add(tagId);
        hits.push({
            tagId,
            name: String(name),
            placed: isOnTableComponent(components[tagId]),
        });
    };

    Object.keys(catalog).forEach((tagId) => {
        const row = catalog[tagId];
        if (row && row.name) consider(tagId, row.name);
    });
    Object.keys(components).forEach((tagId) => {
        const row = catalog[tagId];
        if (row && row.name) consider(tagId, row.name);
        // Presence-only fallback: no catalog name
    });

    if (hits.length === 0) {
        return {
            ok: false,
            error: `Unknown component "${raw}". Use a tag id (tag_22) or library name (quotes if it has spaces).`,
        };
    }

    const placed = hits.filter((h) => h.placed);
    const pool = placed.length ? placed : hits;
    if (pool.length > 1) {
        const list = pool.map((h) => `${h.tagId} (${h.name})`).join(', ');
        return {
            ok: false,
            error: `Ambiguous name "${raw}" matches: ${list}. Use the tag id.`,
        };
    }
    return { ok: true, tagId: pool[0].tagId, matchedBy: 'name' };
}

/**
 * Completion candidates: placed tag ids + quoted catalog names for those tags.
 * @param {object} deps
 * @returns {string[]}
 */
export function componentRefCompletions(deps = {}) {
    const catalog = _catalogMap(deps);
    const lab = _labState(deps);
    const components = (lab && lab.components) || {};
    const tags = Object.keys(components)
        .filter((id) => isOnTableComponent(components[id]))
        .sort();
    const out = [...tags];
    tags.forEach((tagId) => {
        const name = catalog[tagId]?.name;
        if (!name || typeof name !== 'string') return;
        const trimmed = name.trim();
        if (!trimmed) return;
        if (/\s/.test(trimmed) || trimmed.includes('"')) {
            out.push(`"${trimmed}"`);
        } else {
            out.push(trimmed);
        }
    });
    return out;
}

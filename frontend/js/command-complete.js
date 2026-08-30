/**
 * Context-aware Tab completions for the Command Console (small grammar).
 * @see coding_on_the_ui.md
 */

import { componentRefCompletions } from './command-resolve.js';
import { isStoredComponent } from './component-model.js';
import { store } from './state/store.js';

const VERBS = [
    '?',
    'help',
    'lasers',
    'refresh',
    'tunables',
    'get_tunables',
    'measurables',
    'get_measurables',
    'record',
    'record_measurables',
    'move',
    'movelaser',
    'move-laser',
    'stitch',
    'store',
    'storecell',
    'tostorage',
    'place',
    'fromstorage',
    'drag',
    'placelaser',
    'place-laser',
    'draglaser',
    'motor',
    'motorhome',
    'motorset0',
    'json',
];

/**
 * @param {string[]} list
 * @param {string} prefix
 */
function filterPrefix(list, prefix) {
    if (!prefix) return [...list];
    const p = prefix.toLowerCase();
    return list.filter((x) => String(x).toLowerCase().startsWith(p));
}

/** All component keys in current lab state (for tunables/measurables queries). */
function allTagIds(deps) {
    const ls = deps.getLabState && deps.getLabState();
    if (!ls || !ls.components) return [];
    return Object.keys(ls.components).sort();
}

function motorIdStrings(deps, tagId) {
    const entry = deps.getCatalogEntry && deps.getCatalogEntry(tagId);
    if (!entry || !entry.motor_ids || !entry.motor_ids.length) return [];
    return entry.motor_ids.map(String);
}

function laserLineCompletions() {
    const doc = store.laserLinesDoc;
    if (!doc || !Array.isArray(doc.lines)) return [];
    const out = [];
    doc.lines.forEach((ln) => {
        if (!ln || !ln.id) return;
        out.push(String(ln.id));
        const name = ln.name && String(ln.name).trim();
        if (!name) return;
        if (/\s/.test(name) || name.includes('"')) {
            out.push(`"${name}"`);
        } else {
            out.push(name);
        }
    });
    return out.sort();
}

function placedComponentCompletions(deps) {
    return componentRefCompletions(deps);
}

/** Completions for parts currently in storage (place / placelaser). */
function storedComponentCompletions(deps) {
    const ls = deps.getLabState && deps.getLabState();
    const catalog = store.catalogMap || {};
    if (!ls || !ls.components) return [];
    const tags = Object.keys(ls.components)
        .filter((id) => isStoredComponent(ls.components[id]))
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

/** Completions for on-table parts not already stored (store). */
function storableComponentCompletions(deps) {
    const ls = deps.getLabState && deps.getLabState();
    const catalog = store.catalogMap || {};
    if (!ls || !ls.components) return [];
    const tags = Object.keys(ls.components)
        .filter((id) => ls.components[id] && !isStoredComponent(ls.components[id]))
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

function allComponentCompletions(deps) {
    const ls = deps.getLabState && deps.getLabState();
    const catalog = store.catalogMap || {};
    const tags = allTagIds(deps);
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
    if (ls) {
        Object.keys(catalog).forEach((tagId) => {
            if (tags.includes(tagId)) return;
            const name = catalog[tagId]?.name;
            if (!name || typeof name !== 'string') return;
            const trimmed = name.trim();
            if (!trimmed) return;
            out.push(tagId);
            if (/\s/.test(trimmed) || trimmed.includes('"')) {
                out.push(`"${trimmed}"`);
            } else {
                out.push(trimmed);
            }
        });
    }
    return out;
}

/**
 * Parse the line fragment before the caret into tokens and the word being completed.
 * @param {string} before
 */
export function parsePartialLine(before) {
    const endsWithSpace = /\s$/.test(before);
    const trimmed = before.trimEnd();
    const tokens = trimmed.length ? trimmed.split(/\s+/).filter(Boolean) : [];

    if (endsWithSpace) {
        return { endsWithSpace: true, tokens, current: '', prefix: before };
    }

    if (!trimmed.length) {
        return { endsWithSpace: false, tokens: [], current: '', prefix: '' };
    }

    const lastSpace = trimmed.lastIndexOf(' ');
    const current = lastSpace < 0 ? trimmed : trimmed.slice(lastSpace + 1);
    const prefix = before.slice(0, Math.max(0, before.length - current.length));
    return { endsWithSpace: false, tokens, current, prefix };
}

/**
 * Character range in `line` that Tab replaces: [start, end) (end === caret before replace).
 * @param {string} line
 * @param {number} caret
 * @returns {{ start: number, end: number }}
 */
export function getCompletionSlot(line, caret) {
    const before = line.slice(0, caret);
    const { current, endsWithSpace } = parsePartialLine(before);
    const start = endsWithSpace ? before.length : before.length - current.length;
    return { start, end: caret };
}

function isMoveLaserVerb(verb) {
    return verb === 'movelaser' || verb === 'move-laser' || verb === 'stitch';
}

function isStoreVerb(verb) {
    return verb === 'store' || verb === 'storecell' || verb === 'tostorage';
}

function isPlaceVerb(verb) {
    return verb === 'place' || verb === 'fromstorage' || verb === 'drag';
}

function isPlaceLaserVerb(verb) {
    return verb === 'placelaser' || verb === 'place-laser' || verb === 'draglaser';
}

/**
 * Return candidate strings to insert in place of `current` (or after `prefix` when starting a new token).
 * @param {string} line
 * @param {number} caret
 * @param {object} deps
 * @returns {string[]}
 */
export function getTabCompletions(line, caret, deps) {
    const before = line.slice(0, caret);
    const { endsWithSpace, tokens, current } = parsePartialLine(before);
    const placed = placedComponentCompletions(deps);
    const stored = storedComponentCompletions(deps);
    const storable = storableComponentCompletions(deps);
    const allComps = allComponentCompletions(deps);

    if (tokens.length === 0) {
        return filterPrefix(VERBS, current);
    }

    const verbRaw = tokens[0];
    const verb = verbRaw.toLowerCase();

    if (tokens.length === 1 && !endsWithSpace) {
        return filterPrefix(VERBS, current);
    }

    if (tokens.length === 1 && endsWithSpace) {
        const v = verb;
        if (v === 'json' || v === 'help' || v === '?' || v === 'refresh' || v === 'lasers') {
            return [];
        }
        if (
            v === 'tunables' ||
            v === 'get_tunables' ||
            v === 'measurables' ||
            v === 'get_measurables' ||
            v === 'record' ||
            v === 'record_measurables'
        ) {
            return allComps;
        }
        if (isStoreVerb(v)) return storable;
        if (isPlaceVerb(v) || isPlaceLaserVerb(v)) return stored;
        if (
            v === 'move' ||
            isMoveLaserVerb(v) ||
            v === 'motor' ||
            v === 'motorhome' ||
            v === 'motorset0' ||
            v === 'pick' ||
            v === 'hover' ||
            v === 'placehover' ||
            v === 'confirmhold'
        ) {
            return placed;
        }
        return [];
    }

    if (verb === 'json' || verb === 'help' || verb === '?' || verb === 'refresh' || verb === 'lasers') {
        return [];
    }

    if (
        verb === 'tunables' ||
        verb === 'get_tunables' ||
        verb === 'measurables' ||
        verb === 'get_measurables' ||
        verb === 'record' ||
        verb === 'record_measurables'
    ) {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(allComps, current);
        }
        return [];
    }

    if (verb === 'move') {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(placed, current);
        }
        return [];
    }

    if (isMoveLaserVerb(verb)) {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(placed, current);
        }
        if (tokens.length === 4 && endsWithSpace) {
            return laserLineCompletions();
        }
        if (tokens.length === 5 && !endsWithSpace) {
            return filterPrefix(laserLineCompletions(), current);
        }
        return [];
    }

    if (isStoreVerb(verb)) {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(storable, current);
        }
        return [];
    }

    if (isPlaceVerb(verb)) {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(stored, current);
        }
        return [];
    }

    if (isPlaceLaserVerb(verb)) {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(stored, current);
        }
        if (tokens.length === 4 && endsWithSpace) {
            return laserLineCompletions();
        }
        if (tokens.length === 5 && !endsWithSpace) {
            return filterPrefix(laserLineCompletions(), current);
        }
        return [];
    }

    if (verb === 'motor') {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(placed, current);
        }
        if (tokens.length === 2 && endsWithSpace) {
            const mids = motorIdStrings(deps, tokens[1]);
            return mids.length ? mids : [];
        }
        if (tokens.length === 3 && !endsWithSpace) {
            return filterPrefix(motorIdStrings(deps, tokens[1]), current);
        }
        return [];
    }

    if (verb === 'motorhome' || verb === 'motorset0') {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(placed, current);
        }
        if (tokens.length === 2 && endsWithSpace) {
            const mids = motorIdStrings(deps, tokens[1]);
            return mids.length ? mids : [];
        }
        if (tokens.length === 3 && !endsWithSpace) {
            return filterPrefix(motorIdStrings(deps, tokens[1]), current);
        }
        return [];
    }

    if (verb === 'pick' || verb === 'confirmhold') {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(placed, current);
        }
        return [];
    }

    if (verb === 'hover' || verb === 'placehover') {
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(placed, current);
        }
        return [];
    }

    return [];
}

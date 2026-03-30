/**
 * Context-aware Tab completions for the Command Console (small grammar).
 * @see coding_on_the_ui.md
 */

import { COBYLA_DEFAULT_OBJECTIVE, NEWTON_DEFAULTS } from './command-parse.js';

const VERBS = ['?', 'help', 'refresh', 'move', 'motor', 'optimize', 'json'];
const STRATEGIES = ['NEWTON', 'COBYLA'];
const AXES = ['x', 'y'];

/**
 * @param {string[]} list
 * @param {string} prefix
 */
function filterPrefix(list, prefix) {
    if (!prefix) return [...list];
    const p = prefix.toLowerCase();
    return list.filter((x) => String(x).toLowerCase().startsWith(p));
}

function placedTagIds(deps) {
    const ls = deps.getLabState && deps.getLabState();
    if (!ls || !ls.components) return [];
    return Object.keys(ls.components)
        .filter((id) => ls.components[id].state === 'PLACED')
        .sort();
}

function motorIdStrings(deps, tagId) {
    const entry = deps.getCatalogEntry && deps.getCatalogEntry(tagId);
    if (!entry || !entry.motor_ids || !entry.motor_ids.length) return [];
    return entry.motor_ids.map(String);
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
    const tags = placedTagIds(deps);

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
        if (v === 'json' || v === 'help' || v === '?' || v === 'refresh') {
            return [];
        }
        if (v === 'move' || v === 'motor' || v === 'optimize') {
            return tags;
        }
        return [];
    }

    if (verb === 'json') {
        return [];
    }

    if (verb === 'help' || verb === '?') {
        return [];
    }

    if (verb === 'refresh') {
        return [];
    }

    if (verb === 'move') {
        if (tokens.length === 1 && endsWithSpace) {
            return tags;
        }
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(tags, current);
        }
        return [];
    }

    if (verb === 'motor') {
        if (tokens.length === 1 && endsWithSpace) {
            return tags;
        }
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(tags, current);
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

    if (verb === 'optimize') {
        if (tokens.length === 1 && endsWithSpace) {
            return tags;
        }
        if (tokens.length === 2 && !endsWithSpace) {
            return filterPrefix(tags, current);
        }
        if (tokens.length === 2 && endsWithSpace) {
            return STRATEGIES;
        }
        if (tokens.length === 3 && !endsWithSpace) {
            return filterPrefix(STRATEGIES, current);
        }

        const strategy = tokens[2] && tokens[2].toUpperCase();

        if (strategy === 'NEWTON') {
            const dCam = String(NEWTON_DEFAULTS.camera_number);
            const dPx = String(NEWTON_DEFAULTS.target_x_pixel);
            const dTol = String(NEWTON_DEFAULTS.tolerance_ratio);

            if (tokens.length === 3 && endsWithSpace) {
                return [dCam];
            }
            if (tokens.length === 4 && !endsWithSpace) {
                return filterPrefix([dCam], current);
            }
            if (tokens.length === 4 && endsWithSpace) {
                return [dPx];
            }
            if (tokens.length === 5 && !endsWithSpace) {
                return filterPrefix([dPx], current);
            }
            if (tokens.length === 5 && endsWithSpace) {
                return AXES;
            }
            if (tokens.length === 6 && !endsWithSpace) {
                return filterPrefix(AXES, current);
            }
            if (tokens.length === 6 && endsWithSpace) {
                return [dTol];
            }
            if (tokens.length === 7 && !endsWithSpace) {
                return filterPrefix([dTol], current);
            }
            return [];
        }

        if (strategy === 'COBYLA') {
            const dTh = String(COBYLA_DEFAULT_OBJECTIVE);
            if (tokens.length === 3 && endsWithSpace) {
                return [dTh];
            }
            if (tokens.length === 4 && !endsWithSpace) {
                return filterPrefix([dTh], current);
            }
            return [];
        }

        return [];
    }

    return [];
}

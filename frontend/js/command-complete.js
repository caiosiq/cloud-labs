/**
 * Context-aware Tab completions for the Command Console.
 */

import {
    CONSOLE_VERBS,
    LOSS_METRICS_BY_STRATEGY,
    OPTIMIZE_STRATEGIES,
    normalizeVerb,
} from './command-grammar.js';
import { isOnTableComponent, isStoredComponent } from './component-model.js';
import {
    defaultOptimizeSensor,
    listOptimizeSensorCandidates,
} from './optimize-session.js';

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
        .filter((id) => isOnTableComponent(ls.components[id]) && !isStoredComponent(ls.components[id]))
        .sort();
}

function storedTagIds(deps) {
    const ls = deps.getLabState && deps.getLabState();
    if (!ls || !ls.components) return [];
    return Object.keys(ls.components)
        .filter((id) => isStoredComponent(ls.components[id]))
        .sort();
}

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

function optimizeSensorIds(deps, tagId) {
    try {
        return listOptimizeSensorCandidates(tagId);
    } catch {
        return [];
    }
}

function lossMetricsForStrategy(strategy) {
    return LOSS_METRICS_BY_STRATEGY[strategy] || [];
}

function cameraImageTagIds(deps) {
    const ls = deps.getLabState && deps.getLabState();
    if (!ls || !ls.components) return [];
    return Object.keys(ls.components)
        .filter((id) => {
            const ci = ls.components[id]?.measurables?.camera_image;
            return ci && typeof ci === 'object' && ci.path;
        })
        .sort();
}

/** Verbs that take a single tag argument with no further tokens. */
const TAG_ONLY_VERBS = new Set([
    'store',
    'repack',
    'recenter',
    'affirm',
    'pick',
    'confirmhold',
    'confirmholdingtag',
    'startteleop',
    'endteleop',
]);

const STORED_ONLY_TAG_VERBS = new Set(['repack', 'recenter', 'affirm']);
const PLACED_ONLY_TAG_VERBS = new Set(['store', 'pick', 'confirmhold', 'confirmholdingtag']);
const CAMERA_REFERENCE_TAG_VERBS = new Set(['setcobylareference']);

/** Verbs that complete tag ids from all components. */
const ALL_TAG_VERBS = new Set([
    'tunables',
    'gettunables',
    'measurables',
    'getmeasurables',
    'record',
    'recordmeasurables',
    'setexposure',
    'startlivefeed',
    'endlivefeed',
    'teleopgoto',
]);

/** Verbs that complete tag ids from placed (on-table, not stored) components. */
const PLACED_TAG_VERBS = new Set([
    'move',
    'motor',
    'motorhome',
    'motorset0',
    'setmotor',
    'optimize',
    'hover',
    'placehover',
    'placefromhover',
    'scanrotate',
]);

/** Verbs that complete tag ids from stored components. */
const STORED_TAG_VERBS = new Set(['placefromstorage', 'placefromstore']);

/**
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

function completeTagArg(tags, tokens, endsWithSpace, current) {
    if (tokens.length === 1 && endsWithSpace) return tags;
    if (tokens.length === 2 && !endsWithSpace) return filterPrefix(tags, current);
    return [];
}

function completeMotorId(deps, tokens, endsWithSpace, current) {
    if (tokens.length === 2 && endsWithSpace) {
        const mids = motorIdStrings(deps, tokens[1]);
        return mids.length ? mids : [];
    }
    if (tokens.length === 3 && !endsWithSpace) {
        return filterPrefix(motorIdStrings(deps, tokens[1]), current);
    }
    return [];
}

function completeOptimize(deps, tokens, endsWithSpace, current) {
    const tags = placedTagIds(deps);
    if (tokens.length === 1 && endsWithSpace) return tags;
    if (tokens.length === 2 && !endsWithSpace) return filterPrefix(tags, current);
    if (tokens.length === 2 && endsWithSpace) return OPTIMIZE_STRATEGIES;
    if (tokens.length === 3 && !endsWithSpace) return filterPrefix(OPTIMIZE_STRATEGIES, current);

    const strategy = tokens[2] && tokens[2].toUpperCase();
    const target = tokens[1];
    const sensors = optimizeSensorIds(deps, target);
    const defaultSensor = target ? (defaultOptimizeSensor(target) || sensors[0] || '') : '';
    const metrics = lossMetricsForStrategy(strategy);

    if (tokens.length === 3 && endsWithSpace) {
        return sensors.length ? sensors : (defaultSensor ? [defaultSensor] : []);
    }
    if (tokens.length === 4 && !endsWithSpace) {
        return filterPrefix(sensors.length ? sensors : (defaultSensor ? [defaultSensor] : []), current);
    }
    if (tokens.length === 4 && endsWithSpace) {
        return metrics;
    }
    if (tokens.length === 5 && !endsWithSpace) {
        return filterPrefix(metrics, current);
    }
    return [];
}

/**
 * @param {string} line
 * @param {number} caret
 * @param {object} deps
 * @returns {string[]}
 */
export function getTabCompletions(line, caret, deps) {
    const before = line.slice(0, caret);
    const { endsWithSpace, tokens, current } = parsePartialLine(before);

    if (tokens.length === 0) {
        return filterPrefix(CONSOLE_VERBS, current);
    }

    const verb = normalizeVerb(tokens[0]);

    if (tokens.length === 1 && !endsWithSpace) {
        return filterPrefix(CONSOLE_VERBS, current);
    }

    if (verb === 'json' || verb === 'help' || verb === '?' || verb === 'refresh' || verb === 'getstorage') {
        return [];
    }

    if (ALL_TAG_VERBS.has(verb)) {
        return completeTagArg(allTagIds(deps), tokens, endsWithSpace, current);
    }

    if (STORED_TAG_VERBS.has(verb)) {
        return completeTagArg(storedTagIds(deps), tokens, endsWithSpace, current);
    }

    if (PLACED_TAG_VERBS.has(verb)) {
        const tags = placedTagIds(deps);
        if (verb === 'optimize') {
            return completeOptimize(deps, tokens, endsWithSpace, current);
        }
        if (verb === 'motor' || verb === 'motorhome' || verb === 'motorset0' || verb === 'setmotor') {
            if (tokens.length === 1 && endsWithSpace) return tags;
            if (tokens.length === 2 && !endsWithSpace) return filterPrefix(tags, current);
            return completeMotorId(deps, tokens, endsWithSpace, current);
        }
        return completeTagArg(tags, tokens, endsWithSpace, current);
    }

    if (TAG_ONLY_VERBS.has(verb)) {
        let tags = allTagIds(deps);
        if (STORED_ONLY_TAG_VERBS.has(verb)) tags = storedTagIds(deps);
        else if (PLACED_ONLY_TAG_VERBS.has(verb)) tags = placedTagIds(deps);
        return completeTagArg(tags, tokens, endsWithSpace, current);
    }

    if (CAMERA_REFERENCE_TAG_VERBS.has(verb)) {
        return completeTagArg(cameraImageTagIds(deps), tokens, endsWithSpace, current);
    }

    return [];
}

/**
 * Shorthand parser for the Command Console.
 * @see coding_on_the_ui.md
 */

import { tokenizeCommandLine } from './command-resolve.js';

/**
 * @returns {string}
 */
export function formatHelp() {
    return [
        'Commands (whitespace-separated; use "quotes" for names with spaces):',
        '  help | ?          — this text',
        '  lasers            — list laser line ids / names (for movelaser)',
        '  refresh           — refresh poses from camera (same as Refresh Pose button)',
        '  labstate [--json] — report the complete current lab state (alias: state)',
        '  tunables <tag|name>    — print tunables JSON (alias: get_tunables)',
        '  measurables <tag|name> — print measurables JSON (alias: get_measurables; saved state only)',
        '  record <tag|name>      — RECORD_MEASURABLES (poll lab; e.g. camera → measurables.camera_image)',
        '  simreset list|current|default|<preset> — restart MuJoCo from a simulation state',
        '  simsave <preset> [--overwrite]         — save the current simulation state',
        '  simshow current|default|<preset>        — print editable preset JSON',
        '  simwrite <preset> [--overwrite] <json>  — validate and save preset JSON without loading it',
        '  simcomponent list                        — list simple experiment-facing component records',
        '  simcomponent nexttag                     — return the next unused numeric tag',
        '  simcomponent show <tag>                  — show one simple component record',
        '  simcomponent define <tag> <json>          — define a reusable generic/black-box component',
        '  simcomponent configure <tag> <json>       — change name/type/parameters',
        '  simcomponent reset <tag>                  — remove overrides from a built-in definition',
        '  simcomponent insert <tag> <x> <y> <rot>   — add a library component to the table',
        '  simcomponent insert <tag> storage <i> <j> — add a library component to storage',
        '  simcomponent remove <tag>                 — remove an active component, keep its definition',
        '  simcomponent delete <tag>                 — delete an inactive custom definition',
        '  simclear [table|all]                      — clear table parts or every active component',
        '  move <tag|name> <x> <y> <rot>     — MOVE_COMPONENT (lab mm, degrees)',
        '  movelaser <tag|name> <y> <rot> [line_id|name] — MOVE on laser (X from line at Y; omit line → snap)',
        '  store <tag|name>              — STORE_COMPONENT (autopack into a free cell)',
        '  store <tag|name> <i> <j>      — STORE_COMPONENT into storage cell (i, j)',
        '  place <tag|name> <x> <y> <rot> — PLACE_FROM_STORAGE onto breadboard',
        '  placelaser <tag|name> <y> <rot> [line_id|name] — PLACE_FROM_STORAGE stitched to laser',
        '  motor <tag|name> <motor_id> <dist> — MOVE_MOTOR',
        '  motorhome <tag|name> <motor_id>   — MOTOR_SEND_HOME (move by −θ → tracked 0)',
        '  motorset0 <tag|name> <motor_id>   — MOTOR_SET_ZERO (current pos → θ=0, no move)',
        '',
        'In-air manipulation (see new_primitives.md):',
        '  pick <tag|name>                          — PICK_COMPONENT (grasp + lift → HOLDING)',
        '  hover <tag|name> <x> <y> <rot> <z>       — HOVER (re-pose held part in air)',
        '  placehover <tag|name> <x> <y> <rot>      — PLACE_FROM_HOVER (held → on table)',
        '  confirmhold <tag|name>                   — CONFIRM_HOLDING_TAG (clear unconfirmed flag)',
        '',
        '  json <object>       — raw POST /api/command body (single-line JSON)',
        '',
        'Component args accept tag ids (tag_22) or library names. Laser args accept id or name.',
        'Tab cycles completions for the current word (commands, tags, names, laser ids).',
        ''
    ].join('\n');
}

/**
 * @param {string} line
 * @returns {{ ok: true, result: object } | { ok: false, error: string }}
 */
export function parseCommandLine(line) {
    const raw = line.trim();
    if (!raw) {
        return { ok: false, error: 'Empty line.' };
    }

    const jsonMatch = raw.match(/^json\s+(.+)$/i);
    if (jsonMatch) {
        const payload = jsonMatch[1].trim();
        try {
            const obj = JSON.parse(payload);
            if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
                return { ok: false, error: 'json: body must be a single JSON object.' };
            }
            if (typeof obj.action !== 'string' || !obj.action) {
                return { ok: false, error: 'json: missing non-empty string "action".' };
            }
            const parameters =
                obj.parameters && typeof obj.parameters === 'object' && !Array.isArray(obj.parameters)
                    ? obj.parameters
                    : {};
            const command = {
                action: obj.action,
                target_id: obj.target_id,
                parameters
            };
            return { ok: true, result: { type: 'command', command } };
        } catch (e) {
            return { ok: false, error: `json: ${e.message}` };
        }
    }

    const tokens = tokenizeCommandLine(raw);
    const verb = tokens[0].toLowerCase();

    if (verb === '?' || verb === 'help') {
        if (tokens.length !== 1) {
            return { ok: false, error: 'help does not take arguments.' };
        }
        return { ok: true, result: { type: 'help' } };
    }

    if (verb === 'lasers' || verb === 'laserlines' || verb === 'laser-lines') {
        if (tokens.length !== 1) {
            return { ok: false, error: 'lasers does not take arguments.' };
        }
        return { ok: true, result: { type: 'lasers' } };
    }

    if (verb === 'refresh') {
        if (tokens.length !== 1) {
            return { ok: false, error: 'refresh does not take arguments.' };
        }
        return { ok: true, result: { type: 'refresh' } };
    }

    if (/^(?:simwrite|sim_write)\b/i.test(raw)) {
        const match = raw.match(
            /^(?:simwrite|sim_write)\s+([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:\s+(--overwrite))?\s+([\s\S]+)$/i,
        );
        if (!match) {
            return {
                ok: false,
                error: 'Usage: simwrite <preset_name> [--overwrite] <json_object>',
            };
        }
        try {
            const document = JSON.parse(match[3]);
            if (!document || typeof document !== 'object' || Array.isArray(document)) {
                return { ok: false, error: 'simwrite: JSON body must be an object.' };
            }
            return {
                ok: true,
                result: {
                    type: 'simwrite',
                    name: match[1],
                    overwrite: match[2] === '--overwrite',
                    document,
                },
            };
        } catch (e) {
            return { ok: false, error: `simwrite JSON: ${e.message}` };
        }
    }

    if (/^simcomponent\s+(?:define|configure)\b/i.test(raw)) {
        const match = raw.match(
            /^simcomponent\s+(define|configure)\s+(tag_[1-9][0-9]*)\s+([\s\S]+)$/i,
        );
        if (!match) {
            return {
                ok: false,
                error: 'Usage: simcomponent define|configure <tag_id> <json_object>',
            };
        }
        try {
            const definition = JSON.parse(match[3]);
            if (!definition || typeof definition !== 'object' || Array.isArray(definition)) {
                return { ok: false, error: 'simcomponent JSON body must be an object.' };
            }
            return {
                ok: true,
                result: {
                    type: match[1].toLowerCase() === 'define'
                        ? 'simcomponent-define'
                        : 'simcomponent-configure',
                    tagId: match[2],
                    definition,
                },
            };
        } catch (e) {
            return { ok: false, error: `simcomponent JSON: ${e.message}` };
        }
    }

    if (verb === 'labstate' || verb === 'state') {
        if (tokens.length > 2 || (tokens.length === 2 && tokens[1] !== '--json')) {
            return { ok: false, error: 'Usage: labstate [--json]' };
        }
        return {
            ok: true,
            result: { type: 'labstate', rawJson: tokens[1] === '--json' },
        };
    }

    if (verb === 'tunables' || verb === 'get_tunables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: tunables <tag_id|name>  (alias: get_tunables …)' };
        }
        return { ok: true, result: { type: 'tunables', tagRef: tokens[1] } };
    }

    if (verb === 'measurables' || verb === 'get_measurables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: measurables <tag_id|name>  (alias: get_measurables …)' };
        }
        return { ok: true, result: { type: 'measurables', tagRef: tokens[1] } };
    }

    if (verb === 'record' || verb === 'record_measurables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: record <tag_id|name>  (alias: record_measurables …)' };
        }
        return { ok: true, result: { type: 'record', tagRef: tokens[1] } };
    }

    if (verb === 'simreset') {
        if (tokens.length !== 2) {
            return {
                ok: false,
                error: 'Usage: simreset list|current|default|<preset_name>',
            };
        }
        return {
            ok: true,
            result: { type: 'simreset', selector: tokens[1] },
        };
    }

    if (verb === 'simshow' || verb === 'sim_show') {
        if (tokens.length !== 2) {
            return {
                ok: false,
                error: 'Usage: simshow current|default|<preset_name>',
            };
        }
        return {
            ok: true,
            result: { type: 'simshow', selector: tokens[1] },
        };
    }

    if (verb === 'simsave') {
        if (tokens.length !== 2 && tokens.length !== 3) {
            return {
                ok: false,
                error: 'Usage: simsave <preset_name> [--overwrite]',
            };
        }
        if (tokens.length === 3 && tokens[2] !== '--overwrite') {
            return {
                ok: false,
                error: 'simsave: the only supported option is --overwrite',
            };
        }
        return {
            ok: true,
            result: {
                type: 'simsave',
                name: tokens[1],
                overwrite: tokens[2] === '--overwrite',
            },
        };
    }

    if (verb === 'simcomponent' || verb === 'simcomp') {
        const action = (tokens[1] || '').toLowerCase();
        const taggedActions = ['show', 'reset', 'insert', 'remove', 'delete'];
        if (
            taggedActions.includes(action) &&
            tokens.length >= 3 &&
            !/^tag_[1-9][0-9]*$/.test(tokens[2])
        ) {
            return {
                ok: false,
                error: 'Component tags must be tag_<positive integer>, for example tag_23.',
            };
        }
        if (action === 'list' && tokens.length === 2) {
            return { ok: true, result: { type: 'simcomponent-list' } };
        }
        if (action === 'nexttag' && tokens.length === 2) {
            return { ok: true, result: { type: 'simcomponent-nexttag' } };
        }
        if (action === 'show' && tokens.length === 3) {
            return { ok: true, result: { type: 'simcomponent-show', tagId: tokens[2] } };
        }
        if (action === 'reset' && tokens.length === 3) {
            return { ok: true, result: { type: 'simcomponent-reset', tagId: tokens[2] } };
        }
        if (action === 'remove' && tokens.length === 3) {
            return { ok: true, result: { type: 'simcomponent-remove', tagId: tokens[2] } };
        }
        if (action === 'delete' && tokens.length === 3) {
            return { ok: true, result: { type: 'simcomponent-delete', tagId: tokens[2] } };
        }
        if (action === 'insert' && tokens.length === 6 && tokens[3].toLowerCase() !== 'storage') {
            const x = Number(tokens[3]);
            const y = Number(tokens[4]);
            const rotation = Number(tokens[5]);
            if (![x, y, rotation].every(Number.isFinite)) {
                return { ok: false, error: 'simcomponent insert: x, y and rotation must be finite.' };
            }
            return {
                ok: true,
                result: {
                    type: 'simcomponent-insert',
                    tagId: tokens[2],
                    payload: { x, y, rotation },
                },
            };
        }
        if (action === 'insert' && tokens.length === 6 && tokens[3].toLowerCase() === 'storage') {
            const i = Number(tokens[4]);
            const j = Number(tokens[5]);
            if (!Number.isInteger(i) || !Number.isInteger(j)) {
                return { ok: false, error: 'storage i and j must be integers.' };
            }
            return {
                ok: true,
                result: {
                    type: 'simcomponent-insert',
                    tagId: tokens[2],
                    payload: { storage_slot: { i, j } },
                },
            };
        }
        return {
            ok: false,
            error: 'Usage: simcomponent list|nexttag|show|define|configure|reset|insert|remove|delete ...',
        };
    }

    if (verb === 'simclear') {
        if (tokens.length > 2 || (tokens[1] && !['table', 'all'].includes(tokens[1].toLowerCase()))) {
            return { ok: false, error: 'Usage: simclear [table|all]' };
        }
        return {
            ok: true,
            result: { type: 'simclear', scope: (tokens[1] || 'table').toLowerCase() },
        };
    }

    if (verb === 'move') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: move <tag_id|name> <x_mm> <y_mm> <rotation_deg>' };
        }
        const tagRef = tokens[1];
        const x = Number(tokens[2]);
        const y = Number(tokens[3]);
        const rot = Number(tokens[4]);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(rot)) {
            return { ok: false, error: 'move: x, y, and rotation must be numbers.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'MOVE_COMPONENT',
                    target_ref: tagRef,
                    parameters: {
                        target_x: x,
                        target_y: y,
                        rotation: rot
                    }
                }
            }
        };
    }

    if (verb === 'movelaser' || verb === 'move-laser' || verb === 'stitch') {
        if (tokens.length !== 4 && tokens.length !== 5) {
            return {
                ok: false,
                error: 'Usage: movelaser <tag_id|name> <y_mm> <rotation_deg> [laser_line_id|name]',
            };
        }
        const tagRef = tokens[1];
        const y = Number(tokens[2]);
        const rot = Number(tokens[3]);
        const lineId = tokens.length === 5 ? tokens[4] : null;
        if (!Number.isFinite(y) || !Number.isFinite(rot)) {
            return { ok: false, error: 'movelaser: y and rotation must be numbers.' };
        }
        return {
            ok: true,
            result: {
                type: 'movelaser',
                tagRef,
                y,
                rotation: rot,
                lineId,
            },
        };
    }

    if (verb === 'store' || verb === 'storecell' || verb === 'tostorage') {
        if (tokens.length !== 2 && tokens.length !== 4) {
            return {
                ok: false,
                error: 'Usage: store <tag_id|name>  OR  store <tag_id|name> <slot_i> <slot_j>',
            };
        }
        const tagRef = tokens[1];
        if (tokens.length === 2) {
            return {
                ok: true,
                result: {
                    type: 'command',
                    command: {
                        action: 'STORE_COMPONENT',
                        target_ref: tagRef,
                        parameters: {},
                    },
                },
            };
        }
        const slotI = parseInt(tokens[2], 10);
        const slotJ = parseInt(tokens[3], 10);
        if (!Number.isInteger(slotI) || !Number.isInteger(slotJ)) {
            return { ok: false, error: 'store: slot_i and slot_j must be integers.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'STORE_COMPONENT',
                    target_ref: tagRef,
                    parameters: { slot_i: slotI, slot_j: slotJ },
                },
            },
        };
    }

    if (verb === 'place' || verb === 'fromstorage' || verb === 'drag') {
        if (tokens.length !== 5) {
            return {
                ok: false,
                error: 'Usage: place <tag_id|name> <x_mm> <y_mm> <rotation_deg>',
            };
        }
        const tagRef = tokens[1];
        const x = Number(tokens[2]);
        const y = Number(tokens[3]);
        const rot = Number(tokens[4]);
        if (![x, y, rot].every(Number.isFinite)) {
            return { ok: false, error: 'place: x, y, and rotation must be numbers.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'PLACE_FROM_STORAGE',
                    target_ref: tagRef,
                    parameters: {
                        target_x: x,
                        target_y: y,
                        rotation: rot,
                    },
                },
            },
        };
    }

    if (verb === 'placelaser' || verb === 'place-laser' || verb === 'draglaser') {
        if (tokens.length !== 4 && tokens.length !== 5) {
            return {
                ok: false,
                error: 'Usage: placelaser <tag_id|name> <y_mm> <rotation_deg> [laser_line_id|name]',
            };
        }
        const tagRef = tokens[1];
        const y = Number(tokens[2]);
        const rot = Number(tokens[3]);
        const lineId = tokens.length === 5 ? tokens[4] : null;
        if (!Number.isFinite(y) || !Number.isFinite(rot)) {
            return { ok: false, error: 'placelaser: y and rotation must be numbers.' };
        }
        return {
            ok: true,
            result: {
                type: 'placelaser',
                tagRef,
                y,
                rotation: rot,
                lineId,
            },
        };
    }

    if (verb === 'motor') {
        if (tokens.length !== 4) {
            return { ok: false, error: 'Usage: motor <tag_id|name> <motor_id> <distance>' };
        }
        const tagRef = tokens[1];
        const motorId = parseInt(tokens[2], 10);
        const distance = Number(tokens[3]);
        if (!Number.isInteger(motorId) || !Number.isFinite(distance)) {
            return { ok: false, error: 'motor: motor_id must be an integer, distance a number.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'MOVE_MOTOR',
                    target_ref: tagRef,
                    parameters: {
                        motor_id: motorId,
                        distance
                    }
                }
            }
        };
    }

    if (verb === 'motorhome') {
        if (tokens.length !== 3) {
            return { ok: false, error: 'Usage: motorhome <tag_id|name> <motor_id>' };
        }
        const tagRef = tokens[1];
        const motorId = parseInt(tokens[2], 10);
        if (!Number.isInteger(motorId)) {
            return { ok: false, error: 'motorhome: motor_id must be an integer.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'MOTOR_SEND_HOME',
                    target_ref: tagRef,
                    parameters: { motor_id: motorId }
                }
            }
        };
    }

    if (verb === 'motorset0') {
        if (tokens.length !== 3) {
            return { ok: false, error: 'Usage: motorset0 <tag_id|name> <motor_id>' };
        }
        const tagRef = tokens[1];
        const motorId = parseInt(tokens[2], 10);
        if (!Number.isInteger(motorId)) {
            return { ok: false, error: 'motorset0: motor_id must be an integer.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'MOTOR_SET_ZERO',
                    target_ref: tagRef,
                    parameters: { motor_id: motorId }
                }
            }
        };
    }

    if (verb === 'pick') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: pick <tag_id|name>' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'PICK_COMPONENT',
                    target_ref: tokens[1],
                    parameters: {},
                },
            },
        };
    }

    if (verb === 'hover') {
        if (tokens.length !== 6) {
            return { ok: false, error: 'Usage: hover <tag_id|name> <x_mm> <y_mm> <rotation_deg> <z_mm>' };
        }
        const tagRef = tokens[1];
        const x = Number(tokens[2]);
        const y = Number(tokens[3]);
        const rot = Number(tokens[4]);
        const z = Number(tokens[5]);
        if (![x, y, rot, z].every(Number.isFinite)) {
            return { ok: false, error: 'hover: x, y, rotation, and z must be numbers.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'HOVER',
                    target_ref: tagRef,
                    parameters: { target_x: x, target_y: y, rotation: rot, z },
                },
            },
        };
    }

    if (verb === 'placehover' || verb === 'place-from-hover' || verb === 'placefromhover') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: placehover <tag_id|name> <x_mm> <y_mm> <rotation_deg>' };
        }
        const tagRef = tokens[1];
        const x = Number(tokens[2]);
        const y = Number(tokens[3]);
        const rot = Number(tokens[4]);
        if (![x, y, rot].every(Number.isFinite)) {
            return { ok: false, error: 'placehover: x, y, rotation must be numbers.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'PLACE_FROM_HOVER',
                    target_ref: tagRef,
                    parameters: { target_x: x, target_y: y, rotation: rot },
                },
            },
        };
    }

    if (verb === 'confirmhold' || verb === 'confirm-holding-tag' || verb === 'confirm_holding') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: confirmhold <tag_id|name>' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'CONFIRM_HOLDING_TAG',
                    target_ref: tokens[1],
                    parameters: {},
                },
            },
        };
    }

    return { ok: false, error: `Unknown command "${tokens[0]}". Type help.` };
}

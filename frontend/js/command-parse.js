/**
 * Shorthand parser for the Command Console.
 * @see coding_on_the_ui.md
 */

/**
 * @returns {string}
 */
export function formatHelp() {
    return [
        'Commands (whitespace-separated):',
        '  help | ?          — this text',
        '  refresh           — refresh poses from camera (same as Refresh Pose button)',
        '  tunables <tag>    — print tunables JSON for tag (alias: get_tunables)',
        '  measurables <tag> — print measurables JSON for tag (alias: get_measurables; saved state only)',
        '  record <tag>      — RECORD_MEASURABLES (poll lab; e.g. camera → measurables.camera_image)',
        '  move <tag> <x> <y> <rot>     — MOVE_COMPONENT (lab mm, degrees)',
        '  motor <tag> <motor_id> <dist> — MOVE_MOTOR',
        '  motorhome <tag> <motor_id>   — MOTOR_SEND_HOME (move by −θ → tracked 0)',
        '  motorset0 <tag> <motor_id>   — MOTOR_SET_ZERO (current pos → θ=0, no move)',
        '',
        'In-air manipulation (see new_primitives.md):',
        '  pick <tag>                          — PICK_COMPONENT (grasp + lift → HOLDING)',
        '  hover <tag> <x> <y> <rot> <z>       — HOVER (re-pose held part in air)',
        '  placehover <tag> <x> <y> <rot>      — PLACE_FROM_HOVER (held → on table)',
        '  confirmhold <tag>                   — CONFIRM_HOLDING_TAG (clear unconfirmed flag)',
        '',
        '  json <object>       — raw POST /api/command body (single-line JSON)',
        '',
        'Tab cycles completions for the current word (commands, tag ids).',
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

    const tokens = raw.split(/\s+/).filter(Boolean);
    const verb = tokens[0].toLowerCase();

    if (verb === '?' || verb === 'help') {
        if (tokens.length !== 1) {
            return { ok: false, error: 'help does not take arguments.' };
        }
        return { ok: true, result: { type: 'help' } };
    }

    if (verb === 'refresh') {
        if (tokens.length !== 1) {
            return { ok: false, error: 'refresh does not take arguments.' };
        }
        return { ok: true, result: { type: 'refresh' } };
    }

    if (verb === 'tunables' || verb === 'get_tunables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: tunables <tag_id>  (alias: get_tunables <tag_id>)' };
        }
        return { ok: true, result: { type: 'tunables', tagId: tokens[1] } };
    }

    if (verb === 'measurables' || verb === 'get_measurables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: measurables <tag_id>  (alias: get_measurables <tag_id>)' };
        }
        return { ok: true, result: { type: 'measurables', tagId: tokens[1] } };
    }

    if (verb === 'record' || verb === 'record_measurables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: record <tag_id>  (alias: record_measurables <tag_id>)' };
        }
        return { ok: true, result: { type: 'record', tagId: tokens[1] } };
    }

    if (verb === 'move') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: move <tag_id> <x_mm> <y_mm> <rotation_deg>' };
        }
        const tag = tokens[1];
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
                    target_id: tag,
                    parameters: {
                        target_x: x,
                        target_y: y,
                        rotation: rot
                    }
                }
            }
        };
    }

    if (verb === 'motor') {
        if (tokens.length !== 4) {
            return { ok: false, error: 'Usage: motor <tag_id> <motor_id> <distance>' };
        }
        const tag = tokens[1];
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
                    target_id: tag,
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
            return { ok: false, error: 'Usage: motorhome <tag_id> <motor_id>' };
        }
        const tag = tokens[1];
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
                    target_id: tag,
                    parameters: { motor_id: motorId }
                }
            }
        };
    }

    if (verb === 'motorset0') {
        if (tokens.length !== 3) {
            return { ok: false, error: 'Usage: motorset0 <tag_id> <motor_id>' };
        }
        const tag = tokens[1];
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
                    target_id: tag,
                    parameters: { motor_id: motorId }
                }
            }
        };
    }

    if (verb === 'pick') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: pick <tag_id>' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'PICK_COMPONENT',
                    target_id: tokens[1],
                    parameters: {},
                },
            },
        };
    }

    if (verb === 'hover') {
        if (tokens.length !== 6) {
            return { ok: false, error: 'Usage: hover <tag_id> <x_mm> <y_mm> <rotation_deg> <z_mm>' };
        }
        const tag = tokens[1];
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
                    target_id: tag,
                    parameters: { target_x: x, target_y: y, rotation: rot, z },
                },
            },
        };
    }

    if (verb === 'placehover' || verb === 'place-from-hover' || verb === 'placefromhover') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: placehover <tag_id> <x_mm> <y_mm> <rotation_deg>' };
        }
        const tag = tokens[1];
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
                    target_id: tag,
                    parameters: { target_x: x, target_y: y, rotation: rot },
                },
            },
        };
    }

    if (verb === 'confirmhold' || verb === 'confirm-holding-tag' || verb === 'confirm_holding') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: confirmhold <tag_id>' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'CONFIRM_HOLDING_TAG',
                    target_id: tokens[1],
                    parameters: {},
                },
            },
        };
    }

    return { ok: false, error: `Unknown command "${tokens[0]}". Type help.` };
}

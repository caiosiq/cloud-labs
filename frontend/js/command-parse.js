/**
 * Shorthand parser for the Command Console.
 * Defaults for NEWTON/COBYLA must stay aligned with `fetchStrategies()` in js/app-main.js.
 * Console COBYLA / NEWTON may omit `exposure`; `command-api.js` fills it from camera exposure helper when present.
 * @see coding_on_the_ui.md
 */

/** @type {Record<string, { camera_number: number, target_x_pixel: number, axis: string, tolerance_ratio: number }>} */
export const NEWTON_DEFAULTS = {
    camera_number: 2,
    target_x_pixel: 2744,
    axis: 'x',
    tolerance_ratio: 0.1
};

export const COBYLA_DEFAULT_OBJECTIVE = 100.0;

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
        '  optimize <tag> NEWTON [cam] [px] [axis] [tol]',
        '                      — DEPRECATED legacy OPTIMIZE NEWTON (prefer Alignment session)',
        '  optimize <tag> COBYLA [threshold]',
        '                      — DEPRECATED legacy OPTIMIZE COBYLA (prefer run_cobyla / ensemble)',
        '',
        'In-air manipulation (see new_primitives.md):',
        '  pick <tag>                          — PICK_COMPONENT (grasp + lift → HOLDING)',
        '  hover <tag> <x> <y> <rot> <z>       — HOVER (re-pose held part in air)',
        '  placehover <tag> <x> <y> <rot>      — PLACE_FROM_HOVER (held → on table)',
        '  scanrotate <tag> <theta_min> <theta_max> <speed>',
        '                                      — SCAN_ROTATE_IN_PLACE around z',
        '  confirmhold <tag>                   — CONFIRM_HOLDING_TAG (clear unconfirmed flag)',
        '',
        '  json <object>       — raw POST /api/command body (single-line JSON)',
        '',
        'Tab cycles completions for the current word (commands, tag ids, NEWTON/COBYLA, defaults).',
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

    if (verb === 'optimize') {
        if (tokens.length < 3) {
            return { ok: false, error: 'Usage: optimize <tag_id> NEWTON|COBYLA …' };
        }
        const tag = tokens[1];
        const strategy = tokens[2].toUpperCase();

        if (strategy === 'NEWTON') {
            const opt = tokens.slice(3);
            if (opt.length !== 0 && opt.length !== 4) {
                return {
                    ok: false,
                    error: 'NEWTON: use defaults, or exactly 4 args: camera_number target_x_pixel axis(x|y) tolerance_ratio'
                };
            }
            let camera_number = NEWTON_DEFAULTS.camera_number;
            let target_x_pixel = NEWTON_DEFAULTS.target_x_pixel;
            let axis = NEWTON_DEFAULTS.axis;
            let tolerance_ratio = NEWTON_DEFAULTS.tolerance_ratio;
            if (opt.length === 4) {
                camera_number = parseInt(opt[0], 10);
                target_x_pixel = parseInt(opt[1], 10);
                axis = String(opt[2]).toLowerCase();
                tolerance_ratio = Number(opt[3]);
                if (!Number.isInteger(camera_number) || !Number.isInteger(target_x_pixel)) {
                    return { ok: false, error: 'NEWTON: camera_number and target_x_pixel must be integers.' };
                }
                if (axis !== 'x' && axis !== 'y') {
                    return { ok: false, error: 'NEWTON: axis must be x or y.' };
                }
                if (!Number.isFinite(tolerance_ratio)) {
                    return { ok: false, error: 'NEWTON: tolerance_ratio must be a number.' };
                }
            }
            return {
                ok: true,
                result: {
                    type: 'command',
                    command: {
                        action: 'OPTIMIZE',
                        target_id: tag,
                        parameters: {
                            strategy: 'NEWTON',
                            camera_number,
                            target_x_pixel,
                            axis,
                            tolerance_ratio
                        }
                    }
                }
            };
        }

        if (strategy === 'COBYLA') {
            const rest = tokens.slice(3);
            if (rest.length > 1) {
                return { ok: false, error: 'COBYLA: optional single arg: objective_threshold' };
            }
            let objective_threshold = COBYLA_DEFAULT_OBJECTIVE;
            if (rest.length === 1) {
                objective_threshold = Number(rest[0]);
                if (!Number.isFinite(objective_threshold)) {
                    return { ok: false, error: 'COBYLA: objective_threshold must be a number.' };
                }
            }
            return {
                ok: true,
                result: {
                    type: 'command',
                    command: {
                        action: 'OPTIMIZE',
                        target_id: tag,
                        parameters: {
                            strategy: 'COBYLA',
                            objective_threshold
                        }
                    }
                }
            };
        }

        return { ok: false, error: `Unknown strategy "${tokens[2]}". Use NEWTON or COBYLA.` };
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

    if (verb === 'scanrotate' || verb === 'scan-rotate' || verb === 'scan_rotate') {
        if (tokens.length !== 5) {
            return {
                ok: false,
                error: 'Usage: scanrotate <tag_id> <theta_min_deg> <theta_max_deg> <speed_deg_per_s>',
            };
        }
        const tag = tokens[1];
        const theta_min = Number(tokens[2]);
        const theta_max = Number(tokens[3]);
        const speed = Number(tokens[4]);
        if (![theta_min, theta_max, speed].every(Number.isFinite)) {
            return { ok: false, error: 'scanrotate: theta_min, theta_max, speed must be numbers.' };
        }
        if (!(speed > 0)) {
            return { ok: false, error: 'scanrotate: speed_deg_per_s must be > 0.' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'SCAN_ROTATE_IN_PLACE',
                    target_id: tag,
                    parameters: { theta_min, theta_max, speed_deg_per_s: speed, axis: 'z' },
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

/**
 * Shorthand parser for the Command Console.
 * Defaults for NEWTON/COBYLA must stay aligned with `fetchStrategies()` in app.js.
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
        '  refresh           — rescan lab state (same as Refresh State button)',
        '  move <tag> <x> <y> <rot>     — MOVE_COMPONENT (lab mm, degrees)',
        '  motor <tag> <motor_id> <dist> — MOVE_MOTOR',
        '  optimize <tag> NEWTON [cam] [px] [axis] [tol]',
        '                      — OPTIMIZE NEWTON (0 or 4 optional args)',
        '  optimize <tag> COBYLA [threshold]',
        '                      — OPTIMIZE COBYLA (needs motor_ids in catalog)',
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

    return { ok: false, error: `Unknown command "${tokens[0]}". Type help.` };
}

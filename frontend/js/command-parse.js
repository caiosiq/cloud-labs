/**
 * Shorthand parser for the Command Console.
 * @see command-grammar.js — verb list aligned with lab_model primitive registry.
 */

import { formatConsoleHelp, normalizeVerb } from './command-grammar.js';

export { formatConsoleHelp as formatHelp };

function cmd(action, target_id, parameters = {}) {
    return { ok: true, result: { type: 'command', command: { action, target_id, parameters } } };
}

function parseNums(tokens, start, count) {
    const vals = tokens.slice(start, start + count).map(Number);
    if (vals.some((n) => !Number.isFinite(n))) return null;
    return vals;
}

function parsePoseArgs(tokens, start) {
    const nums = parseNums(tokens, start, 3);
    if (!nums) return null;
    return { target_x: nums[0], target_y: nums[1], rotation: nums[2] };
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
            return {
                ok: true,
                result: {
                    type: 'command',
                    command: {
                        action: obj.action,
                        target_id: obj.target_id,
                        parameters,
                        ...(obj.channel != null ? { channel: obj.channel } : {}),
                    },
                },
            };
        } catch (e) {
            return { ok: false, error: `json: ${e.message}` };
        }
    }

    const tokens = raw.split(/\s+/).filter(Boolean);
    const verb = normalizeVerb(tokens[0]);

    if (verb === '?' || verb === 'help') {
        if (tokens.length !== 1) return { ok: false, error: 'help does not take arguments.' };
        return { ok: true, result: { type: 'help' } };
    }

    if (verb === 'refresh') {
        if (tokens.length !== 1) return { ok: false, error: 'refresh does not take arguments.' };
        return { ok: true, result: { type: 'refresh' } };
    }

    if (verb === 'tunables' || verb === 'gettunables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: tunables <tag_id>  (alias: get_tunables <tag_id>)' };
        }
        return { ok: true, result: { type: 'tunables', tagId: tokens[1] } };
    }

    if (verb === 'measurables' || verb === 'getmeasurables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: measurables <tag_id>  (alias: get_measurables <tag_id>)' };
        }
        return { ok: true, result: { type: 'measurables', tagId: tokens[1] } };
    }

    if (verb === 'getstorage') {
        if (tokens.length !== 1) {
            return { ok: false, error: 'Usage: get_storage  (alias: getstorage; no arguments)' };
        }
        return { ok: true, result: { type: 'storage' } };
    }

    if (verb === 'record' || verb === 'recordmeasurables') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: record <tag_id>  (alias: record_measurables <tag_id>)' };
        }
        return { ok: true, result: { type: 'record', tagId: tokens[1] } };
    }

    if (verb === 'move') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: move <tag_id> <x_mm> <y_mm> <rotation_deg>' };
        }
        const pose = parsePoseArgs(tokens, 2);
        if (!pose) return { ok: false, error: 'move: x, y, and rotation must be numbers.' };
        return cmd('MOVE_COMPONENT', tokens[1], pose);
    }

    if (verb === 'store') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: store <tag_id>' };
        return cmd('STORE_COMPONENT', tokens[1]);
    }

    if (verb === 'placefromstorage' || verb === 'placefromstore') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: placefromstorage <tag_id> <x_mm> <y_mm> <rotation_deg>' };
        }
        const pose = parsePoseArgs(tokens, 2);
        if (!pose) return { ok: false, error: 'placefromstorage: x, y, rotation must be numbers.' };
        return cmd('PLACE_FROM_STORAGE', tokens[1], pose);
    }

    if (verb === 'repack') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: repack <tag_id>' };
        return cmd('REPACK_STORAGE', tokens[1]);
    }

    if (verb === 'recenter') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: recenter <tag_id>' };
        return cmd('RECENTER_IN_STORAGE', tokens[1]);
    }

    if (verb === 'affirm') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: affirm <tag_id>' };
        return cmd('AFFIRM_PLACED_AT_CURRENT', tokens[1]);
    }

    if (verb === 'setexposure') {
        if (tokens.length !== 3) return { ok: false, error: 'Usage: setexposure <tag_id> <exposure_ms>' };
        const ms = Number(tokens[2]);
        if (!Number.isFinite(ms) || ms <= 0) {
            return { ok: false, error: 'setexposure: exposure_ms must be a positive number.' };
        }
        return cmd('SET_EXPOSURE', tokens[1], { exposure_time_ms: ms });
    }

    if (verb === 'setmotor') {
        if (tokens.length !== 4) {
            return { ok: false, error: 'Usage: setmotor <tag_id> <motor_id> <angle_deg>' };
        }
        const motorId = parseInt(tokens[2], 10);
        const angle = Number(tokens[3]);
        if (!Number.isInteger(motorId) || !Number.isFinite(angle)) {
            return { ok: false, error: 'setmotor: motor_id must be an integer, angle_deg a number.' };
        }
        return cmd('SET_MOTOR_SETPOINT', tokens[1], { motor_id: motorId, angle_deg: angle });
    }

    if (verb === 'motor') {
        if (tokens.length !== 4) {
            return { ok: false, error: 'Usage: motor <tag_id> <motor_id> <distance_deg>' };
        }
        const motorId = parseInt(tokens[2], 10);
        const distance = Number(tokens[3]);
        if (!Number.isInteger(motorId) || !Number.isFinite(distance)) {
            return { ok: false, error: 'motor: motor_id must be an integer, distance a number.' };
        }
        return cmd('MOVE_MOTOR', tokens[1], { motor_id: motorId, distance });
    }

    if (verb === 'motorhome') {
        if (tokens.length !== 3) return { ok: false, error: 'Usage: motorhome <tag_id> <motor_id>' };
        const motorId = parseInt(tokens[2], 10);
        if (!Number.isInteger(motorId)) {
            return { ok: false, error: 'motorhome: motor_id must be an integer.' };
        }
        return cmd('MOTOR_SEND_HOME', tokens[1], { motor_id: motorId });
    }

    if (verb === 'motorset0') {
        if (tokens.length !== 3) return { ok: false, error: 'Usage: motorset0 <tag_id> <motor_id>' };
        const motorId = parseInt(tokens[2], 10);
        if (!Number.isInteger(motorId)) {
            return { ok: false, error: 'motorset0: motor_id must be an integer.' };
        }
        return cmd('MOTOR_SET_ZERO', tokens[1], { motor_id: motorId });
    }

    if (verb === 'setcobylareference') {
        if (tokens.length !== 2) {
            return { ok: false, error: 'Usage: set_cobyla_reference <tag_id>' };
        }
        return cmd('SET_COBYLA_REFERENCE', tokens[1]);
    }

    if (verb === 'optimize') {
        if (tokens.length < 3) {
            return { ok: false, error: 'Usage: optimize <tag_id> NEWTON|COBYLA [sensor_tag] [loss_metric]' };
        }
        const tag = tokens[1];
        const strategy = tokens[2].toUpperCase();
        if (strategy !== 'NEWTON' && strategy !== 'COBYLA') {
            return { ok: false, error: `Unknown strategy "${tokens[2]}". Use NEWTON or COBYLA.` };
        }
        const rest = tokens.slice(3);
        if (rest.length > 2) {
            return { ok: false, error: 'optimize: at most 2 optional args: sensor_tag loss_metric' };
        }
        const parameters = { strategy };
        if (rest[0]) parameters.sensor_component = rest[0];
        if (rest[1]) parameters.loss_metric = rest[1];
        return cmd('OPTIMIZE', tag, parameters);
    }

    if (verb === 'pick') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: pick <tag_id>' };
        return cmd('PICK_COMPONENT', tokens[1]);
    }

    if (verb === 'hover') {
        if (tokens.length !== 6) {
            return { ok: false, error: 'Usage: hover <tag_id> <x_mm> <y_mm> <rotation_deg> <z_mm>' };
        }
        const nums = parseNums(tokens, 2, 4);
        if (!nums) return { ok: false, error: 'hover: x, y, rotation, and z must be numbers.' };
        return cmd('HOVER', tokens[1], {
            target_x: nums[0],
            target_y: nums[1],
            rotation: nums[2],
            z: nums[3],
        });
    }

    if (verb === 'placehover' || verb === 'placefromhover') {
        if (tokens.length !== 5) {
            return { ok: false, error: 'Usage: placehover <tag_id> <x_mm> <y_mm> <rotation_deg>' };
        }
        const pose = parsePoseArgs(tokens, 2);
        if (!pose) return { ok: false, error: 'placehover: x, y, rotation must be numbers.' };
        return cmd('PLACE_FROM_HOVER', tokens[1], pose);
    }

    if (verb === 'scanrotate') {
        if (tokens.length !== 5) {
            return {
                ok: false,
                error: 'Usage: scanrotate <tag_id> <theta_min_deg> <theta_max_deg> <speed_deg_per_s>',
            };
        }
        const nums = parseNums(tokens, 2, 3);
        if (!nums) {
            return { ok: false, error: 'scanrotate: theta_min, theta_max, speed must be numbers.' };
        }
        if (!(nums[2] > 0)) {
            return { ok: false, error: 'scanrotate: speed_deg_per_s must be > 0.' };
        }
        return cmd('SCAN_ROTATE_IN_PLACE', tokens[1], {
            theta_min: nums[0],
            theta_max: nums[1],
            speed_deg_per_s: nums[2],
            axis: 'z',
        });
    }

    if (verb === 'confirmhold' || verb === 'confirmholdingtag') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: confirmhold <tag_id>' };
        return cmd('CONFIRM_HOLDING_TAG', tokens[1]);
    }

    if (verb === 'startteleop') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: startteleop <tag_id>' };
        return cmd('START_TELEOP', tokens[1]);
    }

    if (verb === 'endteleop') {
        if (tokens.length !== 2) return { ok: false, error: 'Usage: endteleop <tag_id>' };
        return cmd('END_TELEOP', tokens[1]);
    }

    if (verb === 'startlivefeed') {
        if (tokens.length < 2 || tokens.length > 3) {
            return { ok: false, error: 'Usage: startlivefeed <tag_id> [channel]' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'START_LIVE_FEED',
                    target_id: tokens[1],
                    channel: tokens[2] || 'stream',
                    parameters: {},
                },
            },
        };
    }

    if (verb === 'endlivefeed') {
        if (tokens.length < 2 || tokens.length > 3) {
            return { ok: false, error: 'Usage: endlivefeed <tag_id> [channel]' };
        }
        return {
            ok: true,
            result: {
                type: 'command',
                command: {
                    action: 'END_LIVE_FEED',
                    target_id: tokens[1],
                    channel: tokens[2] || 'all',
                    parameters: {},
                },
            },
        };
    }

    if (verb === 'teleopgoto') {
        if (tokens.length < 4 || tokens.length > 6) {
            return { ok: false, error: 'Usage: teleopgoto <tag_id> <x> <y> [rot] [z]' };
        }
        const x = Number(tokens[2]);
        const y = Number(tokens[3]);
        const rot = tokens.length >= 5 ? Number(tokens[4]) : 0;
        const z = tokens.length >= 6 ? Number(tokens[5]) : undefined;
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(rot)) {
            return { ok: false, error: 'teleopgoto: x, y, and rot must be numbers.' };
        }
        if (z !== undefined && !Number.isFinite(z)) {
            return { ok: false, error: 'teleopgoto: z must be a number.' };
        }
        const target_pose = { x, y, rotation: rot };
        if (z !== undefined) target_pose.z = z;
        return cmd('TELEOP_GOTO', tokens[1], { target_pose });
    }

    return { ok: false, error: `Unknown command "${tokens[0]}". Type help.` };
}

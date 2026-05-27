/**
 * Command Console verb list + help text (aligned with lab_model primitive registry).
 */

/** Console verbs (first token). Aliases share the same handler in command-parse.js. */
export const CONSOLE_VERBS = [
    '?',
    'help',
    'refresh',
    'tunables',
    'get_tunables',
    'measurables',
    'get_measurables',
    'getstorage',
    'get_storage',
    'record',
    'record_measurables',
    'move',
    'store',
    'placefromstorage',
    'placefromstore',
    'repack',
    'recenter',
    'affirm',
    'setexposure',
    'setmotor',
    'motor',
    'motorhome',
    'motorset0',
    'set_cobyla_reference',
    'setcobylareference',
    'optimize',
    'pick',
    'hover',
    'placehover',
    'placefromhover',
    'scanrotate',
    'confirmhold',
    'startteleop',
    'endteleop',
    'startlivefeed',
    'endlivefeed',
    'teleopgoto',
    'json',
];

export const OPTIMIZE_STRATEGIES = ['NEWTON', 'COBYLA'];

export const LOSS_METRICS_BY_STRATEGY = {
    NEWTON: ['centroid_match'],
    COBYLA: ['reference_match'],
};

export function formatConsoleHelp() {
    return [
        'Commands (whitespace-separated):',
        '  help | ?          — this text',
        '  refresh           — refresh poses from camera (Refresh Pose button)',
        '  tunables <tag>    — GET tunables JSON (alias: get_tunables)',
        '  measurables <tag> — GET measurables JSON (alias: get_measurables)',
        '  get_storage         — GET_STORAGE (tag ids currently in inventory)',
        '  record <tag>      — RECORD_MEASURABLES',
        '',
        'Table / storage:',
        '  move <tag> <x> <y> <rot>              — MOVE_COMPONENT',
        '  store <tag>                           — STORE_COMPONENT',
        '  placefromstorage <tag> <x> <y> <rot>  — PLACE_FROM_STORAGE',
        '  repack <tag>                          — REPACK_STORAGE',
        '  recenter <tag>                        — RECENTER_IN_STORAGE',
        '  affirm <tag>                          — AFFIRM_PLACED_AT_CURRENT',
        '',
        'Tunables / motors:',
        '  setexposure <tag> <ms>                — SET_EXPOSURE',
        '  setmotor <tag> <motor_id> <angle°>    — SET_MOTOR_SETPOINT',
        '  motor <tag> <motor_id> <distance°>    — MOVE_MOTOR (jog)',
        '  motorhome <tag> <motor_id>            — MOTOR_SEND_HOME',
        '  motorset0 <tag> <motor_id>            — MOTOR_SET_ZERO',
        '',
        'Optimization:',
        '  set_cobyla_reference <tag>           — SET_COBYLA_REFERENCE (pin camera_image)',
        '  optimize <tag> NEWTON|COBYLA [sensor] [loss_metric]',
        '                      — OPTIMIZE (sensor tag e.g. tag_22; metrics per strategy)',
        '',
        'In-air manipulation:',
        '  pick <tag>                          — PICK_COMPONENT',
        '  hover <tag> <x> <y> <rot> <z>       — HOVER',
        '  placehover <tag> <x> <y> <rot>      — PLACE_FROM_HOVER',
        '  scanrotate <tag> <θmin> <θmax> <deg/s> — SCAN_ROTATE_IN_PLACE',
        '  confirmhold <tag>                   — CONFIRM_HOLDING_TAG',
        '',
        'TeleOp / live feed:',
        '  startteleop <tag>                   — START_TELEOP',
        '  endteleop <tag>                     — END_TELEOP',
        '  teleopgoto <tag> <x> <y> [rot] [z]  — TELEOP_GOTO',
        '  startlivefeed <tag> [channel]       — START_LIVE_FEED (default channel: stream)',
        '  endlivefeed <tag> [channel]           — END_LIVE_FEED (default channel: all)',
        '',
        '  json <object>       — raw POST /api/command body (single-line JSON)',
        '',
        'Tab completes verbs, tag ids, strategies, sensors, and loss metrics.',
        '',
    ].join('\n');
}

/** @param {string} verb */
export function normalizeVerb(verb) {
    return String(verb || '').toLowerCase().replace(/[-_]/g, '');
}

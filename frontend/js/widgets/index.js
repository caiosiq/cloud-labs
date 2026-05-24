/**
 * Phase 7 widget registry — single source of truth for the closed widget
 * vocabulary listed in universal_component_architecture.md §14.
 *
 * Every catalog entry's ``capabilities.{tunables,measurables,telemetry}.<field>``
 * declares a ``widget`` string. The symmetric component viewer
 * (``frontend/js/ui/component-viewer.js``) calls {@link getWidget} for each
 * declared field and mounts the returned ``HTMLElement``. Unknown widget
 * names fall back to {@link import('./json-inspector.js').default} per
 * §15.2 (soft fallback) and emit a one-shot console warning so the
 * operator sees the catalog drift without the page breaking.
 *
 * ## Widget factory contract
 *
 * Each widget is a function:
 *
 * ```js
 * function widget(ctx: {
 *   tagId: string,
 *   fieldName: string,
 *   descriptor: object,      // catalog descriptor (widget, min, max, format, ...)
 *   value: any,              // current value from state (may be null!)
 *   scope: 'tunables' | 'measurables' | 'telemetry',
 *   comp: object | undefined,// full lab-state entry for the tag
 *   hooks: {                 // platform escape hatches (no static imports inside widgets)
 *     sendCommand?: (cmd) => Promise<any>,
 *     fetchLabState?: () => Promise<void>,
 *     log?: (msg, level) => void,
 *   },
 * }) -> HTMLElement
 * ```
 *
 * Widgets are pure builders: each call returns a fresh DOM node. Callers
 * are expected to re-render the whole component panel whenever lab state
 * changes (cheaper than diffing, simpler to reason about).
 */
import TablePose from './table-pose.js';
import NudgeMotorGroup from './nudge-motor-group.js';
import FloatRange from './float-range.js';
import TeleopRz from './teleop-rz.js';
import TeleopPose3d from './teleop-pose3d.js';
import MotorRotationsReadout from './motor-rotations-readout.js';
import PoseReadout from './pose-readout.js';
import StorageSlot from './storage-slot.js';
import NumberBadge from './number-badge.js';
import ImageViewer from './image-viewer.js';
import MJPEGViewer from './mjpeg-viewer.js';
import JPEGPoll from './jpeg-poll.js';
import LivePosePoll from './live-pose-poll.js';
import JsonInspector from './json-inspector.js';

const REGISTRY = Object.freeze({
    // Tunable widgets (§14.1).
    TablePose,
    NudgeMotorGroup,
    FloatRange,
    StorageSlot,
    // TeleOp v2: per-channel widgets (Rz / Pose3d) in telemetry.teleop.
    TeleopRz,
    TeleopPose3d,

    // Measurable widgets (§14.2) -- present subset.
    MotorRotationsReadout,
    PoseReadout,
    NumberBadge,
    ImageViewer,

    // Telemetry widgets (§14.3).
    MJPEGViewer,
    JPEGPoll,
    LivePosePoll,

    // Universal fallback (§14.4). Never declared in the catalog; the
    // soft-fallback path swaps it in for unknown widget names.
    JsonInspector,
});

const _warnedOnce = new Set();

/**
 * Resolve a widget by name. Always returns a factory — falls back to
 * ``JsonInspector`` for unknown names (§15.2) and warns once per name
 * so noisy catalogs don't spam the console.
 *
 * @param {string} name  widget name from catalog
 * @returns {Function} widget factory
 */
export function getWidget(name) {
    if (typeof name !== 'string' || !name) {
        return REGISTRY.JsonInspector;
    }
    if (Object.prototype.hasOwnProperty.call(REGISTRY, name)) {
        return REGISTRY[name];
    }
    if (!_warnedOnce.has(name)) {
        _warnedOnce.add(name);
        console.warn(
            `[widget-registry] Unknown widget ${JSON.stringify(name)} — falling back to JsonInspector. ` +
            `Either ship a widget at frontend/js/widgets/<name>.js or remove the catalog reference.`,
        );
    }
    return REGISTRY.JsonInspector;
}

/** Used by tests / dev tools. */
export function knownWidgets() {
    return Object.keys(REGISTRY);
}

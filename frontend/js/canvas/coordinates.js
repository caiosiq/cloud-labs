import {
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    LAB_SCALE,
    LAB_CENTER_PX,
} from '../config.js';

/** Lab mm → canvas pixels (origin at center, +Y lab = up on screen). */
export function mmToPx(labX, labY) {
    return {
        x: LAB_CENTER_PX.x + labX * LAB_SCALE,
        y: LAB_CENTER_PX.y - labY * LAB_SCALE,
    };
}

/** Canvas pixels → lab mm. */
export function pxToMm(px, py) {
    return {
        x: (px - LAB_CENTER_PX.x) / LAB_SCALE,
        y: (LAB_CENTER_PX.y - py) / LAB_SCALE,
    };
}

export { CANVAS_WIDTH, CANVAS_HEIGHT, LAB_SCALE, LAB_CENTER_PX };

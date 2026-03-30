/** Canvas and lab geometry (mm ↔ px mapping inputs). */
export const CANVAS_WIDTH = 1000;
export const CANVAS_HEIGHT = 700;

export const LAB_X_MIN = -500;
export const LAB_X_MAX = 500;
export const LAB_Y_MIN = -500;
export const LAB_Y_MAX = 500;

export const LAB_WIDTH_MM = LAB_X_MAX - LAB_X_MIN;
export const LAB_HEIGHT_MM = LAB_Y_MAX - LAB_Y_MIN;
export const LAB_SCALE = Math.min(CANVAS_WIDTH / LAB_WIDTH_MM, CANVAS_HEIGHT / LAB_HEIGHT_MM);
export const LAB_CENTER_PX = { x: CANVAS_WIDTH / 2, y: CANVAS_HEIGHT / 2 };

export const QUARTER_INCH_MM = 25.4 / 4;
export const BREADBOARD_GRID_FINE_TUNE_X_MM = -2.05;
export const BREADBOARD_GRID_OFFSET_X_MM = -QUARTER_INCH_MM + BREADBOARD_GRID_FINE_TUNE_X_MM;

export const POLLING_INTERVAL = 500;

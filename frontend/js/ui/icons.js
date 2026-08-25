/**
 * Material Icons names for component types.
 * Shared between the sidebar component list and recipe-requirement modal.
 */
export function getComponentIcon(type) {
    switch (type) {
        case 'OPTICAL_MIRROR':
            return 'crop_portrait';
        case 'OPTICAL_LENS':
            return 'lens';
        case 'OPTICAL_BEAMSPLITTER':
            return 'dashboard';
        case 'OPTICAL_CAMERA':
            return 'videocam';
        case 'OPTICAL_FILTER':
            return 'filter_frames';
        case 'OPTICAL_POLARIZER':
            return 'tonality';
        case 'LASER_SOURCE':
            return 'flare';
        case 'OPTICAL_CRYSTAL':
            return 'diamond';
        case 'OPTICAL_BEAM_BLOCK':
        case 'OPTICAL_BEAMBLOCKER':
            return 'block';
        default:
            return 'help_outline';
    }
}

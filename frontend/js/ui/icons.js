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
        default:
            return 'help_outline';
    }
}

/**
 * Lossless, compact Command Console rendering for the complete Tier-C lab state.
 *
 * The default report emits one minified JSON line per component plus one line
 * containing every non-component top-level field. This keeps the report easy
 * for a person or agent to scan without silently dropping state fields.
 */

function json(value) {
    return JSON.stringify(value ?? null);
}

/**
 * @param {object} state
 * @param {Record<string, object>} catalogMap
 * @returns {string[]}
 */
export function formatCompleteLabStateReport(state, catalogMap = {}) {
    const labState = state && typeof state === 'object' ? state : {};
    const components =
        labState.components && typeof labState.components === 'object'
            ? labState.components
            : {};
    const tags = Object.keys(components).sort((a, b) =>
        a.localeCompare(b, undefined, { numeric: true }),
    );
    const holdingTag = labState?.holding?.tag_id || 'none';
    const lines = [
        `LABSTATE status=${labState.system_status || 'UNKNOWN'} components=${tags.length} holding=${holdingTag}`,
    ];

    tags.forEach((tagId) => {
        const component = components[tagId];
        const catalogName = catalogMap?.[tagId]?.name || null;
        const complete = component && typeof component === 'object'
            ? { ...component, catalog_name: catalogName }
            : { value: component, catalog_name: catalogName };
        lines.push(`COMPONENT ${tagId} ${json(complete)}`);
    });

    const meta = {};
    Object.entries(labState).forEach(([key, value]) => {
        if (key !== 'components') meta[key] = value;
    });
    lines.push(`LAB_META ${json(meta)}`);
    return lines;
}

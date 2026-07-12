/**
 * Generate copy-pasteable Python SDK snippets from capability fields.
 */

/**
 * @param {string} backendId
 * @returns {string}
 */
export function connectPreamble(backendId) {
    const id = JSON.stringify(backendId || 'mock.default');
    return `from lab_model.optimization.sdk import connect\n\nwith connect(${id}) as lab:\n    ...`;
}

/**
 * Expand a tunable declaration into concrete scriptable paths + snippets.
 * @param {string} tagId
 * @param {string} field
 * @param {object} decl
 * @param {object} row catalog row (for motor_ids, etc.)
 * @returns {{ path: string, snippet: string, note?: string }[]}
 */
export function tunableHandles(tagId, field, decl, row = {}) {
    const widget = decl?.widget || '—';
    const unit = decl?.unit || '';
    const bounds = formatBounds(decl);

    if (field === 'nominal_pose') {
        return ['x', 'y', 'rotation'].map((axis) => {
            const path = `tunables.nominal_pose.${axis}`;
            const unitHint = axis === 'rotation' ? 'deg' : 'mm';
            return {
                path,
                widget,
                unit: unitHint,
                bounds: bounds || '—',
                snippet: `lab.move_component(${JSON.stringify(tagId)}, ${JSON.stringify(path)}, <value>)`,
            };
        });
    }

    if (field === 'nominal_motor_positions') {
        const motors = Array.isArray(row.motor_ids) && row.motor_ids.length
            ? row.motor_ids
            : ['<motor_id>'];
        return motors.map((mid) => {
            const path = `tunables.nominal_motor_positions.${mid}`;
            return {
                path,
                widget,
                unit: unit || 'deg',
                bounds: bounds || '—',
                snippet: `lab.move_component(${JSON.stringify(tagId)}, ${JSON.stringify(path)}, <value>)`,
            };
        });
    }

    const path = `tunables.${field}`;
    const writeHint = decl?.write_primitive
        ? `write via ${decl.write_primitive}`
        : 'write via declared primitive / Twin UI';
    return [{
        path,
        widget,
        unit: unit || '—',
        bounds: bounds || '—',
        note: writeHint,
        snippet: `# ${path} — ${writeHint}\n# (no dedicated SDK helper yet; use Twin or POST /api/command)`,
    }];
}

/**
 * @param {string} tagId
 * @param {string} field
 * @param {object} decl
 */
export function measurableHandle(tagId, field, decl = {}) {
    const path = `measurables.${field}`;
    return {
        path,
        field,
        widget: decl.widget || '—',
        format: decl.format || '—',
        domain: decl.domain || inferDomain(field, decl),
        shape: decl.shape || decl.tensor_shape || '—',
        description: decl.description || decl.label || '—',
        snippet: `lab.measurable(${JSON.stringify(tagId)}, ${JSON.stringify(field)}).resolve(record=True)`,
    };
}

/**
 * Sample objective term + kernels[] for an approved TorchScript kernel.
 * @param {string} kernelId
 * @param {string} [cameraTag]
 */
export function torchscriptTermSnippet(kernelId, cameraTag = 'tag_22') {
    const term = {
        id: 'ts_custom',
        weight: 1.0,
        source: {
            tag_id: cameraTag,
            kind: 'torchscript_scalar',
            kernel_id: kernelId,
            from: 'measurables.camera_image',
            normalize: { min: 0.0, max: 1.0 },
        },
        metric: 'one_minus_normalized',
    };
    return {
        termJson: JSON.stringify(term, null, 2),
        kernelsLine: `kernels=[${JSON.stringify(kernelId)}]`,
        sdkSnippet: [
            `from lab_model.optimization.sdk import connect`,
            ``,
            `with connect("<backend_id>") as lab:`,
            `    print(lab.describe_kernel(${JSON.stringify(kernelId)}))`,
            `    # Add this term under objective.terms and pass`,
            `    # kernels=[${JSON.stringify(kernelId)}] on OPTIMIZE / job submit.`,
        ].join('\n'),
    };
}

function formatBounds(decl) {
    if (!decl || typeof decl !== 'object') return '';
    if (decl.min != null || decl.max != null) {
        return `[${decl.min ?? '−∞'}, ${decl.max ?? '+∞'}]`;
    }
    if (decl.bounds) return String(decl.bounds);
    return '';
}

function inferDomain(field, decl) {
    if (decl?.domain) return decl.domain;
    if (field.includes('image') || field.includes('camera') || decl?.widget === 'ImageViewer') {
        return 'spatial';
    }
    if (field.includes('trace') || field.includes('scope') || field.includes('waveform')) {
        return 'time';
    }
    return 'scalar';
}

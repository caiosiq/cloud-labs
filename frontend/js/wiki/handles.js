/**
 * Generate copy-pasteable Python SDK snippets from capability fields,
 * plus user-facing physical interpretations for the Wiki.
 */

/**
 * @param {string} backendId
 * @returns {string}
 */
export function connectPreamble(backendId) {
    const id = JSON.stringify(backendId || 'mock.default');
    return `from cloudlabs import connect\n\nwith connect(${id}) as lab:\n    ...`;
}

/**
 * Plain-language physics meaning for a catalog component type / row.
 * @param {object} row
 * @returns {string}
 */
export function componentPhysicalInterpretation(row = {}) {
    const type = String(row.type || '').toUpperCase();
    const name = row.name || row.tag_id || 'This part';
    const byType = {
        MIRROR: `${name} redirects a free-space beam. Moving it on the breadboard changes where the light goes; motor axes (if present) fine-steer tip/tilt without relocating the mount.`,
        LENS: `${name} focuses or collimates the beam. Pose on the table sets the optical path length and alignment relative to upstream sources.`,
        OPTICAL_CAMERA: `${name} is an eye on the experiment — gripper or table camera. Images are the raw material for kernels (centroids, scores) and for human supervision.`,
        FILTER: `${name} attenuates or spectrally selects light. Treat placement as part of the optical recipe, not only a mechanical pose.`,
        LASER: `${name} is a source. Output power and pointing couple into every downstream alignment loop.`,
        STAGE: `${name} is a precision actuator. Scripted moves should respect bounds — small steps often matter more than large jumps.`,
        IRIS: `${name} apertures the beam. Closing it changes power and spatial mode content seen by cameras.`,
    };
    if (byType[type]) return byType[type];
    if (type.includes('CAMERA')) {
        return `${name} captures light as an image. Use RECORD_MEASURABLES / probe_kernel when you need numbers from that image, not only a live view.`;
    }
    if (type.includes('MIRROR') || type.includes('MOUNT')) {
        return `${name} is a steerable optic. Tunables are degrees of freedom you set (and the lab may recalculate); measurables are observations with no matching setpoint (e.g. camera frames).`;
    }
    return `${name} (${type || 'component'}) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.`;
}

/**
 * Plain-language meaning for a measurable field.
 * @param {string} field
 * @param {object} decl
 * @returns {string}
 */
export function measurablePhysicalInterpretation(field, decl = {}) {
    if (decl.physical_interpretation) return String(decl.physical_interpretation);
    if (decl.description && decl.description.length > 40) return String(decl.description);
    const f = String(field || '');
    if (f === 'camera_image' || f.includes('image')) {
        return 'A still frame from this camera — intensity on the sensor. Kernels read BGR pixels; humans see a picture. No tunable sets this field — it must be captured.';
    }
    if (f.includes('centroid')) {
        return 'The intensity-weighted “center of mass” of the spot on the camera, in pixels. Useful as an alignment error signal toward a target pixel.';
    }
    if (f.includes('power') || f.includes('score') || f.includes('intensity')) {
        return 'A scalar summary of how much light (or how good a match) the current setting produces — higher or lower depending on the metric you chose. Captured, not commanded.';
    }
    if (decl.domain === 'spatial' || (decl.format || '').includes('image') || decl.layout === 'bgr_hwc_uint8') {
        return 'Spatial data from the bench (typically an image). Interpret axes in the camera’s pixel grid unless a calibration maps them to millimetres.';
    }
    return `Observed quantity “${f}” — a measurement with no matching tunable. Prefer the catalog description when present.`;
}

/** Fields that must never appear as Wiki measurables (tunable recalculate paths). */
export const NON_MEASURABLE_FIELDS = new Set(['pose', 'motor_rotations', 'last_optimized_pose']);

/**
 * Canonical analysis-tensor specs for known measurables.
 * Catalog decls override when they provide dtype/layout/axes/domain.
 * @type {Record<string, {dtype: string, layout: string, shape: string, domain: string, axes: Record<string, string>, wire?: string}>}
 */
export const MEASURABLE_TENSOR_SPEC = {
    camera_image: {
        dtype: 'uint8',
        layout: 'bgr_hwc_uint8',
        shape: '(H, W, 3)',
        domain: 'spatial',
        axes: {
            H: 'rows (y), pixels top→bottom',
            W: 'cols (x), pixels left→right',
            '3': 'BGR channels (OpenCV / kernel layout)',
        },
        wire: 'PNG on disk / HTTP — not the analysis type',
    },
    last_optimization_score: {
        dtype: 'float64',
        layout: 'scalar',
        shape: '()',
        domain: 'scalar',
        axes: {},
    },
    output_power_readback_mw: {
        dtype: 'float64',
        layout: 'scalar',
        shape: '()',
        domain: 'scalar',
        axes: {},
        unit: 'mW',
    },
};

/**
 * @param {string} field
 * @param {object} decl
 */
export function resolveMeasurableTensor(field, decl = {}) {
    const base = MEASURABLE_TENSOR_SPEC[field] || {};
    const axes =
        decl.axes && typeof decl.axes === 'object' && !Array.isArray(decl.axes)
            ? decl.axes
            : base.axes || {};
    return {
        dtype: decl.dtype || base.dtype || '—',
        layout: decl.layout || base.layout || '—',
        shape: decl.shape || decl.tensor_shape || base.shape || '—',
        domain: decl.domain || base.domain || inferDomain(field, decl),
        axes,
        wire: decl.wire_format ? `wire: ${decl.wire_format}` : base.wire || '',
        unit: decl.unit || base.unit || '',
    };
}

/**
 * Human-readable multi-line tensor cell (dtype, layout, axis meanings).
 * @param {ReturnType<typeof resolveMeasurableTensor>} tensor
 */
export function formatTensorCell(tensor) {
    const lines = [
        `${tensor.dtype} · ${tensor.layout} · ${tensor.shape}`,
    ];
    if (tensor.unit) lines.push(`unit: ${tensor.unit}`);
    const axisEntries = Object.entries(tensor.axes || {});
    if (axisEntries.length) {
        for (const [k, v] of axisEntries) {
            lines.push(`${k}: ${v}`);
        }
    } else if (tensor.layout === 'scalar' || tensor.shape === '()') {
        lines.push('no axes (0-D scalar)');
    }
    if (tensor.wire) lines.push(tensor.wire);
    return lines.join('\n');
}

/**
 * Plain-language meaning for a kernel catalog row.
 * @param {object} row
 * @returns {string}
 */
export function kernelPhysicalInterpretation(row = {}) {
    if (row.physical_interpretation) return String(row.physical_interpretation);
    const id = String(row.id || '');
    const known = {
        'demo.image_mean_score':
            'Average brightness of the whole frame after normalizing to [0, 1]. Brighter scenes score closer to 1. A teaching scalar — not a calibrated optical power meter.',
        'demo.roi_mean_score':
            'Average brightness in the center half of the frame. Emphasizes the middle of the camera FOV so edge clutter matters less.',
        'demo.peak_intensity':
            'Brightest normalized pixel in the frame — a crude peak-power proxy for demos.',
        'builtin.roi_centroid':
            'Sub-pixel center of the spot over the full frame, returned as (cx, cy) in full-frame pixels. Pair with a pixel target to align a beam on camera.',
        'builtin.beam_power':
            'Power proxy features: flux above background, peak, and saturated-fraction. Maximize flux with ratio_to_ref (first-eval flux is the scale); treat saturation as a hard health flag (pin exposure).',
        'builtin.gaussian_beam_fit':
            'Moment-based Gaussian summary: amplitude, center (cx, cy), and widths (σx, σy) in pixels. Intuition: “how bright, where, how wide” — not a full nonlinear fit.',
        'builtin.beam_shift':
            'Offset of the intensity CoM from this camera frame’s geometric center (W/2, H/2) as [dx, dy, magnitude] in px. Maximize magnitude to push the beam off-center; resolution-agnostic; no reference capture.',
        'builtin.beam_com':
            'Full-frame intensity CoM [cx, cy, peak] (same 50%-of-peak gate as beam_shift). Maximize signed displacement from an operator origin along X or Y (+/−) via signed_axis_offset; peak latches beam presence.',
        'ensemble.eval.block_cobyla':
            'Built-in optimizer hook: COBYLA proposes the next actuator step from the scalar loss. Not an image model — the “brain” that walks the loss landscape.',
        'ensemble.eval.mock_landscape':
            'Mock-only synthetic loss landscape so closed-loop demos work without a real beam. Teaches the job path, not real optics.',
        'ensemble.eval.image_features':
            'Real-bench path that turns live camera features into a scalar loss for the ensemble loop.',
        'measurable.materialize.camera':
            'Ensures each optimization eval can persist a camera frame into measurables for UI and SDK tensors.',
        'objective.compile.weighted_sum':
            'Compiles a declarative multi-term objective into the runtime weighted-sum loss. Bookkeeping, not physics.',
        'stabilization.settle':
            'Wait after a move so mechanics and air settle before the next capture — reduces false loss from vibration.',
    };
    if (known[id]) return known[id];
    if (row.description) {
        return String(row.description);
    }
    if (row.runtime === 'torchscript' || row.output_kind === 'features') {
        return 'TorchScript measurement on a camera frame. Outputs are either a scalar score or a small feature vector (e.g. centroid). Use as an INPUT to OPTIMIZE or probe once while authoring.';
    }
    return 'Catalog edge function. Prefer the description field; when absent, treat the id as an internal hook rather than a physical sensor reading.';
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

    if (field === 'reported_pose') {
        // Legacy internal mirror — not a first-class tunable in the product model.
        // Lab observe recalculates the pose tunable itself; Twin ghost is UI draft only.
        return [];
    }

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
                note: 'Pose tunable. Twin ghost is a visual draft before you confirm; scan/observe recalculates this same field.',
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
                note: 'Lab observe / tracker recalculates this same tunable — not a measurable.',
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
    if (NON_MEASURABLE_FIELDS.has(field)) return null;
    const path = `measurables.${field}`;
    const tensor = resolveMeasurableTensor(field, decl);
    return {
        path,
        field,
        widget: decl.widget || '—',
        domain: tensor.domain,
        tensor,
        tensorText: formatTensorCell(tensor),
        description: decl.description || decl.label || '—',
        physical_interpretation: decl.physical_interpretation || '',
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
            `from cloudlabs import connect`,
            ``,
            `with connect("<backend_id>") as lab:`,
            `    # probe_kernel → EVAL_KERNEL primitive (authoring preview)`,
            `    print(lab.probe_kernel(${JSON.stringify(cameraTag)}, "camera_image", kernel_id=${JSON.stringify(kernelId)}))`,
            `    # Closed-loop: same kernel_id as an INPUT to OPTIMIZE (run_cobyla / run_optimize)`,
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

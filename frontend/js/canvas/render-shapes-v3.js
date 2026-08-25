/**
 * Twin v3 Studio component body art — gradients, glass/metal cues, soft shadows.
 * Dark slate mounts + cyan/blue glass (purple reserved for optimization mode).
 */

function dropShadow(ctx, color, blur = 12, offsetY = 3) {
    ctx.shadowColor = color;
    ctx.shadowBlur = blur;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = offsetY;
}

function clearShadow(ctx) {
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
}

function radialFill(ctx, x0, y0, r0, x1, y1, r1, stops) {
    const g = ctx.createRadialGradient(x0, y0, r0, x1, y1, r1);
    stops.forEach(([pos, col]) => g.addColorStop(pos, col));
    ctx.fillStyle = g;
}

function linearFill(ctx, x0, y0, x1, y1, stops) {
    const g = ctx.createLinearGradient(x0, y0, x1, y1);
    stops.forEach(([pos, col]) => g.addColorStop(pos, col));
    ctx.fillStyle = g;
}

function drawMountPlate(ctx, halfW, halfH, w, h) {
    linearFill(ctx, -halfW, -halfH, halfW, halfH, [
        [0, '#1e293b'],
        [0.5, '#334155'],
        [1, '#1e293b'],
    ]);
    ctx.fillRect(-halfW, -halfH, w, h);
    ctx.strokeStyle = 'rgba(96, 165, 250, 0.4)';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(-halfW, -halfH, w, h);
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {{ catalogId: string|null, type: string, halfW: number, halfH: number, w: number, h: number }} p
 */
export function drawComponentBodyV3(ctx, { catalogId, type, halfW, halfH, w, h }) {
    dropShadow(ctx, 'rgba(15, 23, 42, 0.5)', 14, 4);

    if (catalogId === 'nd_filter' || catalogId === 'filter_generic') {
        drawMountPlate(ctx, halfW, halfH, w, h);
        const tint = catalogId === 'filter_generic' ? 'rgba(244, 63, 94, 0.35)' : 'rgba(15, 23, 42, 0.75)';
        radialFill(ctx, 0, 0, 2, 0, 0, Math.min(halfW, halfH) * 0.7, [
            [0, tint],
            [1, 'rgba(30, 41, 59, 0.2)'],
        ]);
        ctx.beginPath();
        ctx.arc(0, 0, Math.min(halfW, halfH) * 0.65, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.4)';
        ctx.lineWidth = 1;
        ctx.stroke();
    } else if (
        catalogId === 'P1' ||
        catalogId === 'tilted_polarizer' ||
        type === 'OPTICAL_POLARIZER'
    ) {
        drawMountPlate(ctx, halfW, halfH, w, h);
        const r = Math.min(halfW, halfH) * 0.72;
        radialFill(ctx, -r * 0.2, -r * 0.2, r * 0.1, 0, 0, r, [
            [0, 'rgba(56, 189, 248, 0.55)'],
            [0.6, 'rgba(125, 211, 252, 0.25)'],
            [1, 'rgba(30, 41, 59, 0.15)'],
        ]);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = 'rgba(79, 143, 247, 0.5)';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.strokeStyle = 'rgba(251, 191, 36, 0.7)';
        ctx.lineWidth = 1;
        const step = Math.max(4, r / 4);
        for (let x = -r; x <= r; x += step) {
            const halfChord = Math.sqrt(Math.max(0, r * r - x * x));
            ctx.beginPath();
            ctx.moveTo(x, -halfChord);
            ctx.lineTo(x, halfChord);
            ctx.stroke();
        }
    } else if (catalogId === 'cam_gripper_1' || catalogId === 'cam_gripper_2' || type === 'OPTICAL_CAMERA') {
        linearFill(ctx, -halfW, -halfH, halfW, halfH, [
            [0, '#1e293b'],
            [1, '#0f172a'],
        ]);
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.5)';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(-halfW, -halfH, w, h);
        const lensR = Math.min(w, h) / 3;
        radialFill(ctx, -lensR * 0.25, -lensR * 0.25, lensR * 0.05, 0, 0, lensR, [
            [0, '#1e3a5f'],
            [0.5, '#0f172a'],
            [1, '#020617'],
        ]);
        ctx.beginPath();
        ctx.arc(0, 0, lensR, 0, Math.PI * 2);
        ctx.fill();
        radialFill(ctx, -lensR * 0.3, -lensR * 0.35, 1, 0, 0, lensR * 0.35, [
            [0, 'rgba(255,255,255,0.45)'],
            [1, 'rgba(255,255,255,0)'],
        ]);
        ctx.beginPath();
        ctx.arc(-lensR * 0.25, -lensR * 0.3, lensR * 0.22, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#ef4444';
        const triH = h / 4;
        ctx.beginPath();
        ctx.moveTo(0, -halfH - 2);
        ctx.lineTo(-triH / 2, -halfH - triH - 2);
        ctx.lineTo(triH / 2, -halfH - triH - 2);
        ctx.fill();
    } else if (catalogId === 'mirror_curved') {
        const radius = Math.min(w, h) / 2;
        const startA = -Math.PI / 2;
        const endA = Math.PI / 2;
        linearFill(ctx, -radius, 0, radius, 0, [
            [0, '#64748b'],
            [0.5, '#e2e8f0'],
            [1, '#94a3b8'],
        ]);
        ctx.beginPath();
        ctx.arc(0, 0, radius + 3, startA, endA, true);
        ctx.arc(0, 0, radius - 1, endA, startA, false);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.45)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(0, 0, radius, startA, endA, true);
        ctx.stroke();
        ctx.strokeStyle = 'rgba(255,255,255,0.65)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(0, 0, radius - 3, startA + 0.2, endA - 0.2, true);
        ctx.stroke();
    } else if (catalogId === 'mirror_planar' || type === 'OPTICAL_MIRROR') {
        linearFill(ctx, -halfW / 2, -halfH, 0, halfH, [
            [0, '#94a3b8'],
            [1, '#334155'],
        ]);
        ctx.fillRect(-halfW / 2, -halfH, halfW / 2, h);
        linearFill(ctx, -2, -halfH, 2, halfH, [
            [0, '#f1f5f9'],
            [0.5, '#cbd5e1'],
            [1, '#f8fafc'],
        ]);
        ctx.fillRect(-2, -halfH, 4, h);
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.35)';
        ctx.lineWidth = 1;
        ctx.strokeRect(-halfW / 2, -halfH, halfW / 2 + 2, h);
    } else if (catalogId === 'beam_block') {
        linearFill(ctx, -halfW, -halfH, halfW, halfH, [
            [0, '#334155'],
            [1, '#0f172a'],
        ]);
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(-halfW, -halfH);
        ctx.lineTo(halfW, halfH);
        ctx.moveTo(halfW, -halfH);
        ctx.lineTo(-halfW, halfH);
        ctx.stroke();
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.35)';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(-halfW, -halfH, w, h);
    } else if (catalogId === 'beam_splitter' || type === 'OPTICAL_BEAMSPLITTER') {
        radialFill(ctx, 0, 0, 2, 0, 0, Math.max(halfW, halfH), [
            [0, 'rgba(226, 232, 240, 0.75)'],
            [1, 'rgba(125, 211, 252, 0.25)'],
        ]);
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.4)';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = 'rgba(59, 130, 246, 0.75)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-halfW, -halfH);
        ctx.lineTo(halfW, halfH);
        ctx.stroke();
    } else if (catalogId === 'lens_main' || type === 'OPTICAL_LENS') {
        const rx = halfW / 3;
        const ry = halfH;
        radialFill(ctx, -rx * 0.3, -ry * 0.35, rx * 0.1, 0, 0, Math.max(rx, ry), [
            [0, 'rgba(186, 230, 253, 0.75)'],
            [0.5, 'rgba(125, 211, 252, 0.45)'],
            [1, 'rgba(30, 41, 59, 0.2)'],
        ]);
        ctx.beginPath();
        ctx.ellipse(0, 0, rx, ry, 0, 0, 2 * Math.PI);
        ctx.fill();
        ctx.strokeStyle = 'rgba(79, 143, 247, 0.55)';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.strokeStyle = 'rgba(255,255,255,0.55)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.ellipse(-rx * 0.15, -ry * 0.2, rx * 0.55, ry * 0.35, -0.4, 0, Math.PI);
        ctx.stroke();
    } else if (catalogId === 'crystal_main' || type === 'OPTICAL_CRYSTAL') {
        linearFill(ctx, -halfW, -halfH, halfW, halfH, [
            [0, 'rgba(236, 72, 153, 0.4)'],
            [0.5, 'rgba(56, 189, 248, 0.45)'],
            [1, 'rgba(244, 114, 182, 0.3)'],
        ]);
        ctx.beginPath();
        ctx.moveTo(-halfW / 2, -halfH);
        ctx.lineTo(halfW / 2, -halfH);
        ctx.lineTo(halfW, 0);
        ctx.lineTo(halfW / 2, halfH);
        ctx.lineTo(-halfW / 2, halfH);
        ctx.lineTo(-halfW, 0);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.55)';
        ctx.lineWidth = 1.5;
        ctx.stroke();
    } else if (catalogId === 'laser_main' || type === 'LASER_SOURCE') {
        linearFill(ctx, -halfW, -halfH, halfW, halfH, [
            [0, '#1e293b'],
            [1, '#0f172a'],
        ]);
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.45)';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(-halfW, -halfH, w, h);
        radialFill(ctx, halfW * 0.15, 0, 1, halfW * 0.5, 0, halfW * 0.5, [
            [0, '#fecaca'],
            [0.5, '#ef4444'],
            [1, '#991b1b'],
        ]);
        ctx.beginPath();
        ctx.arc(halfW * 0.35, 0, halfW * 0.22, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fca5a5';
        ctx.beginPath();
        ctx.moveTo(halfW * 0.55, 0);
        ctx.lineTo(halfW + 8, 0);
        ctx.lineTo(halfW + 3, -5);
        ctx.lineTo(halfW + 3, 5);
        ctx.closePath();
        ctx.fill();
    } else {
        const r = Math.min(halfW, halfH);
        radialFill(ctx, -r * 0.25, -r * 0.25, r * 0.05, 0, 0, r, [
            [0, '#e2e8f0'],
            [0.6, '#94a3b8'],
            [1, '#64748b'],
        ]);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = 'rgba(96, 165, 250, 0.35)';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = '#64748b';
        ctx.font = '600 11px "Source Sans 3", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('?', 0, 4);
    }

    clearShadow(ctx);
}

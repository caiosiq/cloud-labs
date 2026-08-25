/**
 * Twin v2 component body art — flat procedural shapes (unchanged from original render.js).
 */

/**
 * @param {CanvasRenderingContext2D} ctx — already translated/rotated to component center
 * @param {{ catalogId: string|null, type: string, halfW: number, halfH: number, w: number, h: number }} p
 */
export function drawComponentBodyV2(ctx, { catalogId, type, halfW, halfH, w, h }) {
    if (catalogId === 'nd_filter') {
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.fillStyle = 'rgba(20, 20, 20, 0.9)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);
    } else if (catalogId === 'filter_generic') {
        ctx.fillStyle = '#333';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#f87171';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.fillStyle = 'rgba(248, 113, 113, 0.3)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);
    } else if (
        catalogId === 'P1' ||
        catalogId === 'tilted_polarizer' ||
        type === 'OPTICAL_POLARIZER'
    ) {
        ctx.fillStyle = '#1a1f2e';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        const r = Math.min(halfW, halfH) * 0.72;
        ctx.fillStyle = 'rgba(56, 189, 248, 0.18)';
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#7dd3fc';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.strokeStyle = 'rgba(251, 191, 36, 0.85)';
        ctx.lineWidth = 1.25;
        const step = Math.max(4, r / 4);
        for (let x = -r; x <= r; x += step) {
            const halfChord = Math.sqrt(Math.max(0, r * r - x * x));
            ctx.beginPath();
            ctx.moveTo(x, -halfChord);
            ctx.lineTo(x, halfChord);
            ctx.stroke();
        }
        ctx.strokeStyle = '#fbbf24';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(0, r * 0.55);
        ctx.lineTo(0, -r * 0.55);
        ctx.moveTo(-4, -r * 0.35);
        ctx.lineTo(0, -r * 0.55);
        ctx.lineTo(4, -r * 0.35);
        ctx.stroke();
    } else if (catalogId === 'cam_gripper_1' || catalogId === 'cam_gripper_2' || type === 'OPTICAL_CAMERA') {
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.fillStyle = '#000';
        ctx.beginPath();
        ctx.arc(0, 0, Math.min(w, h) / 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#3b82f6';
        ctx.beginPath();
        ctx.arc(0, 0, Math.min(w, h) / 8, 0, Math.PI * 2);
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
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(0, 0, radius, startA, endA, true);
        ctx.stroke();
        ctx.fillStyle = '#444';
        ctx.beginPath();
        ctx.arc(0, 0, radius + 4, startA, endA, true);
        ctx.arc(0, 0, radius, endA, startA, false);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.6)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(0, 0, radius - 2, startA, endA, true);
        ctx.stroke();
    } else if (catalogId === 'mirror_planar' || type === 'OPTICAL_MIRROR') {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(0, -halfH);
        ctx.lineTo(0, halfH);
        ctx.stroke();
        ctx.fillStyle = '#444';
        ctx.fillRect(-halfW / 2, -halfH, halfW / 2, h);
        ctx.strokeStyle = 'rgba(255,255,255,0.5)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(2, -halfH + 5);
        ctx.lineTo(2, halfH - 5);
        ctx.stroke();
    } else if (catalogId === 'beam_block') {
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-halfW, -halfH);
        ctx.lineTo(halfW, halfH);
        ctx.moveTo(halfW, -halfH);
        ctx.lineTo(-halfW, halfH);
        ctx.stroke();
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
    } else if (catalogId === 'beam_splitter' || type === 'OPTICAL_BEAMSPLITTER') {
        ctx.fillStyle = 'rgba(200, 200, 200, 0.1)';
        ctx.strokeStyle = '#888';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = 'rgba(100, 200, 255, 0.8)';
        ctx.beginPath();
        ctx.moveTo(-halfW, -halfH);
        ctx.lineTo(halfW, halfH);
        ctx.stroke();
    } else if (catalogId === 'lens_main' || type === 'OPTICAL_LENS') {
        ctx.fillStyle = 'rgba(100, 200, 255, 0.3)';
        ctx.strokeStyle = 'rgba(150, 220, 255, 0.9)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.ellipse(0, 0, halfW / 3, halfH, 0, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
    } else if (catalogId === 'crystal_main' || type === 'OPTICAL_CRYSTAL') {
        ctx.fillStyle = 'rgba(236, 72, 153, 0.3)';
        ctx.strokeStyle = '#ec4899';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-halfW / 2, -halfH);
        ctx.lineTo(halfW / 2, -halfH);
        ctx.lineTo(halfW, 0);
        ctx.lineTo(halfW / 2, halfH);
        ctx.lineTo(-halfW / 2, halfH);
        ctx.lineTo(-halfW, 0);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
    } else if (catalogId === 'laser_main' || type === 'LASER_SOURCE') {
        ctx.fillStyle = '#1a1f2e';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        ctx.fillStyle = '#ef4444';
        ctx.beginPath();
        ctx.moveTo(halfW * 0.3, 0);
        ctx.lineTo(halfW + 6, 0);
        ctx.lineTo(halfW + 2, -4);
        ctx.lineTo(halfW + 2, 4);
        ctx.closePath();
        ctx.fill();
    } else {
        ctx.fillStyle = '#C0C0C0';
        const r = Math.min(halfW, halfH);
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#000';
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('?', 0, 4);
    }
}

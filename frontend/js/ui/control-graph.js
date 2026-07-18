/**
 * Git-style configuration commit graph (SVG) — horizontal timeline above canvas.
 */
import { shortCommitId } from '../api/control.js';

const LANE_HEIGHT = 34;
const GEN_WIDTH = 92;
const NODE_R = 8;
const PAD_X = 18;
const PAD_Y = 18;

const COLORS = {
    edge: '#475569',
    edgeActive: '#64748b',
    node: '#334155',
    nodeInactive: '#1e293b',
    headRing: '#22c55e',
    selectedRing: '#3b82f6',
    viewingRing: '#f59e0b',
    label: '#64748b',
    message: '#cbd5e1',
    laneLabel: '#94a3b8',
};

/**
 * Horizontal layout: time flows left → right (depth = column), branches = lanes (rows).
 * @param {Array<{id:string,parent_id?:string|null,branch?:string,message?:string,created_at?:string}>} nodes
 */
export function layoutCommitGraph(nodes, { heads = {}, activeBranch = 'main', headId = null } = {}) {
    if (!nodes?.length) {
        return { width: 320, height: 80, positions: new Map(), edges: [], nodes: [] };
    }

    const byId = new Map(nodes.map((n) => [String(n.id), n]));
    const depthById = new Map();

    function depth(id, visiting = new Set()) {
        if (depthById.has(id)) return depthById.get(id);
        if (visiting.has(id)) return 0;
        visiting.add(id);
        const node = byId.get(id);
        const parent = node?.parent_id ? String(node.parent_id) : null;
        if (!parent || !byId.has(parent)) {
            depthById.set(id, 0);
            return 0;
        }
        const d = depth(parent, visiting) + 1;
        depthById.set(id, d);
        return d;
    }

    nodes.forEach((n) => depth(String(n.id)));

    const branchLane = new Map();
    let nextLane = 0;
    const laneForBranch = (branch) => {
        const name = branch || 'main';
        if (name === 'main' && !branchLane.has('main')) {
            branchLane.set('main', 0);
            if (nextLane === 0) nextLane = 1;
            return 0;
        }
        if (!branchLane.has(name)) {
            branchLane.set(name, nextLane++);
        }
        return branchLane.get(name);
    };

    nodes.forEach((n) => laneForBranch(n.branch));

    const activeChain = new Set();
    let cur = headId ? String(headId) : null;
    while (cur && byId.has(cur)) {
        activeChain.add(cur);
        cur = byId.get(cur)?.parent_id ? String(byId.get(cur).parent_id) : null;
    }

    const headSet = new Set(
        Object.entries(heads || {})
            .filter(([, id]) => id)
            .map(([, id]) => String(id)),
    );

    const positions = new Map();
    let maxDepth = 0;
    let maxLane = 0;

    nodes.forEach((node) => {
        const id = String(node.id);
        const gen = depthById.get(id) ?? 0;
        const lane = laneForBranch(node.branch);
        maxDepth = Math.max(maxDepth, gen);
        maxLane = Math.max(maxLane, lane);
        positions.set(id, {
            x: PAD_X + gen * GEN_WIDTH,
            y: PAD_Y + lane * LANE_HEIGHT,
            gen,
            lane,
            node,
            onActiveBranch: activeChain.has(id),
            isHead: headSet.has(id),
        });
    });

    const edges = [];
    nodes.forEach((node) => {
        const childId = String(node.id);
        const parentId = node.parent_id ? String(node.parent_id) : null;
        if (!parentId || !positions.has(parentId) || !positions.has(childId)) return;
        edges.push({
            from: positions.get(parentId),
            to: positions.get(childId),
            active: activeChain.has(parentId) && activeChain.has(childId),
        });
    });

    const width = PAD_X * 2 + (maxDepth + 1) * GEN_WIDTH;
    // Lanes span PAD_Y … PAD_Y + maxLane*LANE_HEIGHT, so this keeps a single-lane
    // (linear) history vertically centered instead of top-aligned with dead space.
    const height = PAD_Y * 2 + maxLane * LANE_HEIGHT;

    return { width, height, positions, edges, nodes, branchLane, activeChain, headId };
}

function edgePath(from, to) {
    const x1 = from.x;
    const y1 = from.y;
    const x2 = to.x;
    const y2 = to.y;
    if (Math.abs(y1 - y2) < 1) {
        return `M ${x1 + NODE_R} ${y1} L ${x2 - NODE_R} ${y2}`;
    }
    const midX = (x1 + x2) / 2;
    return `M ${x1 + NODE_R} ${y1} L ${midX} ${y1} L ${midX} ${y2} L ${x2 - NODE_R} ${y2}`;
}

/**
 * @param {HTMLElement} container
 * @param {ReturnType<typeof layoutCommitGraph>} layout
 * @param {{ headId?: string|null, selectedId?: string|null, viewingId?: string|null, onNodeClick?: (id: string, node: object) => void }} state
 */
export function renderControlGraph(container, layout, state = {}) {
    if (!container) return;

    const wrap = container.querySelector('.control-graph-wrap') || container;
    let svg = wrap.querySelector('#control-graph-svg');
    let emptyEl = wrap.querySelector('#control-graph-empty');
    let tooltip = wrap.querySelector('#control-graph-tooltip');

    if (!svg) {
        svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.id = 'control-graph-svg';
        svg.setAttribute('class', 'control-graph-svg');
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', 'Configuration commit graph');
        wrap.appendChild(svg);
    }
    if (!emptyEl) {
        emptyEl = document.createElement('div');
        emptyEl.id = 'control-graph-empty';
        emptyEl.className = 'control-graph-empty';
        wrap.appendChild(emptyEl);
    }
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'control-graph-tooltip';
        tooltip.className = 'control-graph-tooltip';
        tooltip.hidden = true;
        wrap.appendChild(tooltip);
    }

    const { headId, selectedId, viewingId, appliedId, uncommitted, onNodeClick } = state;

    if (!layout.nodes?.length) {
        svg.innerHTML = '';
        svg.style.display = 'none';
        emptyEl.hidden = false;
        emptyEl.textContent = uncommitted
            ? 'Working table — uncommitted. Use Commit to start history on this branch.'
            : 'No commits in this repo — use Commit to snapshot the current table and start history.';
        container.hidden = false;
        return;
    }

    emptyEl.hidden = true;
    svg.style.display = 'block';
    svg.setAttribute('width', String(layout.width));
    svg.setAttribute('height', String(layout.height));
    svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
    svg.innerHTML = '';

    const ns = 'http://www.w3.org/2000/svg';

    const edgeLayer = document.createElementNS(ns, 'g');
    edgeLayer.setAttribute('class', 'control-graph-edges');
    layout.edges.forEach(({ from, to, active }) => {
        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d', edgePath(from, to));
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', active ? COLORS.edgeActive : COLORS.edge);
        path.setAttribute('stroke-width', active ? '2' : '1.5');
        path.setAttribute('opacity', active ? '0.95' : '0.3');
        edgeLayer.appendChild(path);
    });
    svg.appendChild(edgeLayer);

    if (layout.branchLane.size > 1) {
        const labelLayer = document.createElementNS(ns, 'g');
        labelLayer.setAttribute('class', 'control-graph-branch-labels');
        layout.branchLane.forEach((lane, branchName) => {
            const text = document.createElementNS(ns, 'text');
            text.setAttribute('x', '2');
            // Sit the lane label ABOVE the node row so it never hides behind the
            // first node's circle (which used to clip "main" down to just "m").
            // Clamp so the top lane's label doesn't clip past the SVG top edge.
            text.setAttribute('y', String(Math.max(9, PAD_Y + lane * LANE_HEIGHT - NODE_R - 3)));
            text.setAttribute('fill', COLORS.laneLabel);
            text.setAttribute('font-size', '10');
            text.textContent = branchName === 'main' ? 'main' : branchName;
            labelLayer.appendChild(text);
        });
        svg.appendChild(labelLayer);
    }

    const nodeLayer = document.createElementNS(ns, 'g');
    nodeLayer.setAttribute('class', 'control-graph-nodes');

    layout.positions.forEach((pos, id) => {
        const g = document.createElementNS(ns, 'g');
        g.setAttribute('class', 'control-graph-node');
        g.setAttribute('data-commit-id', id);
        g.style.cursor = 'pointer';

        const inactive = !pos.onActiveBranch;
        const isHead = id === headId || pos.isHead;
        const isApplied = !!appliedId && id === appliedId;
        const isSelected = id === selectedId;
        const isViewing = id === viewingId && id !== headId;

        if (isHead) {
            const ring = document.createElementNS(ns, 'circle');
            ring.setAttribute('cx', String(pos.x));
            ring.setAttribute('cy', String(pos.y));
            ring.setAttribute('r', String(NODE_R + 3));
            ring.setAttribute('fill', 'none');
            ring.setAttribute('stroke', COLORS.headRing);
            ring.setAttribute('stroke-width', id === headId ? '2' : '1.5');
            ring.setAttribute('opacity', inactive ? '0.45' : '1');
            g.appendChild(ring);
        }
        if (isSelected) {
            const sel = document.createElementNS(ns, 'circle');
            sel.setAttribute('cx', String(pos.x));
            sel.setAttribute('cy', String(pos.y));
            sel.setAttribute('r', String(NODE_R + 4));
            sel.setAttribute('fill', 'none');
            sel.setAttribute('stroke', COLORS.selectedRing);
            sel.setAttribute('stroke-width', '1.5');
            g.appendChild(sel);
        }
        if (isViewing) {
            const view = document.createElementNS(ns, 'circle');
            view.setAttribute('cx', String(pos.x));
            view.setAttribute('cy', String(pos.y));
            view.setAttribute('r', String(NODE_R + 4));
            view.setAttribute('fill', 'none');
            view.setAttribute('stroke', COLORS.viewingRing);
            view.setAttribute('stroke-width', '1.5');
            g.appendChild(view);
        }

        const circle = document.createElementNS(ns, 'circle');
        circle.setAttribute('cx', String(pos.x));
        circle.setAttribute('cy', String(pos.y));
        circle.setAttribute('r', String(NODE_R));
        // The applied node is "you are here": fill it solid green so it's
        // unmistakable which node the live bench currently sits on (distinct
        // from other branch heads, which only get the green ring).
        circle.setAttribute('fill', isApplied ? COLORS.headRing : inactive ? COLORS.nodeInactive : COLORS.node);
        circle.setAttribute('stroke', isHead || isApplied ? COLORS.headRing : '#64748b');
        circle.setAttribute('stroke-width', isHead || isApplied ? '1.5' : '1');
        circle.setAttribute('opacity', inactive ? '0.4' : '1');
        g.appendChild(circle);

        g.addEventListener('click', (ev) => {
            ev.stopPropagation();
            onNodeClick?.(id, pos.node);
        });
        g.addEventListener('mouseenter', () => {
            const n = pos.node || {};
            const tags = [isApplied ? 'current' : null, isHead ? 'HEAD' : null].filter(Boolean).join(' · ');
            tooltip.innerHTML = `<strong>${shortCommitId(id)}</strong>${tags ? ` · ${tags}` : ''}<br>${n.message || '(no message)'}<br><span style="color:#64748b">${n.branch || 'main'} · ${formatTime(n.created_at)}</span>`;
            tooltip.hidden = false;
        });
        g.addEventListener('mousemove', (ev) => {
            positionTooltip(tooltip, wrap, ev.clientX, ev.clientY);
        });
        g.addEventListener('mouseleave', () => {
            tooltip.hidden = true;
        });

        nodeLayer.appendChild(g);
    });
    svg.appendChild(nodeLayer);

    container.hidden = false;

    if (headId) {
        requestAnimationFrame(() => {
            const headPos = layout.positions.get(String(headId));
            if (headPos && wrap.scrollWidth > wrap.clientWidth) {
                const target = Math.max(0, headPos.x - wrap.clientWidth * 0.65);
                wrap.scrollTo({ left: target, behavior: 'smooth' });
            }
        });
    }
}

function formatTime(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    } catch {
        return String(iso);
    }
}

function positionTooltip(tooltip, wrap, clientX, clientY) {
    const rect = wrap.getBoundingClientRect();
    const left = clientX - rect.left + 12;
    const top = clientY - rect.top + 12;
    tooltip.style.left = `${Math.min(left, rect.width - 240)}px`;
    tooltip.style.top = `${Math.min(top, rect.height - 72)}px`;
}

export function renderControlGraphLoading(container) {
    if (!container) return;
    container.hidden = false;
    const emptyEl = container.querySelector('#control-graph-empty');
    const svg = container.querySelector('#control-graph-svg');
    if (svg) svg.style.display = 'none';
    if (emptyEl) {
        emptyEl.hidden = false;
        emptyEl.textContent = 'Loading configuration graph…';
    }
}

/**
 * `TablePose` — tunable widget for ``nominal_pose`` (§14.1).
 *
 * Phase 7 ships the **read-only** sibling of the canvas-drag editor.
 * The canvas (and the X/Y/Rot inputs in context-panel.js) already let
 * the operator commit pose intent; this widget exists so every
 * component renders the *same* receipt block, declaratively, from the
 * catalog. Edit affordances are wired through the existing context-
 * panel controls until Phase 8 promotes the widget to a full editor.
 */
import { widgetCard, widgetTitle, row, fmtNum } from './common.js';

export default function TablePose({ fieldName, descriptor, value }) {
    const card = widgetCard();
    card.appendChild(widgetTitle(fieldName, descriptor));
    const pose = value || {};
    const hasXY = Number.isFinite(Number(pose.x)) && Number.isFinite(Number(pose.y));
    if (!hasXY) {
        const el = document.createElement('div');
        el.style.fontStyle = 'italic';
        el.style.color = '#475569';
        el.style.fontSize = '11px';
        el.textContent = '\u2014 no pose committed (component is in inventory or storage)';
        card.appendChild(el);
        return card;
    }
    card.appendChild(row('x (mm)', fmtNum(pose.x, 1)));
    card.appendChild(row('y (mm)', fmtNum(pose.y, 1)));
    card.appendChild(row('rotation (\u00b0)', fmtNum(pose.rotation || 0, 1)));
    return card;
}

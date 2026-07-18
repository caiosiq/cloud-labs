/**
 * Minimal Markdown → HTML for Wiki guides (headings, lists, code, tables, images, links).
 * Not a full CommonMark implementation — enough for our curriculum files.
 */

/**
 * @param {string} md
 * @returns {string} HTML (trusted content from our own /static guides)
 */
export function renderMarkdown(md) {
    const text = String(md || '').replace(/\r\n/g, '\n');
    const lines = text.split('\n');
    const out = [];
    let i = 0;
    let inCode = false;
    let codeLang = '';
    let codeBuf = [];
    let listType = null; // 'ul' | 'ol'
    let tableRows = [];
    let paraBuf = [];

    const flushList = () => {
        if (!listType) return;
        out.push(`</${listType}>`);
        listType = null;
    };

    const flushPara = () => {
        if (!paraBuf.length) return;
        out.push(`<p>${inlineFormat(paraBuf.join(' '))}</p>`);
        paraBuf = [];
    };

    const flushTable = () => {
        if (!tableRows.length) return;
        const rows = tableRows.filter((r) => !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(r));
        if (!rows.length) {
            tableRows = [];
            return;
        }
        const parseRow = (row) =>
            row
                .replace(/^\|/, '')
                .replace(/\|$/, '')
                .split('|')
                .map((c) => inlineFormat(c.trim()));
        const header = parseRow(rows[0]);
        out.push('<table><thead><tr>');
        header.forEach((c) => out.push(`<th>${c}</th>`));
        out.push('</tr></thead><tbody>');
        for (let r = 1; r < rows.length; r++) {
            out.push('<tr>');
            parseRow(rows[r]).forEach((c) => out.push(`<td>${c}</td>`));
            out.push('</tr>');
        }
        out.push('</tbody></table>');
        tableRows = [];
    };

    const appendToLastLi = (chunk) => {
        const last = out.length - 1;
        if (last < 0 || !out[last].startsWith('<li>')) return false;
        out[last] = out[last].replace(/<\/li>$/, ` ${chunk}</li>`);
        return true;
    };

    const isBlockStart = (line) =>
        line.startsWith('```') ||
        /^(#{1,3})\s+/.test(line) ||
        /^[-*]\s+/.test(line) ||
        /^\d+\.\s+/.test(line) ||
        (/^\s*\|/.test(line) && line.includes('|')) ||
        /^!\[([^\]]*)\]\(([^)]+)\)$/.test(line.trim());

    while (i < lines.length) {
        const line = lines[i];

        if (line.startsWith('```')) {
            flushPara();
            flushList();
            flushTable();
            if (!inCode) {
                inCode = true;
                codeLang = line.slice(3).trim();
                codeBuf = [];
            } else {
                out.push(
                    `<pre class="snippet"><code class="lang-${escapeAttr(codeLang)}">${escapeHtml(
                        codeBuf.join('\n'),
                    )}</code></pre>`,
                );
                inCode = false;
                codeLang = '';
                codeBuf = [];
            }
            i++;
            continue;
        }

        if (inCode) {
            codeBuf.push(line);
            i++;
            continue;
        }

        if (/^\s*\|/.test(line) && line.includes('|')) {
            flushPara();
            flushList();
            tableRows.push(line);
            i++;
            continue;
        }
        if (tableRows.length) flushTable();

        if (!line.trim()) {
            flushPara();
            // Keep list open across blank lines (item spacing)
            i++;
            continue;
        }

        const h = /^(#{1,3})\s+(.+)$/.exec(line);
        if (h) {
            flushPara();
            flushList();
            const level = h[1].length;
            out.push(`<h${level}>${inlineFormat(h[2])}</h${level}>`);
            i++;
            continue;
        }

        const img = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(line.trim());
        if (img) {
            flushPara();
            flushList();
            const alt = escapeAttr(img[1]);
            const src = escapeAttr(img[2]);
            out.push(`<img class="guide-figure" src="${src}" alt="${alt}" />`);
            i++;
            continue;
        }

        const ul = /^[-*]\s+(.+)$/.exec(line);
        if (ul) {
            flushPara();
            if (listType !== 'ul') {
                flushList();
                listType = 'ul';
                out.push('<ul>');
            }
            out.push(`<li>${inlineFormat(ul[1])}</li>`);
            i++;
            continue;
        }

        const ol = /^\d+\.\s+(.+)$/.exec(line);
        if (ol) {
            flushPara();
            if (listType !== 'ol') {
                flushList();
                listType = 'ol';
                out.push('<ol>');
            }
            out.push(`<li>${inlineFormat(ol[1])}</li>`);
            i++;
            continue;
        }

        // Indented continuation of the current list item
        if (listType && /^\s{2,}\S/.test(line)) {
            flushPara();
            appendToLastLi(inlineFormat(line.trim()));
            i++;
            continue;
        }

        flushList();
        // Soft-wrapped paragraph: join consecutive prose lines until blank / block
        paraBuf.push(line.trim());
        i++;
        while (i < lines.length) {
            const next = lines[i];
            if (!next.trim() || isBlockStart(next) || next.startsWith('```')) break;
            if (/^\s{2,}\S/.test(next)) break;
            paraBuf.push(next.trim());
            i++;
        }
        flushPara();
    }

    flushPara();
    flushList();
    flushTable();
    if (inCode) {
        out.push(`<pre class="snippet"><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`);
    }
    return out.join('\n');
}

function inlineFormat(s) {
    let t = escapeHtml(s);
    t = t.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, src) => {
        return `<img class="guide-figure" src="${escapeAttr(src)}" alt="${escapeAttr(alt)}" />`;
    });
    t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
        return `<a href="${escapeAttr(href)}">${label}</a>`;
    });
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[^*])\*([^*]+)\*([^*]|$)/g, '$1<em>$2</em>$3');
    return t;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, '&#39;');
}

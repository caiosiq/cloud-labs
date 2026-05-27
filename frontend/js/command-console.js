/**
 * Command Console — UI shell, history, Tab completion (command-complete.js, command-api.js).
 * Tab keeps a session so repeated Tab cycles the same candidate list; hint shows single match / counts.
 * @see coding_on_the_ui.md
 */

import { getCompletionSlot, getTabCompletions } from './command-complete.js';
import { dispatchConsoleLine } from './command-api.js';

const HISTORY_STORAGE_KEY = 'optics_dtw.command_console.history';
const MAX_HISTORY_LINES = 50;

function loadHistoryFromStorage() {
    try {
        const raw = sessionStorage.getItem(HISTORY_STORAGE_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return [];
        return arr.filter((s) => typeof s === 'string').slice(-MAX_HISTORY_LINES);
    } catch {
        return [];
    }
}

function saveHistoryToStorage(list) {
    try {
        sessionStorage.setItem(
            HISTORY_STORAGE_KEY,
            JSON.stringify(list.slice(-MAX_HISTORY_LINES))
        );
    } catch {
        /* quota / private mode */
    }
}

function pushHistoryLine(historyList, line) {
    if (!line) return;
    if (historyList.length && historyList[historyList.length - 1] === line) return;
    historyList.push(line);
    if (historyList.length > MAX_HISTORY_LINES) {
        historyList.shift();
    }
    saveHistoryToStorage(historyList);
}

/**
 * @param {object} deps
 */
let _commandConsoleReady = false;

export function initCommandConsole(deps) {
    if (_commandConsoleReady) return;
    const { log } = deps;
    const root = document.getElementById('command-console-root');
    const panel = document.getElementById('command-console-panel');
    const output = document.getElementById('command-console-output');
    const input = document.getElementById('command-console-input');
    const toggle = document.getElementById('command-console-toggle');

    if (!root || !panel || !output || !input || !toggle) {
        console.warn('[Command Console] DOM nodes missing');
        return;
    }

    const hintEl = document.createElement('div');
    hintEl.className = 'command-console__hint';
    hintEl.id = 'command-console-hint';
    hintEl.setAttribute('aria-live', 'polite');
    input.insertAdjacentElement('afterend', hintEl);

    const chevron = toggle.querySelector('.command-console__chevron');
    let expanded = false;

    const historyList = loadHistoryFromStorage();
    let historyNavIndex = -1;

    /** @type {{ start: number, end: number, candidates: string[], idx: number } | null} */
    let tabSession = null;

    function invalidateTabSession() {
        tabSession = null;
    }

    function refreshHint() {
        const line = input.value;
        const caret = input.selectionStart ?? line.length;
        const matches = getTabCompletions(line, caret, deps);
        if (matches.length === 0) {
            hintEl.textContent = '';
            return;
        }
        if (matches.length === 1) {
            hintEl.textContent = `Tab → ${matches[0]}`;
            return;
        }
        const preview = matches.slice(0, 4).join(', ');
        const extra = matches.length > 4 ? ` (+${matches.length - 4} more)` : '';
        hintEl.textContent = `${matches.length} matches — Tab cycles: ${preview}${extra}`;
    }

    function appendLine(text, kind = 'info') {
        const line = document.createElement('div');
        line.className = `command-console__line command-console__line--${kind}`;
        line.textContent = text;
        output.appendChild(line);
        output.scrollTop = output.scrollHeight;
    }

    function setExpanded(on) {
        expanded = on;
        root.classList.toggle('command-console--expanded', on);
        toggle.setAttribute('aria-expanded', String(on));
        if (chevron) {
            chevron.textContent = on ? 'keyboard_arrow_down' : 'keyboard_arrow_up';
        }
        if (on) {
            input.focus();
            requestAnimationFrame(refreshHint);
        }
    }

    toggle.addEventListener('click', () => setExpanded(!expanded));

    appendLine(
        'Command Console ready. Type help; ↑/↓ history; Tab completes / cycles; hint shows options.',
        'info'
    );

    input.addEventListener('input', () => {
        historyNavIndex = -1;
        invalidateTabSession();
        refreshHint();
    });

    input.addEventListener('keyup', (e) => {
        if (e.key === 'Tab') return;
        refreshHint();
    });

    document.addEventListener('selectionchange', () => {
        if (document.activeElement !== input) return;
        const caret = input.selectionStart ?? 0;
        if (tabSession) {
            const inside = caret >= tabSession.start && caret <= tabSession.end;
            const atEnd = caret === tabSession.end;
            if (!inside || !atEnd) {
                invalidateTabSession();
            }
        }
        refreshHint();
    });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (historyList.length === 0) return;
            if (historyNavIndex === -1) {
                historyNavIndex = historyList.length - 1;
            } else {
                historyNavIndex = Math.max(0, historyNavIndex - 1);
            }
            input.value = historyList[historyNavIndex];
            invalidateTabSession();
            refreshHint();
            return;
        }

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (historyNavIndex === -1) return;
            if (historyNavIndex >= historyList.length - 1) {
                historyNavIndex = -1;
                input.value = '';
            } else {
                historyNavIndex += 1;
                input.value = historyList[historyNavIndex];
            }
            invalidateTabSession();
            refreshHint();
            return;
        }

        if (e.key === 'Tab') {
            e.preventDefault();
            const line = input.value;
            const caret = input.selectionStart ?? line.length;

            const atSessionSlot =
                tabSession &&
                tabSession.candidates.length > 0 &&
                caret === tabSession.end &&
                line.slice(tabSession.start, tabSession.end) === tabSession.candidates[tabSession.idx];

            if (atSessionSlot) {
                if (tabSession.candidates.length === 1) {
                    hintEl.textContent = `Tab → ${tabSession.candidates[0]} (only option)`;
                    return;
                }
                const nextIdx = (tabSession.idx + 1) % tabSession.candidates.length;
                const pick = tabSession.candidates[nextIdx];
                const newLine =
                    line.slice(0, tabSession.start) + pick + line.slice(tabSession.end);
                const newEnd = tabSession.start + pick.length;
                input.value = newLine;
                input.setSelectionRange(newEnd, newEnd);
                tabSession = {
                    start: tabSession.start,
                    end: newEnd,
                    candidates: tabSession.candidates,
                    idx: nextIdx
                };
                const cur = tabSession.candidates[tabSession.idx];
                hintEl.textContent = `${tabSession.idx + 1}/${tabSession.candidates.length}: ${cur} (Tab for next)`;
                return;
            }

            const matches = getTabCompletions(line, caret, deps);
            if (matches.length === 0) {
                invalidateTabSession();
                refreshHint();
                return;
            }

            const { start, end } = getCompletionSlot(line, caret);
            const pick = matches[0];
            const newLine = line.slice(0, start) + pick + line.slice(end);
            const newEnd = start + pick.length;
            input.value = newLine;
            input.setSelectionRange(newEnd, newEnd);
            tabSession = { start, end: newEnd, candidates: matches, idx: 0 };
            if (matches.length === 1) {
                hintEl.textContent = `Tab → ${pick} (only option)`;
            } else {
                hintEl.textContent = `1/${matches.length}: ${pick} (Tab for next)`;
            }
            return;
        }

        if (e.key !== 'Enter') return;
        e.preventDefault();
        const line = input.value.trim();
        if (!line) return;

        pushHistoryLine(historyList, line);
        historyNavIndex = -1;
        invalidateTabSession();
        hintEl.textContent = '';

        appendLine(`> ${line}`, 'cmd');
        input.value = '';

        dispatchConsoleLine(line, deps, appendLine).catch((err) => {
            const msg = err && err.message ? err.message : String(err);
            appendLine(`Error: ${msg}`, 'error');
            if (typeof log === 'function') {
                log(`[Command Console] ${msg}`, 'error');
            }
        });
    });

    refreshHint();
    _commandConsoleReady = true;
}

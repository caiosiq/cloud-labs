/**
 * Session notepad — operator scratchpad for measurements, TODOs, steps.
 *
 * Persisted in localStorage per selected backend. Not lab-state / edge.
 * Optional pop-out mirrors the live-feed floater pattern.
 */
import { getSelectedBackendId } from '../state/backend-selection.js';

const STORAGE_PREFIX = 'cloudlabs.sessionNotes.';
const SAVE_DEBOUNCE_MS = 400;

/** @type {string} */
let _text = '';
/** @type {string | null} */
let _storageBackendId = null;
/** @type {number | null} */
let _saveTimer = null;
/** @type {HTMLTextAreaElement | null} */
let _panelEditor = null;
/** @type {HTMLElement | null} */
let _statusEl = null;
/** @type {{ el: HTMLElement, editor: HTMLTextAreaElement } | null} */
let _popout = null;

function _storageKey(backendId = getSelectedBackendId()) {
    const id = String(backendId || 'default').trim() || 'default';
    return `${STORAGE_PREFIX}${id}`;
}

function _loadForBackend(backendId = getSelectedBackendId()) {
    const id = String(backendId || 'default').trim() || 'default';
    _storageBackendId = id;
    try {
        _text = localStorage.getItem(_storageKey(id)) || '';
    } catch (_) {
        _text = '';
    }
}

function _persistNow() {
    const key = _storageKey(_storageBackendId || getSelectedBackendId());
    try {
        localStorage.setItem(key, _text);
        _setStatus('Saved locally');
    } catch (_) {
        _setStatus('Save failed (storage full?)');
    }
}

function _scheduleSave() {
    _setStatus('Saving…');
    if (_saveTimer) clearTimeout(_saveTimer);
    _saveTimer = window.setTimeout(() => {
        _saveTimer = null;
        _persistNow();
    }, SAVE_DEBOUNCE_MS);
}

function _setStatus(msg) {
    if (_statusEl) _statusEl.textContent = msg;
    const popStatus = _popout?.el.querySelector('[data-notes-status]');
    if (popStatus) popStatus.textContent = msg;
}

function _syncEditors(except = null) {
    if (_panelEditor && _panelEditor !== except && _panelEditor.value !== _text) {
        const start = _panelEditor.selectionStart;
        const end = _panelEditor.selectionEnd;
        _panelEditor.value = _text;
        try {
            _panelEditor.setSelectionRange(start, end);
        } catch (_) {
            /* ignore */
        }
    }
    if (_popout?.editor && _popout.editor !== except && _popout.editor.value !== _text) {
        const start = _popout.editor.selectionStart;
        const end = _popout.editor.selectionEnd;
        _popout.editor.value = _text;
        try {
            _popout.editor.setSelectionRange(start, end);
        } catch (_) {
            /* ignore */
        }
    }
}

function _onEditorInput(editor) {
    _ensureBackendBound();
    _text = editor.value;
    _syncEditors(editor);
    _scheduleSave();
}

function _ensureBackendBound() {
    const id = String(getSelectedBackendId() || 'default').trim() || 'default';
    if (_storageBackendId === id) return;
    // Backend switched — flush current, load the other pad.
    if (_saveTimer) {
        clearTimeout(_saveTimer);
        _saveTimer = null;
        _persistNow();
    }
    _loadForBackend(id);
    _syncEditors();
    _setStatus('Loaded for this backend');
}

function _formatTimestamp() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    );
}

function insertTimestamp() {
    _ensureBackendBound();
    const stamp = `[${_formatTimestamp()}] `;
    const editor = document.activeElement === _popout?.editor ? _popout.editor : _panelEditor;
    if (!editor) {
        _text = _text ? `${_text}\n${stamp}` : stamp;
        _syncEditors();
        _scheduleSave();
        return;
    }
    const start = editor.selectionStart ?? _text.length;
    const end = editor.selectionEnd ?? start;
    const before = editor.value.slice(0, start);
    const after = editor.value.slice(end);
    const needNl = before.length > 0 && !before.endsWith('\n');
    const insert = `${needNl ? '\n' : ''}${stamp}`;
    editor.value = before + insert + after;
    const caret = before.length + insert.length;
    editor.focus();
    editor.setSelectionRange(caret, caret);
    _text = editor.value;
    _syncEditors(editor);
    _scheduleSave();
}

async function copyNotes() {
    _ensureBackendBound();
    try {
        await navigator.clipboard.writeText(_text);
        _setStatus('Copied');
    } catch (_) {
        _setStatus('Copy failed');
    }
}

function clearNotes() {
    if (_text.trim() && !window.confirm('Clear session notes for this backend?')) return;
    _ensureBackendBound();
    _text = '';
    _syncEditors();
    _persistNow();
    _setStatus('Cleared');
}

function _ensurePopoutLayer() {
    let layer = document.getElementById('session-notes-popout-layer');
    if (layer) return layer;
    layer = document.createElement('div');
    layer.id = 'session-notes-popout-layer';
    document.body.appendChild(layer);
    return layer;
}

function _makeDraggable(el, handle) {
    if (!handle) return;
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let origLeft = 0;
    let origTop = 0;
    handle.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('button')) return;
        dragging = true;
        startX = e.clientX;
        startY = e.clientY;
        origLeft = el.offsetLeft;
        origTop = el.offsetTop;
        handle.setPointerCapture(e.pointerId);
        e.preventDefault();
    });
    handle.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        el.style.left = `${Math.max(0, origLeft + e.clientX - startX)}px`;
        el.style.top = `${Math.max(0, origTop + e.clientY - startY)}px`;
    });
    handle.addEventListener('pointerup', () => {
        dragging = false;
    });
    handle.addEventListener('pointercancel', () => {
        dragging = false;
    });
}

export function isNotesPopoutOpen() {
    return Boolean(_popout);
}

export function closeNotesPopout() {
    if (!_popout) return;
    _popout.el.remove();
    _popout = null;
}

export function openNotesPopout() {
    _ensureBackendBound();
    if (_popout) {
        _popout.el.classList.add('session-notes-popout--flash');
        _popout.editor.focus();
        setTimeout(() => _popout?.el.classList.remove('session-notes-popout--flash'), 400);
        return;
    }

    const layer = _ensurePopoutLayer();
    const el = document.createElement('div');
    el.className = 'session-notes-popout';
    el.style.left = '48px';
    el.style.top = '96px';
    el.style.width = `${Math.min(420, Math.round((window.innerWidth || 1280) * 0.36))}px`;
    el.style.height = `${Math.min(480, Math.round((window.innerHeight || 800) * 0.55))}px`;
    el.innerHTML = `
        <div class="session-notes-popout__header" data-drag-handle>
            <span class="session-notes-popout__title">
                <span class="material-icons-round" aria-hidden="true">edit_note</span>
                Notes
            </span>
            <span class="session-notes-popout__actions">
                <button type="button" class="session-notes-popout__btn" data-action="stamp" title="Insert timestamp">Time</button>
                <button type="button" class="session-notes-popout__btn" data-action="copy" title="Copy all">Copy</button>
                <button type="button" class="session-notes-popout__btn session-notes-popout__btn--ghost" data-action="close" title="Hide pop-out">Hide</button>
            </span>
        </div>
        <textarea class="session-notes-popout__editor" spellcheck="true"
            placeholder="Measurements, checklist, next steps…"></textarea>
        <div class="session-notes-popout__footer">
            <span data-notes-status>Saved locally</span>
        </div>
    `;

    const editor = el.querySelector('.session-notes-popout__editor');
    editor.value = _text;
    editor.addEventListener('input', () => _onEditorInput(editor));

    el.querySelector('[data-action="close"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        closeNotesPopout();
    });
    el.querySelector('[data-action="stamp"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        editor.focus();
        insertTimestamp();
    });
    el.querySelector('[data-action="copy"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        void copyNotes();
    });

    _makeDraggable(el, el.querySelector('[data-drag-handle]'));
    layer.appendChild(el);
    _popout = { el, editor };
    editor.focus();
}

/** Call when the Notes workspace tab becomes active (backend may have changed). */
export function onNotesTabActivated() {
    _ensureBackendBound();
    _syncEditors();
}

export function initSessionNotes() {
    _panelEditor = document.getElementById('session-notes-editor');
    _statusEl = document.getElementById('session-notes-status');
    if (!_panelEditor) return;

    _loadForBackend();
    _panelEditor.value = _text;
    _panelEditor.addEventListener('input', () => _onEditorInput(_panelEditor));
    _panelEditor.addEventListener('focus', () => _ensureBackendBound());

    document.getElementById('session-notes-stamp')?.addEventListener('click', () => {
        _panelEditor?.focus();
        insertTimestamp();
    });
    document.getElementById('session-notes-copy')?.addEventListener('click', () => {
        void copyNotes();
    });
    document.getElementById('session-notes-clear')?.addEventListener('click', () => {
        clearNotes();
    });
    document.getElementById('session-notes-popout')?.addEventListener('click', () => {
        openNotesPopout();
    });

    window.addEventListener('beforeunload', () => {
        if (_saveTimer) {
            clearTimeout(_saveTimer);
            _saveTimer = null;
            _persistNow();
        }
    });
}

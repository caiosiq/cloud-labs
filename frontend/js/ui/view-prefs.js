/**
 * Twin view preferences for paper screenshots and visual style (localStorage).
 */
const KEYS = {
    hideStorage: 'cloudlabs.twin.view.hideStorage',
    hideLabels: 'cloudlabs.twin.view.hideLabels',
    compactChrome: 'cloudlabs.twin.view.compactChrome',
    visualStyle: 'cloudlabs.twin.view.visualStyle',
};

function readBool(key, fallback = false) {
    try {
        const v = localStorage.getItem(key);
        if (v === null) return fallback;
        return v === '1' || v === 'true';
    } catch {
        return fallback;
    }
}

function writeBool(key, value) {
    try {
        localStorage.setItem(key, value ? '1' : '0');
    } catch {
        /* ignore */
    }
}

function readVisualStyle() {
    try {
        const v = localStorage.getItem(KEYS.visualStyle);
        return v === 'v3' ? 'v3' : 'v2';
    } catch {
        return 'v2';
    }
}

function writeVisualStyle(style) {
    try {
        localStorage.setItem(KEYS.visualStyle, style === 'v3' ? 'v3' : 'v2');
    } catch {
        /* ignore */
    }
}

const state = {
    hideStorage: readBool(KEYS.hideStorage, false),
    hideLabels: readBool(KEYS.hideLabels, false),
    compactChrome: readBool(KEYS.compactChrome, false),
    visualStyle: readVisualStyle(),
};

const listeners = new Set();

function notify() {
    listeners.forEach((fn) => {
        try {
            fn({ ...state });
        } catch (e) {
            console.warn('[view-prefs] listener failed', e);
        }
    });
    document.body.classList.toggle('twin-hide-storage', state.hideStorage);
    document.body.classList.toggle('twin-hide-labels', state.hideLabels);
    document.body.classList.toggle('twin-compact-chrome', state.compactChrome);
    document.body.classList.toggle('twin-style-v3', state.visualStyle === 'v3');
    window.dispatchEvent(new CustomEvent('cloudlabs:viewprefs', { detail: { ...state } }));
    window.dispatchEvent(new Event('cloudlabs:layout'));
}

export function getViewPrefs() {
    return { ...state };
}

export function getVisualStyle() {
    return state.visualStyle;
}

export function isTwinStyleV3() {
    return state.visualStyle === 'v3';
}

export function setViewPref(key, value) {
    if (!(key in state)) return;
    if (key === 'visualStyle') {
        state.visualStyle = value === 'v3' ? 'v3' : 'v2';
        writeVisualStyle(state.visualStyle);
    } else {
        state[key] = !!value;
        writeBool(KEYS[key], state[key]);
    }
    notify();
}

export function toggleViewPref(key) {
    if (key === 'visualStyle') {
        setViewPref('visualStyle', state.visualStyle === 'v3' ? 'v2' : 'v3');
        return;
    }
    setViewPref(key, !state[key]);
}

export function onViewPrefsChange(fn) {
    if (typeof fn === 'function') listeners.add(fn);
    return () => listeners.delete(fn);
}

function wireToggle(btnId, key) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    const sync = () => {
        const on = !!state[key];
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.classList.toggle('is-active', on);
    };
    sync();
    btn.addEventListener('click', () => {
        toggleViewPref(key);
        sync();
    });
    onViewPrefsChange(sync);
}

function wireStyleToggle(btnId) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    const sync = () => {
        const on = state.visualStyle === 'v3';
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.classList.toggle('is-active', on);
    };
    sync();
    btn.addEventListener('click', () => {
        toggleViewPref('visualStyle');
        sync();
    });
    onViewPrefsChange(sync);
}

/** Bind bottom-chrome view buttons and apply body classes. */
export function initViewPrefs() {
    notify();
    wireToggle('view-pref-hide-storage', 'hideStorage');
    wireToggle('view-pref-hide-labels', 'hideLabels');
    wireToggle('view-pref-compact-chrome', 'compactChrome');
    wireStyleToggle('view-pref-studio-v3');
}

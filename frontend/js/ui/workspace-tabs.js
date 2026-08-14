import { store } from '../state/store.js';

const TAB_NAMES = ['optimization', 'recipes', 'component'];

let activeTab = 'optimization';
let lastNonComponentTab = 'optimization';

function tabButton(name) {
    return document.getElementById(`workspace-tab-${name}`);
}

function tabPanel(name) {
    return document.getElementById(`workspace-panel-${name}`);
}

export function activateWorkspaceTab(name) {
    if (!TAB_NAMES.includes(name)) return;
    const button = tabButton(name);
    const panel = tabPanel(name);
    if (!button || !panel || button.hidden) return;

    TAB_NAMES.forEach((tabName) => {
        const candidateButton = tabButton(tabName);
        const candidatePanel = tabPanel(tabName);
        const selected = tabName === name;
        if (candidateButton) candidateButton.setAttribute('aria-selected', String(selected));
        if (candidatePanel) candidatePanel.classList.toggle('is-active', selected);
    });

    activeTab = name;
    if (name !== 'component') lastNonComponentTab = name;
}

export function showSelectedPartTab() {
    const button = tabButton('component');
    if (!button) return;
    button.hidden = false;
    const count = store.openPanels.length;
    button.textContent = count > 1 ? `Selected Parts (${count})` : 'Selected Part';
    activateWorkspaceTab('component');
}

export function hideSelectedPartTab() {
    const button = tabButton('component');
    if (button) {
        button.hidden = true;
        button.textContent = 'Selected Part';
    }
    if (activeTab === 'component') activateWorkspaceTab(lastNonComponentTab);
}

export function initWorkspaceTabs() {
    TAB_NAMES.forEach((name) => {
        const button = tabButton(name);
        if (!button) return;
        button.addEventListener('click', () => activateWorkspaceTab(name));
    });
    activateWorkspaceTab('optimization');
}

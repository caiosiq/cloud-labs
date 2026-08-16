/**
 * Read-only backend API fetchers.
 * Each fetcher updates {@link store} and optionally triggers a UI follow-up
 * via callbacks registered with {@link initApiFetchers}.
 *
 * Endpoints mounted in `backend/lab_communicator/api/server.py`:
 *   GET /api/catalog
 *   GET /api/storage-grid
 *   GET /api/layout-conflicts
 *   GET /api/recipes
 */
import { store } from '../state/store.js';
import { withBackendQuery } from '../state/backend-selection.js';

let _onCatalogLoaded = () => {};
let _onRecipesLoaded = () => {};

/**
 * Wire post-fetch UI callbacks. Call once at app boot.
 * @param {{
 *   onCatalogLoaded?: () => void,
 *   onRecipesLoaded?: () => void,
 * }} cbs
 */
export function initApiFetchers(cbs = {}) {
    if (cbs.onCatalogLoaded) _onCatalogLoaded = cbs.onCatalogLoaded;
    if (cbs.onRecipesLoaded) _onRecipesLoaded = cbs.onRecipesLoaded;
}

function mergeCatalogRows(rows) {
    if (!Array.isArray(rows)) return;
    rows.forEach((item) => {
        if (item && item.tag_id) {
            store.catalogMap[item.tag_id] = item;
        }
    });
}

export async function fetchCatalogMap() {
    try {
        const [catalogRes, activeRes, libraryRes] = await Promise.all([
            fetch(withBackendQuery('/api/catalog')),
            fetch(withBackendQuery('/api/catalog/active-tags')),
            fetch(withBackendQuery('/api/catalog/library-rows')),
        ]);
        store.catalogMap = {};
        if (libraryRes.ok) {
            mergeCatalogRows(await libraryRes.json());
        }
        if (catalogRes.ok) {
            mergeCatalogRows(await catalogRes.json());
        }
        if (libraryRes.ok || catalogRes.ok) {
            console.log('Catalog Loaded:', store.catalogMap);
        }
        if (activeRes.ok) {
            const active = await activeRes.json();
            store.activeCatalogTags = Array.isArray(active.tag_ids) ? active.tag_ids : [];
            store.libraryTagIds = Array.isArray(active.library_tag_ids)
                ? active.library_tag_ids
                : [];
        } else {
            store.activeCatalogTags = [];
            store.libraryTagIds = [];
        }
        if (libraryRes.ok || catalogRes.ok) {
            _onCatalogLoaded();
        }
    } catch (e) {
        console.error('Catalog fetch failed', e);
    }
}

export async function fetchStorageGridSpec() {
    try {
        const response = await fetch(withBackendQuery('/api/storage-grid'));
        if (response.ok) {
            store.storageGridSpec = await response.json();
        }
    } catch (e) {
        console.warn('storage-grid fetch failed', e);
    }
}

export async function fetchLayoutConflicts() {
    try {
        const response = await fetch(withBackendQuery('/api/layout-conflicts'));
        if (!response.ok) {
            store.layoutIssues = [];
            return;
        }
        const data = await response.json();
        store.layoutIssues = data.issues || [];
    } catch (e) {
        console.warn('layout-conflicts fetch failed', e);
        store.layoutIssues = [];
    }
}

export async function fetchRecipes() {
    try {
        const response = await fetch(withBackendQuery('/api/recipes'));
        if (response.ok) {
            store.availableRecipes = await response.json();
            _onRecipesLoaded();
        }
    } catch (e) {
        console.error('Failed to fetch recipes', e);
    }
}

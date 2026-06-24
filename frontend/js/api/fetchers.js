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
 *
 * Strategies are currently hard-coded client-side (no /api/strategies yet).
 */
import { store } from '../state/store.js';

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

export async function fetchCatalogMap() {
    try {
        const response = await fetch('/api/catalog');
        if (response.ok) {
            const catalog = await response.json();
            store.catalogMap = {};
            catalog.forEach((item) => {
                store.catalogMap[item.tag_id] = item;
            });
            console.log('Catalog Loaded:', store.catalogMap);
            _onCatalogLoaded();
        }
    } catch (e) {
        console.error('Catalog fetch failed', e);
    }
}

/**
 * Strategy metadata — hard-coded for now (backend keeps the canonical list, but the UI
 * doesn't yet pull it). Mutates `store.availableStrategies` synchronously.
 */
export async function fetchStrategies() {
    store.availableStrategies = {
        NEWTON: {
            name: 'Newton Strategy',
            description: 'Aligns a component by minimizing beam deviation.',
            parameters: {
                camera_number: { type: 'integer', default: 2, description: 'Target Camera ID' },
                target_x_pixel: { type: 'integer', default: 2744, description: 'Target X (pixel)' },
                axis: { type: 'string', enum: ['x', 'y'], default: 'x', description: 'Axis' },
                tolerance_ratio: { type: 'float', default: 0.1, description: 'Tolerance' },
                exposure: {
                    type: 'float',
                    default: 0.2,
                    description: 'Exposure (s) for strategy camera captures',
                },
            },
        },
        COBYLA: {
            name: 'Cobyla Alignment',
            description: 'Constrained Optimization by Linear Approximation.',
            parameters: {
                objective_threshold: { type: 'float', default: 100.0, description: 'Threshold' },
                exposure: {
                    type: 'float',
                    default: 0.2,
                    description: 'Exposure (s) for strategy camera video + capture',
                },
            },
        },
    };
}

export async function fetchStorageGridSpec() {
    try {
        const response = await fetch('/api/storage-grid');
        if (response.ok) {
            store.storageGridSpec = await response.json();
        }
    } catch (e) {
        console.warn('storage-grid fetch failed', e);
    }
}

export async function fetchLayoutConflicts() {
    try {
        const response = await fetch('/api/layout-conflicts');
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
        const response = await fetch('/api/recipes');
        if (response.ok) {
            store.availableRecipes = await response.json();
            _onRecipesLoaded();
        }
    } catch (e) {
        console.error('Failed to fetch recipes', e);
    }
}

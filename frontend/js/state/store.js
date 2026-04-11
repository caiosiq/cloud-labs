/** Mutable application state (single source for lab UI). */
export const store = {
    labState: null,
    ghostState: {},
    draggingComponent: null,
    isDragging: false,
    dragOffset: { x: 0, y: 0 },
    pendingCommands: new Set(),
    selectedComponent: null,
    availableStrategies: null,
    availableRecipes: [],
    laserLineCoeffs: null,
    previousSystemStatus: 'IDLE',
    forceGhostSync: false,
    isOptimizing: false,
    isOptimizingFeedActive: false,
    optimizationData: [],
    isRecording: false,
    currentRecipeSteps: [],
    catalogMap: {},
    selectedTableCam: 1,
    /** Seconds; used for GET /api/table-cam/capture exposure query param */
    tableCamExposure: 0.2,
    tableCamLastBlobUrl: null,
    /** Object URL for Cobyla reference preview image (revoked when clearing/updating). */
    cobylaRefPreviewObjectUrl: null,
    /** From GET /api/layout-conflicts */
    layoutIssues: [],
    /** From GET /api/storage-grid (inventory cell overlay) */
    storageGridSpec: null,
    /** Last `state` (PLACED/STORED/…) shown in the context panel for the selected part — used to refresh controls when lab state updates. */
    contextPanelStateSnapshot: null,
    /** STORED part id when "Drag from storage" mode is active (only that part can be dragged to place). */
    dragFromStorageTag: null,
    /** Ghost pose snapshot at mousedown when starting a drag-from-storage move (for cancel/revert). */
    dragFromStorageStartPose: null,
};

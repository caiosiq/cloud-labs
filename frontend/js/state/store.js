/** Mutable application state (single source for lab UI). */
export const store = {
    labState: null,
    ghostState: {},
    draggingComponent: null,
    isDragging: false,
    dragOffset: { x: 0, y: 0 },
    pendingCommands: new Set(),
    /**
     * Last action (e.g. ``HOVER``, ``MOVE_COMPONENT``) that was sent for a
     * given target_id and is still pending. Used purely for rendering: lets
     * the canvas show "HOVERING..." in purple instead of the generic orange
     * "MOVING..." pill while an in-air command is in flight. Entries are
     * added alongside ``pendingCommands`` in ``executeSendCommand`` and
     * cleared whenever the tag is removed from ``pendingCommands``.
     */
    pendingActions: new Map(),
    selectedComponent: null,
    availableStrategies: null,
    availableRecipes: [],
    /** From GET /api/laser-lines (per LAB_MODE schema). */
    laserLinesDoc: null,
    /** Legacy single-line coeffs for snap fallback / logging (derived from doc). */
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
    /**
     * Composite snapshot of top-level status fields that also cause the
     * context panel to rebuild (even when the selected component's placement
     * label is unchanged). Currently covers `system_status`, the currently
     * held tag and the `requires_operator_confirm` flag so that IDLE → HOLDING
     * and HOLDING → IDLE transitions immediately swap the in-air controls.
     */
    contextPanelStatusSnapshot: null,
    /** STORED part id when "Drag from storage" mode is active (only that part can be dragged to place). */
    dragFromStorageTag: null,
    /** Ghost pose snapshot at mousedown when starting a drag-from-storage move (for cancel/revert). */
    dragFromStorageStartPose: null,
};

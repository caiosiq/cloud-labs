/** Mutable application state (single source for lab UI). */
export const store = {
    labState: null,
    ghostState: {},
    draggingComponent: null,
    /** Lab mm pose of the dragged component at mousedown (for Shift = H/V axis lock). */
    dragComponentStartLab: null,
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
    /** Per CAM (1/2): CONNECT lifecycle (lazy cloud recorder + mock parity). */
    tableCamConnected: { 1: false, 2: false },
    /** Optimistic / in-flight CONNECT (grey "Connecting" button). */
    tableCamConnecting: { 1: false, 2: false },
    /** STREAM_ON semantics per CAM — drives `/api/table-cam/stream`. */
    tableCamLive: { 1: false, 2: false },
    /** Optimistic live toggle in flight (reconcile on server response). */
    tableCamLivePending: { 1: false, 2: false },
    /** Capture in progress (Still + "Capturing…" shown immediately). */
    tableCamCapturePending: { 1: false, 2: false },
    /** From GET /api/table-cam/status: real | mock | none */
    tableCamHardware: { 1: 'none', 2: 'none' },
    tableCamLastError: { 1: null, 2: null },
    tableCamRecorderAlive: true,
    tableCamRecorderVariant: 'cloudlab',
    /** Seconds; used for GET /api/table-cam/capture exposure query param */
    tableCamExposure: 0.2,
    /** Per CAM: Object URL from last PNG capture (`URL.createObjectURL`) */
    tableCamLastBlobUrl: { 1: null, 2: null },
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
    /**
     * User-drawn alignment guides (client-side segments in lab mm). Persisted
     * to localStorage — same snap rules as laser line segments.
     */
    guideLines: [],
    /** While drawing a new guide: `{ startLab, currentLab }` in mm (Shift = H/V). */
    guideDraw: null,
    /** Pencil tool: draw guides on empty canvas; does not block selecting parts. */
    pencilToolActive: false,
    /** One-shot gate for GET /api/session-reconciliation/offers after first IDLE poll. */
    sessionReconciliationFetched: false,
};

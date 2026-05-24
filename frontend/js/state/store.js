/** Mutable application state (single source for lab UI). */
export const store = {
    labState: null,
    /**
     * Ghost (canvas-truth) pose per tag: ``{x, y, rotation}``.
     * TeleOp v2 planning uses ``teleopTarget``; hardware truth uses ``teleopLivePose``.
     */
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
    /** Tags that were teleop-ready on the previous lab-state poll (ghost sync on exit). */
    previousTeleopReadyTags: new Set(),
    forceGhostSync: false,
    isOptimizing: false,
    isOptimizingFeedActive: false,
    optimizationData: [],
    isRecording: false,
    currentRecipeSteps: [],
    /**
     * Catalog rows keyed by ``tag_id``. Populated by
     * ``fetchCatalogMap()`` at boot from ``GET /api/catalog``. After
     * Phase 5, each row carries a ``capabilities`` block (tunables /
     * measurables / telemetry / primitives) that drives the symmetric
     * per-component viewer in Phase 7 — see
     * ``frontend/js/ui/component-viewer.js``.
     */
    catalogMap: {},
    /** Default camera exposure (seconds) for COBYLA/NEWTON when command omits `exposure`. */
    defaultCameraExposureSec: 0.02,
    // Phase 9d removed the table-cam dock and its store fields.
    // Phase 9a deleted the legacy Cobyla-reference preview object URL
    // (the field tracked the URL.createObjectURL handle for the dropped
    // red-bordered preview slot); the per-component camera viewer reads
    // the latest captured PNG from measurables.camera_image instead.
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
    /**
     * JSON snapshot of the selected component's tunables + measurables.
     * When this changes, the context panel re-renders so read-only widgets
     * and primitive regions (e.g. TELEOP) stay in sync without re-clicking.
     */
    contextPanelDataSnapshot: null,
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

    /** Live hardware pose from TeleOp poll (not lab_state JSON). */
    teleopLivePose: {},
    /** Operator target preview while planning a TeleOp goto. */
    teleopTarget: {},
    /** Per-tag TeleOp motion speed. */
    teleopSpeed: {},
    /**
     * In-flight motor commands keyed by ``setpoint:tagId:motorId`` or ``jog:tagId:motorId``.
     */
    motorActionApply: {},
    /** Optional render hook set by canvas init. */
    _teleopLivePoseRender: null,
};

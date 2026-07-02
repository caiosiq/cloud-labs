/** Mutable application state (single source for lab UI). */
export const store = {
    labState: null,
    runtimeMode: null,
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
    /**
     * Multi-panel state.
     *
     * ``openPanels`` — ordered list of currently-open component tags.
     * Insertion order matches click order; with the dock's
     * ``flex-direction: row-reverse`` styling, additional panels opened
     * via Ctrl/Cmd+click appear to the LEFT of the first one (matching
     * the operator request "add another window to the left").
     *
     * ``focusedPanel`` — the one panel that captures interactive clicks
     * (buttons, inputs). All other open panels stay visible and scrollable
     * so live camera feeds keep rendering, but their controls are gated
     * with ``pointer-events: none`` until focused.
     *
     * ``selectedComponent`` (below, installed as a property descriptor)
     * is a backwards-compatibility shim that reads ``focusedPanel`` and
     * on write replaces ``openPanels`` with ``[value]`` (legacy "click
     * replaces" semantics). New code should call into the panel-dock
     * manager (``openPanel`` / ``focusPanel`` / ``closePanel``) directly.
     */
    openPanels: [],
    focusedPanel: null,
    /**
     * Per-tag snapshot bag (``{ state, status, data }``) that drives the
     * "rebuild this panel because something changed" check in lab-state.js.
     * Keyed by ``tagId``. Populated and consumed only by ``ui/context-panel.js``
     * and ``state/lab-state.js`` — every other reader should go through the
     * panel-dock manager.
     */
    contextPanelSnapshots: new Map(),
    availableStrategies: null,
    availableRecipes: [],
    /** From GET /api/laser-lines (per LAB_MODE schema). */
    laserLinesDoc: null,
    /** Legacy single-line coeffs for snap fallback / logging (derived from doc). */
    laserLineCoeffs: null,
    previousSystemStatus: 'IDLE',
    previousRuntimeErrorKey: null,
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
    /** Tag ids in ``active_catalog.json`` (operator-controlled parts). */
    activeCatalogTags: [],
    /** All tag ids declared in ``component_library.json``. */
    libraryTagIds: [],
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
    /** Currently selected guide id (click-to-select), or null. */
    selectedGuideId: null,
    /** While moving an existing guide: `{ id, mouseLab, p1, p2 }` (orig endpoints). */
    guideDrag: null,
    /** While resizing a selected guide: `{ id, endpoint, origP1, origP2 }`. */
    guideEndpointDrag: null,
    /** One-shot gate for GET /api/session-reconciliation/offers after first IDLE poll. */
    sessionReconciliationFetched: false,

    /**
     * Configuration version control (ControlManager UI).
     * @see docs/CONTROL_RUNTIME_AND_VERSIONING.md
     */
    control: {
        // null = version control not started yet (opt-in). The user picks a repo
        // via "Start version control" before any control APIs are called.
        repoId: null,
        repos: [],
        branch: 'main',
        heads: {},
        liveHeadId: null,
        viewingCommitId: null,
        selectedCommitId: null,
        // Read-only preview overlay: the configuration of the node currently
        // being VIEWED (soft checkout). The live bench (store.labState) is never
        // mutated by preview; the canvas renders this overlay instead while set.
        previewConfig: null,
        nodes: [],
        graphNodes: [],
        loading: false,
        // Single mirror of the backend `status.working` payload — the one source
        // of truth for the Git-like working tree: the applied pointer, on_head /
        // detached / dirty flags, the stash summary, and the action capabilities.
        // Never write its fields directly; go through control-state.js accessors
        // (getAppliedCommitId, isDirty, isDetached, hasStash, controlCapabilities).
        working: null,
        /**
         * Live reconcile execution progress (Apply on bench / stash / pop).
         * While active, the config-view badge shows the step list and the orange
         * preview chrome stays up until the run finishes.
         * @type {null|{
         *   active: boolean,
         *   title: string,
         *   commitId?: string|null,
         *   steps: Array<{ id: string, label: string, status: 'pending'|'active'|'done'|'error' }>,
         *   currentIndex: number,
         * }}
         */
        reconcileProgress: null,
    },

    /** Live hardware pose from TeleOp poll (not lab_state JSON). */
    teleopLivePose: {},
    /** Operator target preview while planning a TeleOp goto. */
    teleopTarget: {},
    /**
     * After START_TELEOP, sync ``teleopTarget`` from the first live CURRENT
     * poll (hardware truth) instead of nominal/meas layout seed.
     */
    teleopTargetAwaitingLive: {},
    /** Per-tag TeleOp motion speed. */
    teleopSpeed: {},
    /**
     * In-flight motor commands keyed by ``setpoint:tagId:motorId`` or ``jog:tagId:motorId``.
     */
    motorActionApply: {},
    /** Optional render hook set by canvas init. */
    _teleopLivePoseRender: null,
};

/**
 * ``store.selectedComponent`` — backwards-compatible shim.
 *
 * Reads return the focused panel's tag id (or ``null``). Writes apply the
 * legacy single-panel "click replaces" semantics:
 *   - ``store.selectedComponent = null``  → clear every panel.
 *   - ``store.selectedComponent = "tag"`` → if the tag is already open, just
 *     focus it; otherwise replace the open set with ``["tag"]`` and focus it.
 *
 * Every existing call site (canvas/interaction.js, ui/updateUI.js,
 * ui/bench-chrome-bar.js, etc.) keeps working through this shim. New code
 * paths should call ``openPanel`` / ``focusPanel`` directly via the
 * panel-dock manager in ``ui/context-panel.js``.
 */
Object.defineProperty(store, 'selectedComponent', {
    enumerable: true,
    configurable: true,
    get() {
        return this.focusedPanel;
    },
    set(value) {
        if (value == null) {
            this.openPanels = [];
            this.focusedPanel = null;
            this.contextPanelSnapshots.clear();
            return;
        }
        const tag = String(value);
        if (this.openPanels.includes(tag)) {
            this.focusedPanel = tag;
            return;
        }
        this.openPanels = [tag];
        this.focusedPanel = tag;
        const stale = [...this.contextPanelSnapshots.keys()].filter((k) => k !== tag);
        stale.forEach((k) => this.contextPanelSnapshots.delete(k));
    },
});

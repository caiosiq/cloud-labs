// Optical Digital Twin — ES module bundle (see js/bootstrap.js)
import {
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    LAB_X_MIN,
    LAB_X_MAX,
    LAB_Y_MIN,
    LAB_Y_MAX,
    LAB_SCALE,
    LAB_CENTER_PX,
    BREADBOARD_GRID_OFFSET_X_MM,
    POLLING_INTERVAL,
} from './config.js';
import { mmToPx, pxToMm } from './canvas/coordinates.js';
import { store } from './state/store.js';

console.log('App main module loading...');

// DOM Elements
const canvas = document.getElementById('optical-table');
const ctx = canvas.getContext('2d');
const logOutput = document.getElementById('log-output');
const statusBadge = document.getElementById('system-status-badge');
const componentList = document.getElementById('component-list');
const libraryPopup = document.getElementById('library-popup');
const libraryList = document.getElementById('library-list');
const addComponentBtn = document.getElementById('add-component-btn');
const libraryClose = document.getElementById('library-close');
const refreshBtn = document.getElementById('refresh-btn');
const saveStateBtn = document.getElementById('save-state-btn');
const loadStateBtn = document.getElementById('load-state-btn');
const sidebar = document.getElementById('sidebar');
const recipeList = document.getElementById('recipe-list');
// const runExpBtn = document.getElementById('run-exp-btn'); // Removed

// Context Panel Elements (Left Sidebar)
const contextPanel = document.getElementById('context-panel');
const selectedCompName = document.getElementById('selected-comp-name');
const selectedCompProperties = document.getElementById('selected-comp-properties');
const ctxX = document.getElementById('ctx-x');
const ctxY = document.getElementById('ctx-y');
const ctxRot = document.getElementById('ctx-rot');
const ctxMoveBtn = document.getElementById('ctx-move-btn');
const ctxStrategies = document.getElementById('ctx-strategies');

// Recipe UI Elements (Right Sidebar)
const recordBtn = document.getElementById('record-btn');
const recipePanel = document.getElementById('recipe-panel');
const recipeEditorName = document.getElementById('recipe-editor-name');
const recipeStepsContainer = document.getElementById('recipe-steps-container');
const recipeEditorSave = document.getElementById('recipe-editor-save');
const recipeEditorCancel = document.getElementById('recipe-editor-cancel');
const recIndicator = document.getElementById('rec-indicator');

// Unified Panel Elements (Right Sidebar - now static)
const videoImg = document.getElementById('live-video-img');
const videoPlaceholder = document.getElementById('video-placeholder');
const videoStatus = document.getElementById('video-status');


// --- 1. Networking ---

// We need the catalog to map IDs to Names (store.catalogMap)

async function fetchCatalogMap() {
    try {
        const response = await fetch('/api/catalog');
        if (response.ok) {
            const catalog = await response.json();
            catalog.forEach(item => {
                store.catalogMap[item.tag_id] = item;
            });
            console.log("Catalog Loaded:", store.catalogMap);
            // Re-render UI once catalog is loaded to update names
            updateUI();
        }
    } catch (e) { console.error("Catalog fetch failed", e); }
}

// Call this early
fetchCatalogMap();
fetchLaserLine();

async function fetchStrategies() {
    store.availableStrategies = {
      "NEWTON": {
        "name": "Newton Strategy",
        "description": "Aligns a component by minimizing beam deviation.",
        "parameters": {
          "camera_number": { "type": "integer", "default": 2, "description": "Target Camera ID" },
          "target_x_pixel": { "type": "integer", "default": 2744, "description": "Target X (pixel)" },
          "axis": { "type": "string", "enum": ["x", "y"], "default": "x", "description": "Axis" },
          "tolerance_ratio": { "type": "float", "default": 0.1, "description": "Tolerance" }
        }
      },
      "COBYLA": {
        "name": "Cobyla Alignment",
        "description": "Constrained Optimization by Linear Approximation.",
        "parameters": {
          "objective_threshold": { "type": "float", "default": 100.0, "description": "Threshold" }
        }
      }
    };
}

async function fetchLaserLine() {
    try {
        const response = await fetch('/api/laser-line');
        if (response.ok) {
            store.laserLineCoeffs = await response.json();
            console.log("Laser line:", store.laserLineCoeffs.source, store.laserLineCoeffs.loaded !== false ? "a=" + store.laserLineCoeffs.a + " b=" + store.laserLineCoeffs.b : "(defaults)");
        }
    } catch (e) {
        console.error("Laser line fetch failed", e);
    }
}

async function fetchRecipes() {
    try {
        const response = await fetch('/api/recipes');
        if (response.ok) {
            store.availableRecipes = await response.json();
            renderRecipes();
        }
    } catch (e) {
        console.error("Failed to fetch recipes", e);
    }
}

async function fetchLabState() {
    try {
        console.log(`[${new Date().toLocaleTimeString()}] Requesting Lab State...`);
        const response = await fetch('/api/lab-state');
        if (!response.ok) {
            // Try to parse error detail from backend
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errData = await response.json();
                if (errData.detail) errorMsg = errData.detail;
            } catch (e) { /* ignore JSON parse error */ }
            console.error(`[${new Date().toLocaleTimeString()}] Error receiving Lab State: ${errorMsg}`);
            throw new Error(errorMsg);
        }
        
        store.labState = await response.json();
        console.log(`[${new Date().toLocaleTimeString()}] Received Lab State successfully.`);
        
        // Clear error modal if it was open (recovery)
        const existingError = document.getElementById('error-modal');
        if (existingError) existingError.remove();

        // Initial Sync: Use INTENT if available, else Physical Pose
        if (store.labState.components) {
            // Determine if we should sync ghost state from backend
            // 1. Force Sync (Refresh button)
            // 2. System status transitioned from BUSY/OPTIMIZING to IDLE (Command finished)
            // 3. Initial Load (handled by !store.ghostState check)
            
            const justFinishedCommand = (store.previousSystemStatus !== 'IDLE' && store.labState.system_status === 'IDLE');
            const shouldSync = store.forceGhostSync || justFinishedCommand;

            if (shouldSync) {
                 log("Syncing ghost state with lab state...", "info");
            }

            Object.entries(store.labState.components).forEach(([name, comp]) => {
                if (comp.state === 'PLACED') {
                    // Always initialize if missing (first load)
                    if (!store.ghostState[name]) {
                        if (comp.intent && comp.intent.nominal_pose) {
                            store.ghostState[name] = { ...comp.intent.nominal_pose };
                        } else {
                            store.ghostState[name] = { ...comp.pose };
                        }
                        // Ensure rotation
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = comp.pose.rotation || 0;
                        }
                    }
                    // During real Newton-style optimization the backend updates intent.nominal_pose before each
                    // sub-move (ghost) and pose after the place completes (solid). Sync ghost every poll while OPTIMIZING.
                    else if (store.labState.system_status === 'OPTIMIZING' && !store.isDragging && comp.intent && comp.intent.nominal_pose) {
                        store.ghostState[name] = { ...comp.intent.nominal_pose };
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = comp.pose.rotation || 0;
                        }
                        if (store.selectedComponent === name && document.getElementById('ctx-x')) {
                            ctxX.value = store.ghostState[name].x.toFixed(1);
                            ctxY.value = store.ghostState[name].y.toFixed(1);
                            ctxRot.value = store.ghostState[name].rotation.toFixed(1);
                        }
                    }
                    // Otherwise only sync if allowed (command finished or forced refresh)
                    else if (shouldSync && !store.isDragging) {
                        if (comp.intent && comp.intent.nominal_pose) {
                            store.ghostState[name] = { ...comp.intent.nominal_pose };
                        } else {
                            store.ghostState[name] = { ...comp.pose };
                        }
                        
                        // Ensure rotation
                        if (typeof store.ghostState[name].rotation !== 'number') {
                            store.ghostState[name].rotation = comp.pose.rotation || 0;
                        }

                        // If selected, update UI inputs immediately
                        if (store.selectedComponent === name) {
                            if (document.getElementById('ctx-x')) {
                                ctxX.value = store.ghostState[name].x.toFixed(1);
                                ctxY.value = store.ghostState[name].y.toFixed(1);
                                ctxRot.value = store.ghostState[name].rotation.toFixed(1);
                            }
                        }
                    }
                }
            });

            // Reset flags
            if (shouldSync) store.forceGhostSync = false;
        }
        
        store.previousSystemStatus = store.labState.system_status;
        
        // --- ADDED: Auto-refresh available components list for sidebar ---
        if (!store.labState.components || Object.keys(store.labState.components).length === 0) {
             // If lab state is empty, we should still show something if it's just initialized
             // but 'components' in store.labState might be empty if the file is empty.
             // We rely on 'updateUI' to handle rendering.
        }
        
        if (store.labState.system_status === 'IDLE') {
            store.pendingCommands.clear(); 
            if (store.isOptimizing) {
                store.isOptimizing = false; 
                log("Optimization sequence complete.", "info");
                
                // Hide optimization feed overlay
                const optOverlay = document.getElementById('optimization-overlay');
                if (optOverlay) optOverlay.style.display = 'none';
                store.isOptimizingFeedActive = false;

                // Revert table-cam highlight
                const tableCamPreview = document.getElementById('table-cam-preview');
                if (tableCamPreview) {
                    tableCamPreview.style.border = '1px solid var(--border-color)';
                    tableCamPreview.style.backgroundColor = '#0f1115';
                    tableCamPreview.style.boxShadow = '';
                }
                
                // Stop optimization stream to save bandwidth
                const tableCamImg = document.getElementById('table-cam-img');
                const tableCamPlaceholder = document.getElementById('table-cam-placeholder');
                if (tableCamImg) {
                    tableCamImg.src = ""; 
                    tableCamImg.style.display = 'none';
                }
                if (tableCamPlaceholder) {
                    tableCamPlaceholder.style.display = 'flex';
                    tableCamPlaceholder.innerHTML = '<span class="material-icons-round" style="font-size: 24px; margin-bottom: 8px;">camera_alt</span><span style="font-size: 11px;">Table Cam Capture</span>';
                }
            }
        } else if (store.labState.system_status === 'OPTIMIZING') {
            store.isOptimizing = true;
            
            // Show optimization feed overlay
            const optOverlay = document.getElementById('optimization-overlay');
            const optStepText = document.getElementById('optimization-step-text');
            if (optOverlay && optStepText) {
                optOverlay.style.display = 'flex';
                optStepText.innerText = `OPTIMIZING (Step ${store.labState.optimization_step || 0})`;
            }

            // Highlight the table cam preview while optimizing
            const tableCamPreview = document.getElementById('table-cam-preview');
            if (tableCamPreview) {
                console.log(`[UI] OPTIMIZING -> highlighting table-cam-preview (step=${store.labState.optimization_step || 0})`);
                tableCamPreview.style.border = '2px solid #22c55e';
                tableCamPreview.style.backgroundColor = '#0b2a19';
                tableCamPreview.style.boxShadow = '0 0 0 3px rgba(34,197,94,0.25)';
            }

            if (!store.isOptimizingFeedActive) {
                store.isOptimizingFeedActive = true;
                const tableCamImg = document.getElementById('table-cam-img');
                const tableCamPlaceholder = document.getElementById('table-cam-placeholder');
                const tableCamError = document.getElementById('table-cam-error');
                
                if (tableCamImg) {
                    // Remove the old captured image immediately when optimization starts
                    tableCamImg.src = "";
                    tableCamImg.style.display = 'none';
                    console.log(`[UI] switching table-cam to optimization-feed stream...`);
                    if (tableCamPlaceholder) {
                        tableCamPlaceholder.style.display = 'flex';
                        tableCamPlaceholder.innerHTML = `<span class="material-icons-round" style="font-size: 18px; margin-bottom: 2px;">auto_awesome</span><div>Optimizing... (Step ${store.labState.optimization_step || 0})</div>`;
                    }

                    tableCamImg.src = `/api/optimization-feed/stream?t=${Date.now()}`;
                    tableCamImg.style.display = 'block';
                    if (tableCamPlaceholder) tableCamPlaceholder.style.display = 'none';
                    if (tableCamError) tableCamError.style.display = 'none';
                }
            }

            // Fake beam-intensity plot is mock-only; real lab has no such metric in state.
            if (store.labState.lab_mode === 'MOCK' && Math.random() > 0.5) {
                store.optimizationData.push({
                    step: store.optimizationData.length, 
                    value: Math.min(1.0, 0.2 + store.optimizationData.length * 0.05 + Math.random() * 0.1)
                });
            }
        }

        updateUI();
    } catch (error) {
        console.error("Failed to fetch lab state:", error);
        statusBadge.innerHTML = `<span class="status-dot error"></span> OFFLINE`;
        
        showErrorModal("Connection Failed", error.message);

        // Update Inventory List to show error instead of spinner
        componentList.innerHTML = `
            <div style="padding: 20px; text-align: center; color: #ef4444;">
                <span class="material-icons-round" style="font-size: 24px;">error_outline</span>
                <p style="margin-top: 8px; font-size: 12px;">Connection Failed</p>
                <p style="font-size: 10px; opacity: 0.7;">${error.message}</p>
                <button onclick="location.reload()" class="btn btn-secondary" style="margin-top: 12px; font-size: 10px;">Retry</button>
            </div>
        `;
    }
}

function showErrorModal(title, message) {
    if (document.getElementById('error-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'error-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #ef4444';
    card.style.borderRadius = '8px';
    card.style.padding = '32px';
    card.style.width = '450px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 48px; color: #ef4444; margin-bottom: 16px;">report_problem</span>
        <h2 style="margin: 0 0 12px 0; color: #e2e8f0; font-size: 20px;">${title}</h2>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5;">${message}</p>
        <button onclick="location.reload()" class="btn btn-primary" style="background-color: #ef4444; width: auto; margin: 0 auto; padding: 10px 24px;">
            <span class="material-icons-round">refresh</span> Retry Connection
        </button>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

function showConfirmationModal(message, onConfirm, onCancel) {
    if (document.getElementById('confirm-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'confirm-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #3b82f6';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '400px';
    card.style.textAlign = 'center';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    card.innerHTML = `
        <span class="material-icons-round" style="font-size: 40px; color: #3b82f6; margin-bottom: 12px;">help_outline</span>
        <h3 style="margin: 0 0 12px 0; color: #e2e8f0;">Confirm Action</h3>
        <p style="margin: 0 0 24px 0; color: #94a3b8; font-size: 14px; line-height: 1.5;">${message}</p>
        <div style="display: flex; justify-content: center; gap: 12px;">
            <button id="confirm-no" class="btn btn-secondary" style="width: auto; padding: 8px 20px;">Cancel</button>
            <button id="confirm-yes" class="btn btn-primary" style="width: auto; padding: 8px 20px;">Confirm</button>
        </div>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    document.getElementById('confirm-no').onclick = () => {
        overlay.remove();
        if (onCancel) onCancel();
    };

    document.getElementById('confirm-yes').onclick = () => {
        overlay.remove();
        if (onConfirm) onConfirm();
    };
}

async function sendCommand(command) {
    // Intercept MOVE_COMPONENT commands for confirmation
    if (command.action === 'MOVE_COMPONENT' && !store.isRecording) {
        // Create a descriptive message
        let msg = `Move <strong>${command.target_id}</strong>?`;
        if (command.parameters) {
            msg += `<br>X: ${command.parameters.target_x.toFixed(1)} mm<br>Y: ${command.parameters.target_y.toFixed(1)} mm<br>Rot: ${command.parameters.rotation.toFixed(1)}°`;
        }
        
        return new Promise((resolve) => {
            showConfirmationModal(
                msg, 
                async () => {
                    const r = await executeSendCommand(command);
                    resolve(r);
                },
                () => {
                    // Cancelled: Revert ghost state if possible
                    log("Move cancelled by user.", "info");
                    
                    if (command.target_id && store.labState && store.labState.components && store.labState.components[command.target_id] && store.labState.components[command.target_id].state === 'PLACED') {
                        const original = store.labState.components[command.target_id].pose;
                        // Only revert if we have the ghost state object
                        if (store.ghostState[command.target_id]) {
                            store.ghostState[command.target_id].x = original.x;
                            store.ghostState[command.target_id].y = original.y;
                            store.ghostState[command.target_id].rotation = original.rotation;
                        
                            // Update Context Panel if selected
                            if (store.selectedComponent === command.target_id) {
                                updateContextPanel(command.target_id);
                            }
                            render();
                        }
                    }
                    resolve({ ok: false, error: 'cancelled' });
                }
            );
        });
    }

    // Direct execution for other commands or if recording
    return await executeSendCommand(command);
}

async function executeSendCommand(command) {
    try {
        log(`Sending command: ${command.action}`, "info");
        
        // RECIPE LOGIC: Capture command if recording
        if (store.isRecording) {
            const step = {
                step: store.currentRecipeSteps.length + 1,
                action: command.action,
                component: command.target_id,
                parameters: command.parameters || {}
            };
            store.currentRecipeSteps.push(step);
            updateRecipeEditorList();
            // We still execute it live so the user sees the result!
        }

        if (command.target_id) store.pendingCommands.add(command.target_id);
        
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(command)
        });
        
        if (response.status === 409) {
             log("System BUSY. Command rejected.", "warn");
             store.pendingCommands.delete(command.target_id);
             return { ok: false, error: 'System is BUSY or OPTIMIZING (409).' };
        }

        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
            const detail = result.detail || `HTTP ${response.status}`;
            log(`Command rejected: ${detail}`, "error");
            if (command.target_id) store.pendingCommands.delete(command.target_id);
            return { ok: false, error: detail };
        }

        log(`Server: ${result.message}`, "info");
        
        if (command.action === 'OPTIMIZE') {
            store.isOptimizing = true;
            store.optimizationData = []; 
        }

        return { ok: true, message: result.message || 'Accepted' };

    } catch (error) {
        log(`Command failed: ${error.message}`, "error");
        if (command.target_id) store.pendingCommands.delete(command.target_id);
        return { ok: false, error: error.message || String(error) };
    }
}

// --- 2. Interaction Logic ---

function getComponentSize(name) {
    let size = { width: 90, height: 90 }; // Default 90x90mm as requested

    // Case 1: Existing component in Lab State
    if (store.labState && store.labState.components && store.labState.components[name]) {
        const tagId = store.labState.components[name].id;
        if (store.catalogMap[tagId] && store.catalogMap[tagId].size) {
            size = store.catalogMap[tagId].size;
        }
    } 
    // Case 2: Direct Tag ID (e.g. during Drag-and-Drop creation)
    else if (store.catalogMap[name] && store.catalogMap[name].size) {
        size = store.catalogMap[name].size;
    }
    return size;
}

function getComponentRadius(name) {
    const size = getComponentSize(name);
    // Circumscribed radius = sqrt(w^2 + h^2) / 2
    return Math.sqrt(size.width * size.width + size.height * size.height) / 2;
}

function checkCollision(targetId, x, y) {
    const PADDING_MM = 5; // Minimal padding distance between circumscribed circles
    const r1 = getComponentRadius(targetId);

    for (const [id, pose] of Object.entries(store.ghostState)) {
        if (id === targetId) continue; // Don't check against self
        
        const r2 = getComponentRadius(id);
        const minDist = r1 + r2 + PADDING_MM;

        // Calculate distance in mm
        const dx = x - pose.x;
        const dy = y - pose.y;
        const dist = Math.sqrt(dx*dx + dy*dy);
        
        if (dist < minDist) {
            return { detected: true, other: id };
        }
    }

    // Check Danger Zone (R=126/2mm)
    const distOrigin = Math.sqrt(x*x + y*y);
    if (distOrigin < 90 + r1) {
        return { detected: true, other: "DANGER ZONE (Robot Base)" };
    }

    return { detected: false };
}

function getComponentAtPosition(canvasX, canvasY) {
    for (const [name, pose] of Object.entries(store.ghostState)) {
        const p = mmToPx(pose.x, pose.y);
        const dx = canvasX - p.x;
        const dy = canvasY - p.y;
        if (Math.sqrt(dx*dx + dy*dy) < 20) return { name, type: 'GHOST' };
    }
    return null;
}

canvas.addEventListener('mousedown', (e) => {
    if (store.labState && store.labState.system_status !== 'IDLE') return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = getComponentAtPosition(mouseX, mouseY);
    
    if (hit) {
        if (store.selectedComponent !== hit.name) {
            store.selectedComponent = hit.name;
            updateContextPanel(hit.name);
            render(); 
            log(`Selected ${hit.name}`, "info");
        } else {
            store.isDragging = true;
            store.draggingComponent = hit.name;
            const p = mmToPx(store.ghostState[hit.name].x, store.ghostState[hit.name].y);
            store.dragOffset = { x: mouseX - p.x, y: mouseY - p.y };
        }
    } else {
        store.selectedComponent = null;
        contextPanel.style.display = 'none';
        render();
    }
});

function updateContextPanel(name) {
    const comp = store.labState.components[name];
    const pose = store.ghostState[name];
    
    let displayName = name;
    let properties = {};

    if (store.catalogMap[name]) {
        displayName = store.catalogMap[name].name;
        if (store.catalogMap[name].properties) {
            properties = store.catalogMap[name].properties;
        }
    }
    
    selectedCompName.textContent = displayName;

    // Render Properties
    selectedCompProperties.innerHTML = '';
    if (Object.keys(properties).length > 0) {
        const propsHtml = Object.entries(properties).map(([key, val]) => {
            // Format Key: radius_of_curvature -> Radius of curvature
            const cleanKey = key.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
            return `<div style="margin-bottom: 2px;">${cleanKey}: <span style="color: #e2e8f0;">${val}</span></div>`;
        }).join('');
        selectedCompProperties.innerHTML = propsHtml;
    }

    contextPanel.style.display = 'block';
    
    ctxX.value = pose.x.toFixed(1);
    ctxY.value = pose.y.toFixed(1);
    ctxRot.value = (pose.rotation || 0).toFixed(1);
    
    // Generate Strategies Buttons
    ctxStrategies.innerHTML = '';
    Object.entries(store.availableStrategies).forEach(([stratKey, strat]) => {
        const btn = document.createElement('button');
        btn.className = 'btn btn-secondary';
        btn.style.width = '100%';
        btn.style.marginBottom = '4px';
        btn.style.fontSize = '10px';
        btn.style.padding = '6px';
        btn.style.textAlign = 'left';
        btn.innerHTML = `<span class="material-icons-round" style="font-size: 12px; vertical-align: middle;">settings_suggest</span> ${strat.name}`;
        btn.onclick = () => showParameterModal(stratKey, strat);
        ctxStrategies.appendChild(btn);
    });

    // --- Motor Controls ---
    const existingMotor = document.getElementById('ctx-motor-controls');
    if (existingMotor) existingMotor.remove();

    if (store.catalogMap[name] && store.catalogMap[name].motor_ids && store.catalogMap[name].motor_ids.length > 0) {
        const motorSection = document.createElement('div');
        motorSection.id = 'ctx-motor-controls';
        motorSection.style.marginTop = '12px';
        motorSection.style.paddingTop = '12px';
        motorSection.style.borderTop = '1px solid #2a2e36';
        
        motorSection.innerHTML = '<div style="font-size:11px; color:#94a3b8; margin-bottom:8px; font-weight:600;">MOTOR CONTROL (Relative)</div>';
        
        store.catalogMap[name].motor_ids.forEach(mid => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.gap = '10px';
            row.style.marginBottom = '8px';
            
            const label = document.createElement('span');
            label.textContent = `M${mid}`;
            label.style.fontSize = '12px';
            label.style.color = '#cbd5e1';
            label.style.width = '25px';
            
            const input = document.createElement('input');
            input.type = 'number';
            input.value = '100'; 
            input.style.width = '60px';
            input.style.fontSize = '12px';
            input.style.padding = '6px 8px';
            input.style.background = '#0f1115';
            input.style.border = '1px solid #2a2e36';
            input.style.color = '#fff';
            input.style.borderRadius = '4px';
            input.title = "Step Size";
            
            const btnRev = document.createElement('button');
            btnRev.className = 'btn btn-secondary';
            btnRev.style.padding = '6px 12px';
            btnRev.style.fontSize = '12px';
            btnRev.style.width = 'auto';
            btnRev.innerHTML = '<span class="material-icons-round" style="font-size:14px">remove</span>';
            btnRev.title = "Jog Backward";
            btnRev.onclick = () => moveMotor(name, mid, -parseFloat(input.value));

            const btnFwd = document.createElement('button');
            btnFwd.className = 'btn btn-secondary';
            btnFwd.style.padding = '6px 12px';
            btnFwd.style.fontSize = '12px';
            btnFwd.style.width = 'auto';
            btnFwd.innerHTML = '<span class="material-icons-round" style="font-size:14px">add</span>';
            btnFwd.title = "Jog Forward";
            btnFwd.onclick = () => moveMotor(name, mid, parseFloat(input.value));
            
            row.appendChild(label);
            row.appendChild(btnRev);
            row.appendChild(input);
            row.appendChild(btnFwd);
            motorSection.appendChild(row);
        });
        
        ctxStrategies.parentNode.appendChild(motorSection);
    }
}

async function moveMotor(targetId, motorId, dist) {
    await sendCommand({
        action: "MOVE_MOTOR",
        target_id: targetId,
        parameters: {
            motor_id: motorId,
            distance: dist
        }
    });
}

ctxMoveBtn.addEventListener('click', async () => {
    if (!store.selectedComponent) return;
    
    const tx = parseFloat(ctxX.value);
    const ty = parseFloat(ctxY.value);
    const trot = parseFloat(ctxRot.value);

    // Collision Check
    const collision = checkCollision(store.selectedComponent, tx, ty);
    if (collision.detected) {
        log(`Move cancelled: Collision with ${collision.other}`, "error");
        // Revert UI values to current ghost state (which hasn't updated yet)
        if (store.ghostState[store.selectedComponent]) {
            const old = store.ghostState[store.selectedComponent];
            ctxX.value = old.x.toFixed(1);
            ctxY.value = old.y.toFixed(1);
        }
        return;
    }

    // Update Ghost State immediately for visual feedback
    store.ghostState[store.selectedComponent].x = tx;
    store.ghostState[store.selectedComponent].y = ty;
    store.ghostState[store.selectedComponent].rotation = trot;
    
    await sendCommand({
        action: "MOVE_COMPONENT",
        target_id: store.selectedComponent,
        parameters: {
            target_x: tx,
            target_y: ty,
            rotation: trot
        }
    });
    render();
});

// Remove popup related event listeners
// popupClose.addEventListener... (deleted)
// popupMoveBtn.addEventListener... (deleted)
// update MouseDown logic above replaced the old one


canvas.addEventListener('mousemove', (e) => {
    if (!store.isDragging || !store.draggingComponent) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const lab = pxToMm(mouseX - store.dragOffset.x, mouseY - store.dragOffset.y);
    
    // Snapping Logic (Snap to Laser Line)
    let finalX = lab.x;
    let finalY = lab.y;

    if (store.laserLineCoeffs && typeof store.laserLineCoeffs.a === 'number') {
        const a = store.laserLineCoeffs.a;
        const b = store.laserLineCoeffs.b;
        const SNAP_THRESHOLD_MM = 10; // Snap if within 10mm

        // Line eq: x - ay - b = 0  => A=1, B=-a, C=-b
        // Distance d = |Ax + By + C| / sqrt(A^2 + B^2)
        const val = finalX - a * finalY - b;
        const dist = Math.abs(val) / Math.sqrt(1 + a * a);

        if (dist < SNAP_THRESHOLD_MM) {
            // Project point onto line
            // (x, y) - k * (A, B) where k = val / (A^2 + B^2)
            const k = val / (1 + a * a);
            finalX = finalX - k;
            finalY = finalY + a * k; // y - (-a)*k
            
            // Optional: Snap Rotation? 
            // For now, we just snap position as requested.
        }
    }

    store.ghostState[store.draggingComponent].x = finalX;
    store.ghostState[store.draggingComponent].y = finalY;
    
    render();
});

// --- ADDED: Mouse Wheel Rotation ---
canvas.addEventListener('wheel', (e) => {
    if (store.isDragging && store.draggingComponent && store.ghostState[store.draggingComponent]) {
        e.preventDefault();
        
        // Scroll direction: positive deltaY (down) -> +5 deg, negative (up) -> -5 deg
        const direction = Math.sign(e.deltaY);
        const step = 5;
        
        if (typeof store.ghostState[store.draggingComponent].rotation !== 'number') {
            store.ghostState[store.draggingComponent].rotation = 0;
        }
        
        store.ghostState[store.draggingComponent].rotation += (direction * step);
        
        // Update UI immediately
        render();
        updateContextPanel(store.draggingComponent);
    }
}, { passive: false });

canvas.addEventListener('mouseup', async (e) => {
    if (store.isDragging && store.draggingComponent) {
        store.isDragging = false;
        
        // Collision Check
        const current = store.ghostState[store.draggingComponent];
        const collision = checkCollision(store.draggingComponent, current.x, current.y);
        
        if (collision.detected) {
             log(`Move cancelled: Collision with ${collision.other}`, "error");
             
             // Snap back to original position from store.labState if possible
             if (store.labState.components[store.draggingComponent] && store.labState.components[store.draggingComponent].state === 'PLACED') {
                 const original = store.labState.components[store.draggingComponent].pose;
                 store.ghostState[store.draggingComponent].x = original.x;
                 store.ghostState[store.draggingComponent].y = original.y;
                 store.ghostState[store.draggingComponent].rotation = original.rotation;
             }
             render();
             store.draggingComponent = null;
             return;
        }

        await sendCommand({
            action: "MOVE_COMPONENT",
            target_id: store.draggingComponent,
            parameters: {
                target_x: store.ghostState[store.draggingComponent].x,
                target_y: store.ghostState[store.draggingComponent].y,
                rotation: store.ghostState[store.draggingComponent].rotation
            }
        });
        
        store.draggingComponent = null;
    }
});

function handleInventoryDragStart(e, componentName) {
    e.dataTransfer.setData("text/plain", componentName);
}

canvas.addEventListener('dragover', (e) => e.preventDefault());

canvas.addEventListener('drop', (e) => {
        e.preventDefault();
        if (store.labState && store.labState.system_status !== 'IDLE') return;
    
        const componentName = e.dataTransfer.getData("text/plain");
        
        // --- MODIFIED: Allow dropping ANY component ID, even if not in store.labState yet ---
        if (componentName) {
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;
            
            const type = e.dataTransfer.getData("application/type") || "OPTICAL_MIRROR";
            const lab = pxToMm(mouseX, mouseY);
            // Initialize ghost state for new component immediately
            store.ghostState[componentName] = {
                x: lab.x,
                y: lab.y,
                rotation: 0
            };

            // Collision Check
            const collision = checkCollision(componentName, lab.x, lab.y);
            if (collision.detected) {
                log(`Placement cancelled: Collision with ${collision.other}`, "error");
                delete store.ghostState[componentName];
                render();
                return;
            }
    
            // Trigger move command which will create it in backend
            sendCommand({
                action: "MOVE_COMPONENT",
                target_id: componentName,
                parameters: {
                    target_x: store.ghostState[componentName].x,
                    target_y: store.ghostState[componentName].y,
                    rotation: 0,
                    type: type // Pass type to backend
                }
            });
    
            log(`Placed ${componentName}`, "info");
            render();
        }
    });


// Context Popup Elements
const contextPopup = document.getElementById('context-popup');
const popupTitle = document.getElementById('popup-title');
const popupClose = document.getElementById('popup-close');
const popupX = document.getElementById('popup-x');
const popupY = document.getElementById('popup-y');
const popupRot = document.getElementById('popup-rot');
const popupMoveBtn = document.getElementById('popup-move-btn');
const popupStrategies = document.getElementById('popup-strategies');

// ...

// --- 3. Sidebar Selection Panel ---

// Removed showContextPopup and hideContextPopup functions as they are replaced by updateContextPanel

// ... (Parameter Modal Logic remains)

// --- Parameter Modal Logic ---

function showParameterModal(strategyKey, strategyDef) {
    const existing = document.getElementById('param-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'param-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.7)';
    overlay.style.zIndex = '2000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #2a2e36';
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '400px';
    card.style.boxShadow = '0 10px 25px rgba(0,0,0,0.5)';

    const title = document.createElement('h3');
    title.textContent = `Configure ${strategyDef.name}`;
    title.style.margin = '0 0 8px 0';
    title.style.color = '#e2e8f0';
    card.appendChild(title);

    const desc = document.createElement('p');
    desc.textContent = strategyDef.description;
    desc.style.margin = '0 0 20px 0';
    desc.style.color = '#94a3b8';
    desc.style.fontSize = '13px';
    card.appendChild(desc);

    const form = document.createElement('form');
    const inputs = {};

    Object.entries(strategyDef.parameters).forEach(([paramKey, paramDef]) => {
        const field = document.createElement('div');
        field.style.marginBottom = '16px';

        const label = document.createElement('label');
        label.textContent = `${paramKey} (${paramDef.description})`;
        label.style.display = 'block';
        label.style.marginBottom = '6px';
        label.style.color = '#cbd5e1';
        label.style.fontSize = '12px';
        field.appendChild(label);

        let input;
        if (paramDef.enum) {
            input = document.createElement('select');
            paramDef.enum.forEach(opt => {
                const option = document.createElement('option');
                option.value = opt;
                option.textContent = opt;
                if (opt === paramDef.default) option.selected = true;
                input.appendChild(option);
            });
        } else {
            input = document.createElement('input');
            input.type = paramDef.type === 'integer' || paramDef.type === 'float' ? 'number' : 'text';
            input.value = paramDef.default !== null ? paramDef.default : '';
            if (paramDef.type === 'float') input.step = '0.01';
        }
        
        input.style.width = '100%';
        input.style.padding = '8px';
        input.style.backgroundColor = '#0f1115';
        input.style.border = '1px solid #2a2e36';
        input.style.borderRadius = '4px';
        input.style.color = 'white';
        
        inputs[paramKey] = input;
        field.appendChild(input);
        form.appendChild(field);
    });

    const btnRow = document.createElement('div');
    btnRow.style.display = 'flex';
    btnRow.style.justifyContent = 'flex-end';
    btnRow.style.gap = '12px';
    btnRow.style.marginTop = '24px';

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-secondary'; 
    cancelBtn.style.width = 'auto';
    cancelBtn.onclick = () => overlay.remove();

    const runBtn = document.createElement('button');
    runBtn.textContent = 'Run Strategy';
    runBtn.type = 'submit';
    runBtn.className = 'btn btn-primary'; 
    runBtn.style.width = 'auto';

    form.onsubmit = (e) => {
        e.preventDefault();
        const params = {};
        Object.entries(inputs).forEach(([key, el]) => {
            const def = strategyDef.parameters[key];
            let val = el.value;
            if (def.type === 'integer') val = parseInt(val);
            if (def.type === 'float') val = parseFloat(val);
            params[key] = val;
        });
        
        params.strategy = strategyKey;

        // Auto-inject motor_ids for COBYLA if available
        if (strategyKey === 'COBYLA' && store.selectedComponent) {
             if (store.catalogMap[store.selectedComponent] && store.catalogMap[store.selectedComponent].motor_ids) {
                 params.motor_ids = store.catalogMap[store.selectedComponent].motor_ids;
                 log(`Using motor_ids: [${params.motor_ids.join(', ')}]`, "info");
             }
        }

        sendCommand({
            action: "OPTIMIZE",
            target_id: store.selectedComponent, 
            parameters: params
        });
        overlay.remove();
    };

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(runBtn);
    form.appendChild(btnRow);
    card.appendChild(form);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

// --- 4. Rendering ---

function clearCanvas() {
    const gradient = ctx.createLinearGradient(0, 0, 0, CANVAS_HEIGHT);
    gradient.addColorStop(0, '#1a1d21');
    gradient.addColorStop(1, '#141619');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
    
    // Draw Danger Zone (R=10cm around origin)
    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, 90 * LAB_SCALE, 0, Math.PI * 2); // 90mm
    ctx.fillStyle = 'rgba(239, 68, 68, 0.1)'; // Reddish transparent
    ctx.fill();
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
    
    // Draw Danger Zone Label
    ctx.fillStyle = 'rgba(239, 68, 68, 0.5)';
    ctx.font = '10px Inter';
    ctx.fillText("DANGER ZONE", LAB_CENTER_PX.x - 30, LAB_CENTER_PX.y - 10);

    // Draw Axes
    // X Axis (Red)
    ctx.beginPath();
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x + 50, LAB_CENTER_PX.y); // 50px length
    ctx.stroke();
    ctx.fillStyle = '#ef4444';
    ctx.fillText("X", LAB_CENTER_PX.x + 55, LAB_CENTER_PX.y + 4);

    // Y Axis (Green) - Note: Canvas Y is inverted relative to Lab Y usually, but here we mapped +Y up in mmToPx
    // mmToPx: y: LAB_CENTER_PX.y - labY * LAB_SCALE. So +LabY is -CanvasY (Up).
    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;
    ctx.moveTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y);
    ctx.lineTo(LAB_CENTER_PX.x, LAB_CENTER_PX.y - 50); // Up
    ctx.stroke();
    ctx.fillStyle = '#10b981';
    ctx.fillText("Y", LAB_CENTER_PX.x - 4, LAB_CENTER_PX.y - 55);

    // Origin Dot
    ctx.beginPath();
    ctx.arc(LAB_CENTER_PX.x, LAB_CENTER_PX.y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();

    // Draw Breadboard Grid (25mm spacing), shifted in X to match physical hole columns (see BREADBOARD_GRID_OFFSET_X_MM).
    ctx.fillStyle = '#2a2e36';
    const gridSpacingMm = 25;
    
    // Calculate start/end based on lab coordinates
    // We iterate in mm and convert to px to ensure accuracy
    for (let xMm = LAB_X_MIN; xMm <= LAB_X_MAX; xMm += gridSpacingMm) {
        for (let yMm = LAB_Y_MIN; yMm <= LAB_Y_MAX; yMm += gridSpacingMm) {
            const p = mmToPx(xMm + BREADBOARD_GRID_OFFSET_X_MM, yMm);
            // Only draw if within canvas bounds (though mmToPx should handle mapping)
            if (p.x >= 0 && p.x <= CANVAS_WIDTH && p.y >= 0 && p.y <= CANVAS_HEIGHT) {
                ctx.beginPath(); 
                ctx.arc(p.x, p.y, 2, 0, Math.PI * 2); 
                ctx.fill();
            }
        }
    }
}

/** Clip segment of line x = a*y + b to lab bounds; returns [p1, p2] in mm or null. */
function clipLaserLineToBounds(a, b) {
    const pts = [];
    if (Math.abs(a) < 1e-10) {
        const x = b;
        if (x < LAB_X_MIN || x > LAB_X_MAX) return null;
        pts.push({ x, y: LAB_Y_MIN }, { x, y: LAB_Y_MAX });
    } else {
        const yAtLeft = (LAB_X_MIN - b) / a;
        const yAtRight = (LAB_X_MAX - b) / a;
        if (yAtLeft >= LAB_Y_MIN && yAtLeft <= LAB_Y_MAX) pts.push({ x: LAB_X_MIN, y: yAtLeft });
        if (yAtRight >= LAB_Y_MIN && yAtRight <= LAB_Y_MAX) pts.push({ x: LAB_X_MAX, y: yAtRight });
        const xAtBottom = a * LAB_Y_MIN + b;
        const xAtTop = a * LAB_Y_MAX + b;
        if (xAtBottom >= LAB_X_MIN && xAtBottom <= LAB_X_MAX) pts.push({ x: xAtBottom, y: LAB_Y_MIN });
        if (xAtTop >= LAB_X_MIN && xAtTop <= LAB_X_MAX) pts.push({ x: xAtTop, y: LAB_Y_MAX });
    }
    if (pts.length < 2) return null;
    pts.sort((a_, b_) => a_.y - b_.y);
    return [pts[0], pts[pts.length - 1]];
}

function drawLaserPath() {
    ctx.shadowBlur = 10;
    ctx.shadowColor = '#ff3b3b';
    ctx.strokeStyle = '#ff3b3b';
    ctx.lineWidth = 2;
    ctx.setLineDash([10, 10]);
    const coef = store.laserLineCoeffs || { a: 0, b: 0 };
    const seg = clipLaserLineToBounds(coef.a, coef.b);
    if (seg) {
        const p1 = mmToPx(seg[0].x, seg[0].y);
        const p2 = mmToPx(seg[1].x, seg[1].y);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.shadowBlur = 0;
}

function drawComponent(name, pose, type, mode = 'SOLID') {
    const p = mmToPx(pose.x, pose.y);
    const x = p.x;
    const y = p.y;
    const rotation = pose.rotation * (Math.PI / 180); 

    // Determine Size in Pixels (since LAB_SCALE is px/mm, width_px = width_mm * LAB_SCALE)
    const size = getComponentSize(name);
    const w = size.width * LAB_SCALE;
    const h = size.height * LAB_SCALE;
    const halfW = w / 2;
    const halfH = h / 2;

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rotation);

    if (mode === 'GHOST') ctx.globalAlpha = 0.5;
    if (mode === 'PENDING') ctx.globalAlpha = 0.7;

    ctx.shadowColor = (mode === 'GHOST') ? 'transparent' : 'rgba(0,0,0,0.5)';
    ctx.shadowBlur = (mode === 'GHOST') ? 0 : 10;
    
    // Selection Halo
    if (name === store.selectedComponent) {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        ctx.beginPath(); 
        // Use circumscribed circle for halo to ensure it covers the shape
        const r = Math.sqrt(halfW*halfW + halfH*halfH) + 5;
        ctx.arc(0, 0, r, 0, Math.PI * 2); 
        ctx.stroke();
    }

    if (mode === 'PENDING') {
        ctx.strokeStyle = '#f59e0b'; // Amber
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 2]);
        const r = Math.sqrt(halfW*halfW + halfH*halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.stroke();
        ctx.setLineDash([]);
    }

    if (store.isOptimizing && store.pendingCommands.has(name)) {
        ctx.shadowColor = '#10b981'; // Green glow
        ctx.shadowBlur = 20;
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        const r = Math.sqrt(halfW*halfW + halfH*halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.stroke();
    }

    // Draw Specific Icons based on Catalog ID or Type
    const catalogItem = store.catalogMap[name];
    const catalogId = catalogItem ? catalogItem.id : null;

    if (catalogId === 'nd_filter') {
        // ND Filter: Dark Neutral (Black/Grey)
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        // Dark Glass look
        ctx.fillStyle = 'rgba(20, 20, 20, 0.9)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);

    } else if (catalogId === 'filter_generic') {
        // Generic Filter: Colored (e.g. Red/Pink)
        ctx.fillStyle = '#333';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#f87171'; // Reddish border
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        // Tinted Glass look
        ctx.fillStyle = 'rgba(248, 113, 113, 0.3)';
        ctx.fillRect(-halfW + 2, -halfH + 2, w - 4, h - 4);

    } else if (catalogId === 'cam_gripper_1' || catalogId === 'cam_gripper_2' || type === 'OPTICAL_CAMERA') {
        // Camera
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(-halfW, -halfH, w, h);
        // Lens ring
        ctx.fillStyle = '#000';
        ctx.beginPath(); ctx.arc(0, 0, Math.min(w,h)/3, 0, Math.PI * 2); ctx.fill();
        // Sensor reflection
        ctx.fillStyle = '#3b82f6'; // Blueish reflection
        ctx.beginPath(); ctx.arc(0, 0, Math.min(w,h)/8, 0, Math.PI * 2); ctx.fill();
        // Direction indicator
        ctx.fillStyle = '#ef4444';
        const triH = h/4;
        ctx.beginPath(); ctx.moveTo(0, -halfH - 2); ctx.lineTo(-triH/2, -halfH - triH - 2); ctx.lineTo(triH/2, -halfH - triH - 2); ctx.fill();

    } else if (catalogId === 'mirror_curved') {
        // Curved Mirror (Semi-Circle Concave)
        // Assume width is the diameter, height is the depth? Or vice versa.
        // Catalog size: 90x90. Let's draw it fitting in the box.
        const radius = Math.min(w, h) / 2;
        
        // Mirror Surface (Semi-circle)
        ctx.strokeStyle = '#3b82f6'; 
        ctx.lineWidth = 4;
        ctx.beginPath();
        // Arc from -PI/2 (top) to PI/2 (bottom) counter-clockwise (Left Side)
        ctx.arc(0, 0, radius, -Math.PI/2-Math.PI/4, Math.PI/2-Math.PI/4, true);
        ctx.stroke();
        
        // Mount backing (curved)
        ctx.fillStyle = '#444';
        ctx.beginPath();
        // Outer arc (backing)
        ctx.arc(0, 0, radius + 4, -Math.PI/2-Math.PI/4, Math.PI/2-Math.PI/4, true);
        // Connect bottom
        ctx.lineTo(0, radius);
        // Inner arc (match mirror)
        ctx.arc(0, 0, radius, Math.PI/2-Math.PI/4, -Math.PI/2-Math.PI/4, false);
        ctx.closePath();
        ctx.fill();

        // Reflective side hint (Gloss)
        ctx.strokeStyle = 'rgba(255,255,255,0.6)'; 
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(0, 0, radius - 2, -Math.PI/2-Math.PI/4, Math.PI/2-Math.PI/4, true);
        ctx.stroke();

    } else if (catalogId === 'mirror_planar' || type === 'OPTICAL_MIRROR') {
        // Planar Mirror
        // Draw fitting in w x h box.
        // Usually thin in one dimension, wide in other.
        // But footprint is 90x90.
        // We'll draw the mirror face along Y axis, centered.
        
        ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.moveTo(0, -halfH); ctx.lineTo(0, halfH); ctx.stroke();
        // Mount backing
        ctx.fillStyle = '#444'; ctx.fillRect(-halfW/2, -halfH, halfW/2, h); 
        // Reflective side hint
        ctx.strokeStyle = 'rgba(255,255,255,0.5)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(2, -halfH + 5); ctx.lineTo(2, halfH - 5); ctx.stroke();

    } else if (catalogId === 'beam_block') {
        // Beam Block: Solid dark block with cross
        ctx.fillStyle = '#111';
        ctx.fillRect(-halfW, -halfH, w, h);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.beginPath(); 
        ctx.moveTo(-halfW, -halfH); ctx.lineTo(halfW, halfH);
        ctx.moveTo(halfW, -halfH); ctx.lineTo(-halfW, halfH);
        ctx.stroke();
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);

    } else if (catalogId === 'beam_splitter' || type === 'OPTICAL_BEAMSPLITTER') {
        // Beam Splitter: Cube
        ctx.fillStyle = 'rgba(200, 200, 200, 0.1)';
        ctx.strokeStyle = '#888'; ctx.lineWidth = 2;
        ctx.strokeRect(-halfW, -halfH, w, h);
        // Diagonal coating
        ctx.strokeStyle = 'rgba(100, 200, 255, 0.8)';
        ctx.beginPath(); ctx.moveTo(-halfW, -halfH); ctx.lineTo(halfW, halfH); ctx.stroke();

    } else if (catalogId === 'lens_main' || type === 'OPTICAL_LENS') {
        // Lens: Ellipse fitting the box
        ctx.fillStyle = 'rgba(100, 200, 255, 0.3)';
        ctx.strokeStyle = 'rgba(150, 220, 255, 0.9)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.ellipse(0, 0, halfW/3, halfH, 0, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        
    } else if (catalogId === 'crystal_main' || type === 'OPTICAL_CRYSTAL') {
        // Crystal: Hexagon or Rectangle fitting box
        ctx.fillStyle = 'rgba(236, 72, 153, 0.3)'; // Pinkish
        ctx.strokeStyle = '#ec4899';
        ctx.lineWidth = 2;
        ctx.beginPath();
        // Draw Hexagon fitting in w/h
        ctx.moveTo(-halfW/2, -halfH); ctx.lineTo(halfW/2, -halfH);
        ctx.lineTo(halfW, 0);
        ctx.lineTo(halfW/2, halfH); ctx.lineTo(-halfW/2, halfH);
        ctx.lineTo(-halfW, 0);
        ctx.closePath();
        ctx.fill(); ctx.stroke();

    } else {
        // Default / Unknown
        ctx.fillStyle = '#C0C0C0'; 
        const r = Math.min(halfW, halfH);
        ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#000';
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText("?", 0, 4);
    }

    ctx.restore();
    
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = (mode === 'GHOST') ? 'rgba(255, 255, 255, 0.5)' : 'rgba(255, 255, 255, 0.9)';
    ctx.font = '500 11px Inter, sans-serif';
    ctx.textAlign = 'center';
    
    // Resolve Display Name from Catalog
    let displayName = name;
    if (store.catalogMap[name]) {
        displayName = store.catalogMap[name].name;
    }
    
    ctx.fillText(displayName, 0, -halfH - 10);
    
    if (mode === 'PENDING') {
        ctx.fillStyle = '#f59e0b';
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText("MOVING...", 0, halfH + 15);
    }
    if (store.isOptimizing && store.pendingCommands.has(name)) {
        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText("OPTIMIZING...", 0, halfH + 15);
    }

    ctx.restore();
}

function drawOptimizationGraph() {
    if (!store.isOptimizing || store.optimizationData.length === 0) return;
    if (!store.labState || store.labState.lab_mode !== 'MOCK') return;

    const w = 300;
    const h = 150;
    const x = CANVAS_WIDTH - w - 20;
    const y = CANVAS_HEIGHT - h - 20;

    // Background
    ctx.fillStyle = 'rgba(24, 27, 33, 0.9)';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#2a2e36';
    ctx.strokeRect(x, y, w, h);

    // Title
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px Inter';
    ctx.fillText("Optimization Metric (Beam Intensity)", x + 10, y + 20);

    // Plot
    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;

    const maxSteps = 20; // assumed max
    const xScale = (w - 20) / maxSteps;
    const yScale = (h - 40); // 0-1 normalized

    store.optimizationData.forEach((point, i) => {
        const px = x + 10 + point.step * xScale;
        const py = y + h - 10 - point.value * yScale;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
    });
    ctx.stroke();
}

function render() {
    clearCanvas();
    drawLaserPath();

    if (!store.labState) return;

    // 1. Draw Physical Components (Solid)
    Object.entries(store.labState.components).forEach(([name, comp]) => {
        if (comp.state === 'PLACED') {
            drawComponent(name, comp.pose, comp.type, 'SOLID');
        }
    });

    // 2. Draw Ghost Components (Intent)
    // We now use store.ghostState which is initialized from comp.intent.nominal_pose
    Object.entries(store.ghostState).forEach(([name, pose]) => {
        const type = store.labState.components[name]?.type || 'UNKNOWN';
        const isPending = store.pendingCommands.has(name);
        drawComponent(name, pose, type, isPending ? 'PENDING' : 'GHOST');
        
        // Draw Drift Line (Nominal vs Physical)
        const physical = store.labState.components[name];
        if (physical && physical.state === 'PLACED') {
            const from = mmToPx(physical.pose.x, physical.pose.y);
            const to = mmToPx(pose.x, pose.y);
            ctx.strokeStyle = isPending ? '#f59e0b' : 'rgba(255, 255, 255, 0.2)';
            ctx.setLineDash([5, 5]);
            ctx.beginPath();
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
            ctx.stroke();
            ctx.setLineDash([]);
        }
    });

    drawOptimizationGraph();
}

// --- 5. UI Logic ---

function getComponentIcon(type) {
    switch(type) {
        case 'OPTICAL_MIRROR': return 'crop_portrait';
        case 'OPTICAL_LENS': return 'lens';
        case 'OPTICAL_BEAMSPLITTER': return 'dashboard';
        case 'OPTICAL_CAMERA': return 'videocam';
        default: return 'help_outline';
    }
}

function updateUI() {
    if (!store.labState) return;

    const status = store.labState.system_status;
    let badgeClass = 'active';
    let badgeColor = 'placed'; // green
    let badgeStyle = '';

    if (status === 'BUSY') {
        badgeClass = '';
        badgeColor = 'inventory'; // blue/default
        badgeStyle = 'background-color: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.4);';
    } else if (status === 'OPTIMIZING') {
        badgeClass = '';
        badgeColor = 'placed';
        badgeStyle = 'background-color: #10b981; box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);';
    }

    statusBadge.className = `system-status ${badgeClass}`;
    statusBadge.innerHTML = `<span class="status-dot ${badgeColor}" style="${badgeStyle}"></span> ${status}`;

    // Update Sidebar Selection if active
    if (store.selectedComponent) {
        // updateSidebarSelection(); // function removed previously
    }

    componentList.innerHTML = '';
    // libraryList.innerHTML = ''; // DO NOT TOUCH LIBRARY LIST IN UPDATE LOOP
    
    const components = store.labState.components || {};
    
    // Check if there are any placed items
    // (We treat everything in components as 'placed' or at least 'in lab' for the sidebar list)
    const placedCount = Object.keys(components).length;
    
    if (placedCount === 0) {
        componentList.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b; font-size: 11px;">No components placed.</div>';
    }
    
    // REMOVED: All logic that tries to put items into libraryList based on state
    // The libraryList is exclusively for the CATALOG (Add Component Popup)

    Object.entries(components).forEach(([name, comp]) => {
        const card = document.createElement('div');
        card.className = 'component-card';
        if (name === store.selectedComponent) card.style.borderColor = '#3b82f6'; 
        // card.draggable = true; // Dragging from sidebar to move? Maybe, but mostly we select and use context panel.
        
        // card.addEventListener('dragstart', (e) => handleInventoryDragStart(e, name));
        const isPlaced = comp.state === 'PLACED';
        
        // Resolve Real Name from Catalog using Tag ID
        let displayName = name; // Default to key if unknown
        let displayType = comp.type;
        let unknownTag = false;

        if (store.catalogMap[comp.id]) {
            displayName = store.catalogMap[comp.id].name;
            // displayType = store.catalogMap[comp.id].type; // Ensure type matches catalog
        } else {
            // Unknown Tag Logic
            displayName = `Unknown (${comp.id})`;
            unknownTag = true;
        }

        const icon = getComponentIcon(comp.type);
        
        // Show status dot
        let statusDot = `<div class="status-dot ${isPlaced ? 'placed' : 'inventory'}" title="${comp.state}"></div>`;
        if (comp.intent && comp.intent.is_optimized) {
            statusDot = `<div class="status-dot" style="background-color: #10b981; box-shadow: 0 0 6px #10b981;" title="Optimized"></div>`;
        } else if (comp.state === 'PLACED') {
             // statusDot = `<div class="status-dot" style="background-color: #f59e0b;" title="Drifted/Manual"></div>`;
        }

        // Motor Badge
        let motorBadge = '';
        if (store.catalogMap[comp.id] && store.catalogMap[comp.id].motor_ids && store.catalogMap[comp.id].motor_ids.length > 0) {
            motorBadge = `<span class="material-icons-round" style="font-size: 12px; color: #f59e0b; margin-right: 4px;" title="Motorized">settings_input_component</span>`;
        }

        card.innerHTML = `
            <div class="comp-icon material-icons-round">${icon}</div>
            <div class="comp-info">
                <span class="comp-name" style="${unknownTag ? 'color: #f59e0b;' : ''}">${displayName}</span>
                <span class="comp-meta">${motorBadge}${displayType.replace('OPTICAL_', '')} • ${comp.id}</span>
            </div>
            ${statusDot}
        `;
        
        // Click listener for selection
        card.addEventListener('click', () => {
            store.selectedComponent = name;
            updateContextPanel(name);
            render();
        });

        componentList.appendChild(card);
    });

    render();
}

// --- Library Controls ---

addComponentBtn.addEventListener('click', async () => {
    libraryPopup.style.display = 'block'; // Show immediately
    
    // Use cached catalog if available to avoid refetching
    // if (store.catalogCache) {
    //    renderCatalog(store.catalogCache);
    //    return;
    // }

    libraryList.innerHTML = '<div style="padding:10px; text-align:center; color:#64748b">Loading catalog...</div>';
    
    try {
        const response = await fetch('/api/catalog');
        if (!response.ok) throw new Error("Failed to load catalog");
        store.catalogCache = await response.json();
        renderCatalog(store.catalogCache);
    } catch (e) {
        libraryList.innerHTML = `<div style="padding:10px; text-align:center; color:#ef4444">Error: ${e.message}</div>`;
    }
});

function renderCatalog(catalog) {
    libraryList.innerHTML = '';
    if (catalog.length === 0) {
        libraryList.innerHTML = '<div style="padding:10px; text-align:center; color:#64748b">Catalog empty.</div>';
        return;
    }

    // Get current lab components to check what is already placed
    const labComponents = store.labState ? store.labState.components : {};
    const placedTagIds = new Set(Object.values(labComponents).map(c => c.id));

    catalog.forEach(item => {
        const card = document.createElement('div');
        card.className = 'component-card';
        
        const icon = getComponentIcon(item.type);
        const isAlreadyPlaced = placedTagIds.has(item.tag_id);
        
        let actionBtn = '';
        if (isAlreadyPlaced) {
            actionBtn = `<div style="font-size: 10px; color: #10b981; font-weight: 600; padding: 4px 8px;">IN LAB</div>`;
        } else {
            actionBtn = `<div class="btn btn-primary req-btn" style="padding: 4px 8px; font-size: 10px; width: auto;">Request</div>`;
        }
        
        card.innerHTML = `
            <div class="comp-icon material-icons-round">${icon}</div>
            <div class="comp-info">
                <span class="comp-name">${item.name}</span>
                <span class="comp-meta">${item.type.replace('OPTICAL_', '')} • ${item.tag_id}</span>
            </div>
            ${actionBtn}
        `;
        
        if (!isAlreadyPlaced) {
            // Click to Request Placement
            const btn = card.querySelector('.req-btn');
            btn.addEventListener('click', async () => {
                log(`Requesting placement for ${item.name}...`, "info");
                try {
                    const res = await fetch('/api/components', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(item)
                    });
                    if (res.ok) {
                        const data = await res.json();
                        log(data.message, "success");
                        libraryPopup.style.display = 'none';
                    } else {
                        log("Placement request failed.", "error");
                    }
                } catch (e) {
                    log("Network error requesting placement.", "error");
                }
            });
        }
        
        libraryList.appendChild(card);
    });
}

// Removed: handleInventoryDragStart for new items (logic replaced by Request to Place)

libraryClose.addEventListener('click', () => {
    libraryPopup.style.display = 'none';
});

function renderRecipes() {
    recipeList.innerHTML = '';
    if (store.availableRecipes.length === 0) {
        recipeList.innerHTML = '<div style="color: #64748b; font-size: 11px; padding: 10px; text-align: center;">No recipes saved.</div>';
        return;
    }

    store.availableRecipes.forEach(recipe => {
        const item = document.createElement('div');
        item.style.backgroundColor = 'rgba(255,255,255,0.03)';
        item.style.border = '1px solid #2a2e36';
        item.style.borderRadius = '6px';
        item.style.padding = '8px';
        item.style.marginBottom = '6px';
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.justifyContent = 'space-between';

        item.innerHTML = `
            <div style="overflow: hidden;">
                <div style="font-weight: 500; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${recipe.name}</div>
                <div style="font-size: 10px; color: #64748b;">${recipe.steps.length} steps</div>
            </div>
        `;

        const playBtn = document.createElement('button');
        playBtn.className = 'btn btn-primary';
        playBtn.style.padding = '4px 8px';
        playBtn.style.fontSize = '10px';
        playBtn.style.width = 'auto';
        playBtn.innerHTML = '<span class="material-icons-round" style="font-size: 14px;">play_arrow</span>';
        playBtn.title = "Run Recipe";
        playBtn.onclick = () => playRecipe(recipe.id);

        item.appendChild(playBtn);
        recipeList.appendChild(item);
    });
}

// --- Recipe Controls ---

function updateRecipeEditorList() {
    recipeStepsContainer.innerHTML = '';
    if (store.currentRecipeSteps.length === 0) {
        recipeStepsContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b; font-size: 12px;">No steps recorded yet.</div>';
        return;
    }

    store.currentRecipeSteps.forEach((step, index) => {
        const item = document.createElement('div');
        item.className = 'recipe-step-item';
        
        let desc = `${step.component}`;
        if (step.action === 'MOVE_COMPONENT') {
            desc += ` to (${step.parameters.target_x.toFixed(1)}, ${step.parameters.target_y.toFixed(1)})`;
        } else if (step.action === 'OPTIMIZE') {
            desc += ` with ${step.parameters.strategy || 'NEWTON'}`;
        }

        item.innerHTML = `
            <div class="step-num">${index + 1}</div>
            <div class="step-action">${step.action === 'MOVE_COMPONENT' ? 'MOVE' : 'OPTIMIZE'}</div>
            <div class="step-desc">${desc}</div>
            <div class="material-icons-round step-del" title="Remove Step">delete</div>
        `;

        item.querySelector('.step-del').addEventListener('click', () => deleteStep(index));
        recipeStepsContainer.appendChild(item);
    });
}

function deleteStep(index) {
    store.currentRecipeSteps.splice(index, 1);
    // Re-assign step numbers if needed, though mostly visual
    updateRecipeEditorList();
}

recordBtn.addEventListener('click', () => {
    store.isRecording = !store.isRecording; // Toggle recording
    
    if (store.isRecording) {
        store.currentRecipeSteps = [];
        recipeEditorName.value = `Recipe ${new Date().toLocaleTimeString()}`;
        updateRecipeEditorList();
        recIndicator.style.display = 'flex';
        recordBtn.classList.add('btn-primary'); // Highlight
        recordBtn.classList.remove('btn-secondary');
        log("Recording started. Perform actions on the canvas.", "warn");
    } else {
        recIndicator.style.display = 'none';
        recordBtn.classList.remove('btn-primary');
        recordBtn.classList.add('btn-secondary');
        log("Recording stopped.", "info");
    }
});

recipeEditorSave.addEventListener('click', async () => {
    if (store.currentRecipeSteps.length === 0) {
        alert("No actions recorded!");
        return;
    }
    
    const name = recipeEditorName.value || "Untitled Recipe";
    const id = name.toLowerCase().replace(/[^a-z0-9]/g, '_') + '_' + Math.floor(Math.random() * 1000);

    // Re-number steps just in case
    const steps = store.currentRecipeSteps.map((s, i) => ({ ...s, step: i + 1 }));

    const recipe = {
        id: id,
        name: name,
        steps: steps
    };

    try {
        const res = await fetch('/api/recipes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(recipe)
        });
        
        if (res.ok) {
            log(`Recipe "${name}" saved!`, "info");
            store.isRecording = false;
            recIndicator.style.display = 'none';
            recordBtn.classList.remove('btn-primary');
            recordBtn.classList.add('btn-secondary');
            fetchRecipes();
        }
    } catch (e) {
        log("Failed to save recipe.", "error");
    }
});

// --- Recipe Panel Dragging --- REMOVED
// Logic removed as panel is now static in sidebar


async function playRecipe(id) {
    // 1. Find the recipe object
    const recipe = store.availableRecipes.find(r => r.id === id);
    if (!recipe) {
        log("Recipe not found locally.", "error");
        return;
    }

    // 2. Check Requirements
    const reqs = checkRecipeRequirements(recipe);
    
    if (!reqs.valid) {
        showRecipeRequirementModal(recipe.name, reqs);
        return;
    }

    try {
        log(`Playing recipe ${id}...`, "info");
        const res = await fetch(`/api/recipes/${id}/play`, { method: 'POST' });
        if (res.ok) {
            log("Recipe execution started.", "info");
        } else {
            const err = await res.json();
            log(`Failed to start recipe: ${err.detail}`, "error");
        }
    } catch (e) {
        log("Network error starting recipe.", "error");
    }
}

function checkRecipeRequirements(recipe) {
    if (!store.labState || !store.labState.components) return { valid: false, error: "Lab state not loaded" };
    
    const missingRequestable = [];
    const missingUnknown = [];
    
    // Get all unique components referenced in recipe
    const requiredComponents = new Set();
    recipe.steps.forEach(step => {
        if (step.component) requiredComponents.add(step.component);
        // Fallback for older recipe formats if they used 'target' or 'target_id'
        if (step.target) requiredComponents.add(step.target);
    });
    
    requiredComponents.forEach(id => {
        // Check if it exists in the current lab state
        if (!store.labState.components[id]) {
            // Check if in catalog
            if (store.catalogMap[id]) {
                missingRequestable.push(store.catalogMap[id]);
            } else {
                missingUnknown.push(id);
            }
        }
    });
    
    return {
        valid: missingRequestable.length === 0 && missingUnknown.length === 0,
        missingRequestable,
        missingUnknown
    };
}

function showRecipeRequirementModal(recipeName, reqs) {
    const existing = document.getElementById('req-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'req-modal';
    overlay.style.position = 'fixed';
    overlay.style.top = '0'; overlay.style.left = '0';
    overlay.style.width = '100vw'; overlay.style.height = '100vh';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
    overlay.style.zIndex = '3000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(5px)';

    const card = document.createElement('div');
    card.style.backgroundColor = '#181b21';
    card.style.border = '1px solid #f59e0b'; // Amber warning
    card.style.borderRadius = '8px';
    card.style.padding = '24px';
    card.style.width = '500px';
    card.style.maxHeight = '80vh';
    card.style.overflowY = 'auto';
    card.style.boxShadow = '0 20px 50px rgba(0,0,0,0.7)';

    let html = `
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
            <span class="material-icons-round" style="font-size: 32px; color: #f59e0b;">warning_amber</span>
            <h2 style="margin: 0; color: #e2e8f0; font-size: 18px;">Components Missing</h2>
        </div>
        <p style="color: #94a3b8; font-size: 13px; margin-bottom: 20px;">
            The recipe <strong>"${recipeName}"</strong> cannot run because some components are not present in the lab.
        </p>
    `;

    if (reqs.missingUnknown.length > 0) {
        html += `
            <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 6px; padding: 12px; margin-bottom: 16px;">
                <div style="color: #ef4444; font-weight: 600; font-size: 12px; margin-bottom: 8px;">OUTDATED / UNKNOWN COMPONENTS</div>
                <div style="font-size: 12px; color: #cbd5e1;">
                    The following IDs are not found in the Catalog. The recipe may be outdated.
                    <ul style="margin: 8px 0 0 20px; padding: 0;">
                        ${reqs.missingUnknown.map(id => `<li>${id}</li>`).join('')}
                    </ul>
                </div>
            </div>
        `;
    }

    if (reqs.missingRequestable.length > 0) {
        html += `
            <div style="margin-bottom: 16px;">
                <div style="color: #e2e8f0; font-weight: 600; font-size: 12px; margin-bottom: 8px;">AVAILABLE TO REQUEST</div>
                <div id="req-list" style="display: flex; flex-direction: column; gap: 8px;">
                    <!-- Items injected via JS -->
                </div>
            </div>
        `;
    }

    html += `
        <div style="display: flex; justify-content: flex-end; gap: 12px; margin-top: 24px;">
            <button id="req-close-btn" class="btn btn-secondary" style="width: auto;">Close</button>
        </div>
    `;

    card.innerHTML = html;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    // Event Listeners
    document.getElementById('req-close-btn').onclick = () => overlay.remove();

    // Render Requestable Items
    const listContainer = document.getElementById('req-list');
    if (listContainer && reqs.missingRequestable.length > 0) {
        reqs.missingRequestable.forEach(item => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.justifyContent = 'space-between';
            row.style.background = '#0f1115';
            row.style.padding = '8px 12px';
            row.style.borderRadius = '4px';
            row.style.border = '1px solid #2a2e36';

            const icon = getComponentIcon(item.type);
            
            row.innerHTML = `
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span class="material-icons-round" style="color: #64748b; font-size: 18px;">${icon}</span>
                    <div>
                        <div style="font-size: 12px; color: #e2e8f0; font-weight: 500;">${item.name}</div>
                        <div style="font-size: 10px; color: #64748b;">${item.tag_id}</div>
                    </div>
                </div>
            `;

            const btn = document.createElement('button');
            btn.className = 'btn btn-primary';
            btn.style.width = 'auto';
            btn.style.padding = '4px 10px';
            btn.style.fontSize = '10px';
            btn.textContent = 'Request';
            
            btn.onclick = async () => {
                btn.textContent = 'Requesting...';
                btn.disabled = true;
                try {
                    const res = await fetch('/api/components', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(item)
                    });
                    if (res.ok) {
                        btn.textContent = 'Requested';
                        btn.style.backgroundColor = '#10b981';
                        btn.style.borderColor = '#10b981';
                    } else {
                        btn.textContent = 'Failed';
                        btn.disabled = false;
                    }
                } catch (e) {
                    btn.textContent = 'Error';
                    btn.disabled = false;
                }
            };

            row.appendChild(btn);
            listContainer.appendChild(row);
        });
    }
}


function log(message, type = 'info') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logOutput.prepend(entry);
    if (logOutput.children.length > 50) logOutput.removeChild(logOutput.lastChild);
}

/** Initialize store.ghostState[tagId] from lab state for Command Console moves. */
function ensureGhostForConsole(tagId) {
    if (store.ghostState[tagId]) return true;
    const comp = store.labState && store.labState.components && store.labState.components[tagId];
    if (!comp || comp.state !== 'PLACED' || !comp.pose) return false;
    store.ghostState[tagId] = {
        x: comp.pose.x,
        y: comp.pose.y,
        rotation: typeof comp.pose.rotation === 'number' ? comp.pose.rotation : 0
    };
    return true;
}

/** Same behavior as the Refresh State button (shared with Command Console). */
async function runLabStateRefresh() {
    log("Refreshing lab state (re-scan)...", "warn");
    const res = await fetch('/api/lab-state/refresh', { method: 'POST' });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Refresh failed');
    }

    const start = Date.now();
    while (Date.now() - start < 30000) {
        const stateRes = await fetch('/api/lab-state');
        const state = await stateRes.json();
        if (state && state.system_status === 'IDLE') break;
        await new Promise(r => setTimeout(r, 500));
    }

    store.forceGhostSync = true;
    await fetchLaserLine();
    await fetchLabState();
    checkVideoStatus();
}

function init() {
    log("Interface loaded.");
    fetchStrategies();
    fetchRecipes();
    fetchLabState();
    setInterval(fetchLabState, POLLING_INTERVAL);
    refreshBtn.addEventListener('click', async () => {
        try {
            await runLabStateRefresh();
        } catch (e) {
            console.error("Refresh state failed:", e);
            log(`Refresh failed: ${e.message || e}`, "error");
            showErrorModal("Refresh Failed", e.message || String(e));
        }
    });

    if (saveStateBtn) {
        saveStateBtn.addEventListener('click', async () => {
            try {
                const defaultName = `state_${new Date().toISOString().replace(/[:.]/g, '-')}`;
                const name = prompt("Save current lab state as:", defaultName);
                if (!name) return;

                const res = await fetch('/api/states/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Save failed');

                log(`Saved lab state: ${data.name || name}`, "info");
            } catch (e) {
                console.error("Save state failed:", e);
                showErrorModal("Save Failed", e.message || String(e));
            }
        });
    }

    if (loadStateBtn) {
        loadStateBtn.addEventListener('click', async () => {
            try {
                const defaultName = `state_last`;
                const name = prompt("Load lab state (saved name):", defaultName);
                if (!name) return;

                const res = await fetch('/api/states/load', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Load failed');

                // Force UI+ghost to match loaded state immediately.
                store.forceGhostSync = true;
                await fetchLabState();
                log(`Loaded lab state: ${data.name || name}`, "info");
            } catch (e) {
                console.error("Load state failed:", e);
                showErrorModal("Load Failed", e.message || String(e));
            }
        });
    }
    
    initVideoFeed();
    initUnifiedPanel();
}

// --- Unified Panel Logic (REMOVED - Panel is static) ---
function initUnifiedPanel() {
    // No dynamic minimization or tabs anymore
}

// --- Video Feed Logic ---
function initVideoFeed() {
    const fpsInput = document.getElementById('fps-input');
    if (fpsInput) {
        fpsInput.addEventListener('change', () => {
             const fps = Math.max(1, Math.min(60, parseInt(fpsInput.value) || 10));
             fpsInput.value = fps;
             // Reload video
             if (videoImg) {
                 // Base source is /api/video-feed/stream
                 const baseSrc = '/api/video-feed/stream';
                 videoImg.src = `${baseSrc}?fps=${fps}&t=${Date.now()}`;
                 log(`Video stream FPS set to ${fps}`, "info");
             }
        });
    }

    // Check status periodically
    setInterval(checkVideoStatus, 5000);
    checkVideoStatus();
}

// --- Table cam capture (on-demand, real lab only) ---
const tableCamBtn1 = document.getElementById('table-cam-btn-1');
const tableCamBtn2 = document.getElementById('table-cam-btn-2');
const tableCamCaptureBtn = document.getElementById('table-cam-capture-btn');
const tableCamImg = document.getElementById('table-cam-img');
const tableCamPlaceholder = document.getElementById('table-cam-placeholder');
const tableCamError = document.getElementById('table-cam-error');

function setTableCamSelection(camId) {
    store.selectedTableCam = camId;
    if (tableCamBtn1) {
        tableCamBtn1.classList.toggle('btn-primary', camId === 1);
        tableCamBtn1.classList.toggle('btn-secondary', camId !== 1);
    }
    if (tableCamBtn2) {
        tableCamBtn2.classList.toggle('btn-primary', camId === 2);
        tableCamBtn2.classList.toggle('btn-secondary', camId !== 2);
    }
}

if (tableCamBtn1) tableCamBtn1.addEventListener('click', () => setTableCamSelection(1));
if (tableCamBtn2) tableCamBtn2.addEventListener('click', () => setTableCamSelection(2));
setTableCamSelection(1);

if (tableCamCaptureBtn) {
    tableCamCaptureBtn.addEventListener('click', async () => {
        if (!tableCamImg || !tableCamPlaceholder || !tableCamError) return;
        tableCamPlaceholder.textContent = 'Capturing...';
        tableCamPlaceholder.style.display = 'block';
        tableCamImg.style.display = 'none';
        tableCamImg.src = '';
        tableCamError.style.display = 'none';
        try {
            const res = await fetch(`/api/table-cam/capture?cam_id=${store.selectedTableCam}`);
            if (res.ok) {
                const blob = await res.blob();
                if (store.tableCamLastBlobUrl) URL.revokeObjectURL(store.tableCamLastBlobUrl);
                store.tableCamLastBlobUrl = URL.createObjectURL(blob);
                tableCamImg.src = store.tableCamLastBlobUrl;
                tableCamImg.style.display = 'block';
                tableCamPlaceholder.style.display = 'none';
                tableCamPlaceholder.textContent = 'Click Capture to get image';
            } else {
                const err = (await res.json().catch(() => ({}))).detail || 'Capture failed (real lab only)';
                tableCamError.textContent = err;
                tableCamError.style.display = 'block';
                tableCamPlaceholder.style.display = 'none';
                tableCamPlaceholder.textContent = 'Click Capture to get image';
            }
        } catch (e) {
            tableCamError.textContent = e.message || 'Request failed';
            tableCamError.style.display = 'block';
            tableCamPlaceholder.style.display = 'none';
            tableCamPlaceholder.textContent = 'Click Capture to get image';
        }
    });
}

// --- Cobyla reference image (server-side BGR ndarray for CobylaAlignmentStrategy.reference_image) ---
const tableCamCobylaRefBtn = document.getElementById('table-cam-cobyla-ref-btn');
const tableCamCobylaClearBtn = document.getElementById('table-cam-cobyla-clear-btn');
const cobylaRefStatusEl = document.getElementById('cobyla-ref-status');

async function refreshCobylaRefStatus() {
    if (!cobylaRefStatusEl) return;
    try {
        const r = await fetch('/api/cobyla-reference-image/status');
        if (!r.ok) {
            cobylaRefStatusEl.textContent = 'Cobyla ref: status unavailable';
            return;
        }
        const d = await r.json();
        if (d.set && d.width && d.height) {
            cobylaRefStatusEl.textContent = `Cobyla ref: set (${d.width}×${d.height})`;
        } else {
            cobylaRefStatusEl.textContent = 'Cobyla ref: not set';
        }
    } catch (e) {
        cobylaRefStatusEl.textContent = 'Cobyla ref: status error';
    }
}

if (tableCamCobylaRefBtn) {
    tableCamCobylaRefBtn.addEventListener('click', async () => {
        if (!cobylaRefStatusEl) return;
        cobylaRefStatusEl.textContent = 'Cobyla ref: uploading…';
        try {
            const cap = await fetch(`/api/table-cam/capture?cam_id=${store.selectedTableCam}`);
            if (!cap.ok) {
                const err = (await cap.json().catch(() => ({}))).detail || `Capture failed (${cap.status})`;
                cobylaRefStatusEl.textContent = `Cobyla ref: ${err}`;
                log(err, 'warn');
                return;
            }
            const blob = await cap.blob();
            const res = await fetch('/api/cobyla-reference-image', {
                method: 'POST',
                headers: { 'Content-Type': 'image/png' },
                body: blob,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const err = data.detail || res.statusText || 'Upload failed';
                cobylaRefStatusEl.textContent = `Cobyla ref: ${err}`;
                log(err, 'warn');
                return;
            }
            log(data.message || 'Cobyla reference stored', 'info');
            await refreshCobylaRefStatus();
        } catch (e) {
            cobylaRefStatusEl.textContent = `Cobyla ref: ${e.message || 'failed'}`;
            log(e.message || 'Cobyla reference upload failed', 'error');
        }
    });
}

if (tableCamCobylaClearBtn) {
    tableCamCobylaClearBtn.addEventListener('click', async () => {
        try {
            const res = await fetch('/api/cobyla-reference-image', { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (res.ok) log(data.message || 'Cobyla reference cleared', 'info');
            await refreshCobylaRefStatus();
        } catch (e) {
            log(e.message || 'Clear failed', 'error');
        }
    });
}

refreshCobylaRefStatus();
setInterval(refreshCobylaRefStatus, 8000);

async function checkVideoStatus() {
    try {
        console.log(`[${new Date().toLocaleTimeString()}] Checking Video Status...`);
        const res = await fetch('/api/video-feed/status');
        if (res.ok) {
            const data = await res.json();
            if (data.connected) {
                console.log(`[${new Date().toLocaleTimeString()}] Video Status: Connected`);
                videoImg.style.display = 'block';
                videoPlaceholder.style.display = 'none';
                videoStatus.innerHTML = '● LIVE';
                videoStatus.style.color = '#10b981';
                // Force refresh with cache-busting so we don't get stuck showing an older mock SVG response.
                // Reload only if the placeholder was visible (i.e. previous state was OFFLINE).
                if (videoPlaceholder.style.display !== 'none') {
                    const fpsInput = document.getElementById('fps-input');
                    const fps = fpsInput ? fpsInput.value : 10;
                    videoImg.src = `${data.source}?fps=${fps}&t=${Date.now()}`;
                } else if (videoImg.src.indexOf('t=') === -1) {
                    // Also refresh once on connect if the src has no timestamp yet.
                    const fpsInput = document.getElementById('fps-input');
                    const fps = fpsInput ? fpsInput.value : 10;
                    videoImg.src = `${data.source}?fps=${fps}&t=${Date.now()}`;
                }
            } else {
                console.warn(`[${new Date().toLocaleTimeString()}] Video Status: Disconnected`);
                throw new Error("Disconnected");
            }
        } else {
             console.error(`[${new Date().toLocaleTimeString()}] Video Status Check Failed: HTTP ${res.status}`);
            throw new Error("API Error");
        }
    } catch (e) {
        console.error(`[${new Date().toLocaleTimeString()}] Video Error: ${e.message}`);
        videoImg.style.display = 'none';
        videoPlaceholder.style.display = 'flex';
        videoStatus.innerHTML = '● OFFLINE';
        videoStatus.style.color = '#ef4444';
    }
}

// --- Command Console (ES module `js/command-console.js`): dependency injection ---
window.__commandConsoleDeps = {
    executeSendCommand,
    sendCommand,
    checkCollision,
    log,
    runLabStateRefresh,
    ensureGhostForConsole,
    get ghostState() {
        return store.ghostState;
    },
    render,
    getCatalogEntry: (tagId) => store.catalogMap[tagId] || null,
    getLabState: () => store.labState
};

init();

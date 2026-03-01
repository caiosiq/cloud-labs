// Optical Digital Twin - Phase 4.5 (Golden Datastructures)
console.log("App.js loading...");

const CANVAS_WIDTH = 1000;
const CANVAS_HEIGHT = 700;
const MM_TO_PX = 1.0; 

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

// State
let labState = null;        
let ghostState = {};        
let draggingComponent = null; 
let isDragging = false;
let dragOffset = { x: 0, y: 0 };
let pendingCommands = new Set();
let selectedComponent = null; 
let availableStrategies = null; 
let availableRecipes = [];

// Optimization State
let isOptimizing = false;
let optimizationData = []; 

// Recipe State
let isRecording = false;
let currentRecipeSteps = [];

// Configuration
const POLLING_INTERVAL = 500; 

// --- 1. Networking ---

// We need the catalog to map IDs to Names
let catalogMap = {}; 

async function fetchCatalogMap() {
    try {
        const response = await fetch('/api/catalog');
        if (response.ok) {
            const catalog = await response.json();
            catalog.forEach(item => {
                catalogMap[item.tag_id] = item;
            });
            console.log("Catalog Loaded:", catalogMap);
            // Re-render UI once catalog is loaded to update names
            updateUI();
        }
    } catch (e) { console.error("Catalog fetch failed", e); }
}

// Call this early
fetchCatalogMap();

async function fetchStrategies() {
    availableStrategies = {
      "NEWTON": {
        "name": "Newton Strategy",
        "description": "Aligns a component by minimizing beam deviation.",
        "parameters": {
          "camera_number": { "type": "integer", "default": 1, "description": "Target Camera ID" },
          "axis": { "type": "string", "enum": ["x", "y"], "default": "x", "description": "Axis" },
          "tolerance_ratio": { "type": "float", "default": 0.05, "description": "Tolerance" }
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

async function fetchRecipes() {
    try {
        const response = await fetch('/api/recipes');
        if (response.ok) {
            availableRecipes = await response.json();
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
        
        labState = await response.json();
        console.log(`[${new Date().toLocaleTimeString()}] Received Lab State successfully.`);
        
        // Clear error modal if it was open (recovery)
        const existingError = document.getElementById('error-modal');
        if (existingError) existingError.remove();

        // Initial Sync: Use INTENT if available, else Physical Pose
        if (labState.components) {
            Object.entries(labState.components).forEach(([name, comp]) => {
                if (comp.state === 'PLACED') {
                    // Only update ghostState if it doesn't exist for this component
                    // or if we haven't touched it (no pending drag)
                    if (!ghostState[name] && !isDragging) {
                        if (comp.intent && comp.intent.nominal_pose) {
                            ghostState[name] = { ...comp.intent.nominal_pose };
                        } else {
                            ghostState[name] = { ...comp.pose };
                        }
                    }
                }
            });
        }
        
        // --- ADDED: Auto-refresh available components list for sidebar ---
        if (!labState.components || Object.keys(labState.components).length === 0) {
             // If lab state is empty, we should still show something if it's just initialized
             // but 'components' in labState might be empty if the file is empty.
             // We rely on 'updateUI' to handle rendering.
        }
        
        if (labState.system_status === 'IDLE') {
            pendingCommands.clear(); 
            if (isOptimizing) {
                isOptimizing = false; 
                log("Optimization sequence complete.", "info");
            }
        } else if (labState.system_status === 'OPTIMIZING') {
            isOptimizing = true;
            if (Math.random() > 0.5) {
                optimizationData.push({
                    step: optimizationData.length, 
                    value: Math.min(1.0, 0.2 + optimizationData.length * 0.05 + Math.random() * 0.1)
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

async function sendCommand(command) {
    try {
        log(`Sending command: ${command.action}`, "info");
        
        // RECIPE LOGIC: Capture command if recording
        if (isRecording) {
            const step = {
                step: currentRecipeSteps.length + 1,
                action: command.action,
                component: command.target_id,
                parameters: command.parameters || {}
            };
            currentRecipeSteps.push(step);
            updateRecipeEditorList();
            // We still execute it live so the user sees the result!
        }

        if (command.target_id) pendingCommands.add(command.target_id);
        
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(command)
        });
        
        if (response.status === 409) {
             log("System BUSY. Command rejected.", "warn");
             pendingCommands.delete(command.target_id);
             return;
        }

        const result = await response.json();
        log(`Server: ${result.message}`, "info");
        
        if (command.action === 'OPTIMIZE') {
            isOptimizing = true;
            optimizationData = []; 
        }

    } catch (error) {
        log(`Command failed: ${error.message}`, "error");
        if (command.target_id) pendingCommands.delete(command.target_id);
    }
}

// --- 2. Interaction Logic ---

function getComponentAtPosition(x, y) {
    for (const [name, pose] of Object.entries(ghostState)) {
        const dx = x - pose.x * MM_TO_PX;
        const dy = y - pose.y * MM_TO_PX;
        if (Math.sqrt(dx*dx + dy*dy) < 20) return { name, type: 'GHOST' };
    }
    return null;
}

canvas.addEventListener('mousedown', (e) => {
    if (labState && labState.system_status !== 'IDLE') return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = getComponentAtPosition(mouseX, mouseY);
    
    if (hit) {
        if (selectedComponent !== hit.name) {
            selectedComponent = hit.name;
            updateContextPanel(hit.name);
            render(); 
            log(`Selected ${hit.name}`, "info");
        } else {
            isDragging = true;
            draggingComponent = hit.name;
            dragOffset = {
                x: mouseX - ghostState[hit.name].x * MM_TO_PX,
                y: mouseY - ghostState[hit.name].y * MM_TO_PX
            };
        }
    } else {
        selectedComponent = null;
        contextPanel.style.display = 'none';
        render();
    }
});

function updateContextPanel(name) {
    const comp = labState.components[name];
    const pose = ghostState[name];
    
    let displayName = name;
    let properties = {};

    if (catalogMap[name]) {
        displayName = catalogMap[name].name;
        if (catalogMap[name].properties) {
            properties = catalogMap[name].properties;
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
    Object.entries(availableStrategies).forEach(([stratKey, strat]) => {
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
}

ctxMoveBtn.addEventListener('click', async () => {
    if (!selectedComponent) return;
    
    const tx = parseFloat(ctxX.value);
    const ty = parseFloat(ctxY.value);
    const trot = parseFloat(ctxRot.value);

    ghostState[selectedComponent].x = tx;
    ghostState[selectedComponent].y = ty;
    ghostState[selectedComponent].rotation = trot;
    
    await sendCommand({
        action: "MOVE_COMPONENT",
        target_id: selectedComponent,
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
    if (!isDragging || !draggingComponent) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    let newX = (mouseX - dragOffset.x) / MM_TO_PX;
    let newY = (mouseY - dragOffset.y) / MM_TO_PX;

    if (Math.abs(newY - 200) < 15) newY = 200;

    ghostState[draggingComponent].x = newX;
    ghostState[draggingComponent].y = newY;
    
    render();
});

canvas.addEventListener('mouseup', async (e) => {
    if (isDragging && draggingComponent) {
        isDragging = false;
        
        await sendCommand({
            action: "MOVE_COMPONENT",
            target_id: draggingComponent,
            parameters: {
                target_x: ghostState[draggingComponent].x,
                target_y: ghostState[draggingComponent].y,
                rotation: ghostState[draggingComponent].rotation
            }
        });
        
        draggingComponent = null;
    }
});

function handleInventoryDragStart(e, componentName) {
    e.dataTransfer.setData("text/plain", componentName);
}

canvas.addEventListener('dragover', (e) => e.preventDefault());

canvas.addEventListener('drop', (e) => {
        e.preventDefault();
        if (labState && labState.system_status !== 'IDLE') return;
    
        const componentName = e.dataTransfer.getData("text/plain");
        
        // --- MODIFIED: Allow dropping ANY component ID, even if not in labState yet ---
        if (componentName) {
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;
            
            const type = e.dataTransfer.getData("application/type") || "OPTICAL_MIRROR";
    
            // Initialize ghost state for new component immediately
            ghostState[componentName] = {
                x: mouseX / MM_TO_PX,
                y: mouseY / MM_TO_PX,
                rotation: 0
            };
    
            // Trigger move command which will create it in backend
            sendCommand({
                action: "MOVE_COMPONENT",
                target_id: componentName,
                parameters: {
                    target_x: ghostState[componentName].x,
                    target_y: ghostState[componentName].y,
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

// Update MouseDown Logic to Trigger Popup
canvas.addEventListener('mousedown', (e) => {
    if (labState && labState.system_status !== 'IDLE') return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = getComponentAtPosition(mouseX, mouseY);
    
    if (hit) {
        if (selectedComponent !== hit.name) {
            selectedComponent = hit.name;
            updateContextPanel(hit.name);
            render(); 
            log(`Selected ${hit.name}`, "info");
        } else {
            isDragging = true;
            draggingComponent = hit.name;
            dragOffset = {
                x: mouseX - ghostState[hit.name].x * MM_TO_PX,
                y: mouseY - ghostState[hit.name].y * MM_TO_PX
            };
            // Hide popup while dragging to avoid clutter
            // hideContextPopup(); // Removed
        }
    } else {
        selectedComponent = null;
        contextPanel.style.display = 'none';
        // hideContextPopup(); // Removed
        render();
    }
});

// Re-show popup after drag ends
canvas.addEventListener('mouseup', async (e) => {
    if (isDragging && draggingComponent) {
        isDragging = false;
        
        await sendCommand({
            action: "MOVE_COMPONENT",
            target_id: draggingComponent,
            parameters: {
                target_x: ghostState[draggingComponent].x,
                target_y: ghostState[draggingComponent].y,
                rotation: ghostState[draggingComponent].rotation
            }
        });
        
        // Re-open popup at new location (REMOVED - Context Panel is static)
        // const pose = ghostState[draggingComponent];
        // showContextPopup(draggingComponent, pose.x * MM_TO_PX, pose.y * MM_TO_PX, false);
        
        updateContextPanel(draggingComponent);
        draggingComponent = null;
    }
});


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

        sendCommand({
            action: "OPTIMIZE",
            target_id: selectedComponent, 
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
    
    ctx.fillStyle = '#2a2e36';
    const spacing = 25 * MM_TO_PX;
    for (let x = spacing; x < CANVAS_WIDTH; x += spacing) {
        for (let y = spacing; y < CANVAS_HEIGHT; y += spacing) {
            ctx.beginPath(); ctx.arc(x, y, 2, 0, Math.PI * 2); ctx.fill();
        }
    }
}

function drawLaserPath() {
    ctx.shadowBlur = 10;
    ctx.shadowColor = '#ff3b3b';
    ctx.strokeStyle = '#ff3b3b'; 
    ctx.lineWidth = 2;
    ctx.setLineDash([10, 10]); 
    ctx.beginPath();
    ctx.moveTo(0, 200 * MM_TO_PX);
    ctx.lineTo(CANVAS_WIDTH, 200 * MM_TO_PX);
    ctx.stroke();
    ctx.setLineDash([]); 
    ctx.shadowBlur = 0; 
}

function drawComponent(name, pose, type, mode = 'SOLID') {
    const x = pose.x * MM_TO_PX;
    const y = pose.y * MM_TO_PX;
    const rotation = pose.rotation * (Math.PI / 180); 

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rotation);

    if (mode === 'GHOST') ctx.globalAlpha = 0.5;
    if (mode === 'PENDING') ctx.globalAlpha = 0.7;

    ctx.shadowColor = (mode === 'GHOST') ? 'transparent' : 'rgba(0,0,0,0.5)';
    ctx.shadowBlur = (mode === 'GHOST') ? 0 : 10;
    
    // Selection Halo
    if (name === selectedComponent) {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(0, 0, 22, 0, Math.PI * 2); ctx.stroke();
    }

    if (mode === 'PENDING') {
        ctx.strokeStyle = '#f59e0b'; // Amber
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 2]);
        ctx.beginPath(); ctx.arc(0, 0, 18, 0, Math.PI * 2); ctx.stroke();
        ctx.setLineDash([]);
    }

    if (isOptimizing && pendingCommands.has(name)) {
        ctx.shadowColor = '#10b981'; // Green glow
        ctx.shadowBlur = 20;
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(0, 0, 20, 0, Math.PI * 2); ctx.stroke();
    }

    // Draw Specific Icons based on Catalog ID or Type
    const catalogItem = catalogMap[name];
    const catalogId = catalogItem ? catalogItem.id : null;

    if (catalogId === 'nd_filter') {
        // ND Filter: Dark Neutral (Black/Grey)
        ctx.fillStyle = '#111';
        ctx.fillRect(-12, -12, 24, 24);
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.strokeRect(-12, -12, 24, 24);
        // Dark Glass look
        ctx.fillStyle = 'rgba(20, 20, 20, 0.9)';
        ctx.fillRect(-10, -10, 20, 20);

    } else if (catalogId === 'filter_generic') {
        // Generic Filter: Colored (e.g. Red/Pink)
        ctx.fillStyle = '#333';
        ctx.fillRect(-12, -12, 24, 24);
        ctx.strokeStyle = '#f87171'; // Reddish border
        ctx.lineWidth = 2;
        ctx.strokeRect(-12, -12, 24, 24);
        // Tinted Glass look
        ctx.fillStyle = 'rgba(248, 113, 113, 0.3)';
        ctx.fillRect(-10, -10, 20, 20);

    } else if (catalogId === 'cam_gripper_1' || catalogId === 'cam_gripper_2' || type === 'OPTICAL_CAMERA') {
        // Camera
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(-15, -15, 30, 30);
        // Lens ring
        ctx.fillStyle = '#000';
        ctx.beginPath(); ctx.arc(0, 0, 10, 0, Math.PI * 2); ctx.fill();
        // Sensor reflection
        ctx.fillStyle = '#3b82f6'; // Blueish reflection
        ctx.beginPath(); ctx.arc(0, 0, 4, 0, Math.PI * 2); ctx.fill();
        // Direction indicator
        ctx.fillStyle = '#ef4444';
        ctx.beginPath(); ctx.moveTo(0, -18); ctx.lineTo(-4, -24); ctx.lineTo(4, -24); ctx.fill();

    } else if (catalogId === 'mirror_curved') {
        // Curved Mirror (Semi-Circle Concave)
        const radius = 15;
        
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
        ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.moveTo(0, -18); ctx.lineTo(0, 18); ctx.stroke();
        // Mount backing
        ctx.fillStyle = '#444'; ctx.fillRect(-6, -18, 6, 36);
        // Reflective side hint
        ctx.strokeStyle = 'rgba(255,255,255,0.5)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(2, -15); ctx.lineTo(2, 15); ctx.stroke();

    } else if (catalogId === 'beam_block') {
        // Beam Block: Solid dark block with cross
        ctx.fillStyle = '#111';
        ctx.fillRect(-12, -12, 24, 24);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.beginPath(); 
        ctx.moveTo(-12, -12); ctx.lineTo(12, 12);
        ctx.moveTo(12, -12); ctx.lineTo(-12, 12);
        ctx.stroke();
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 2;
        ctx.strokeRect(-12, -12, 24, 24);

    } else if (catalogId === 'beam_splitter' || type === 'OPTICAL_BEAMSPLITTER') {
        // Beam Splitter: Cube
        ctx.fillStyle = 'rgba(200, 200, 200, 0.1)';
        ctx.strokeStyle = '#888'; ctx.lineWidth = 2;
        ctx.strokeRect(-14, -14, 28, 28);
        // Diagonal coating
        ctx.strokeStyle = 'rgba(100, 200, 255, 0.8)';
        ctx.beginPath(); ctx.moveTo(-14, -14); ctx.lineTo(14, 14); ctx.stroke();

    } else if (catalogId === 'lens_main' || type === 'OPTICAL_LENS') {
        // Lens: Ellipse
        ctx.fillStyle = 'rgba(100, 200, 255, 0.3)';
        ctx.strokeStyle = 'rgba(150, 220, 255, 0.9)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.ellipse(0, 0, 6, 20, 0, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        
    } else if (catalogId === 'crystal_main' || type === 'OPTICAL_CRYSTAL') {
        // Crystal: Hexagon or Rectangle
        ctx.fillStyle = 'rgba(236, 72, 153, 0.3)'; // Pinkish
        ctx.strokeStyle = '#ec4899';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-10, -20); ctx.lineTo(10, -20);
        ctx.lineTo(15, 0);
        ctx.lineTo(10, 20); ctx.lineTo(-10, 20);
        ctx.lineTo(-15, 0);
        ctx.closePath();
        ctx.fill(); ctx.stroke();

    } else {
        // Default / Unknown
        ctx.fillStyle = '#C0C0C0'; 
        ctx.beginPath(); ctx.arc(0, 0, 14, 0, Math.PI * 2); ctx.fill();
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
    if (catalogMap[name]) {
        displayName = catalogMap[name].name;
    }
    
    ctx.fillText(displayName, 0, -25);
    
    if (mode === 'PENDING') {
        ctx.fillStyle = '#f59e0b';
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText("MOVING...", 0, 25);
    }
    if (isOptimizing && pendingCommands.has(name)) {
        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.fillText("OPTIMIZING...", 0, 25);
    }

    ctx.restore();
}

function drawOptimizationGraph() {
    if (!isOptimizing || optimizationData.length === 0) return;

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

    optimizationData.forEach((point, i) => {
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

    if (!labState) return;

    // 1. Draw Physical Components (Solid)
    Object.entries(labState.components).forEach(([name, comp]) => {
        if (comp.state === 'PLACED') {
            drawComponent(name, comp.pose, comp.type, 'SOLID');
        }
    });

    // 2. Draw Ghost Components (Intent)
    // We now use ghostState which is initialized from comp.intent.nominal_pose
    Object.entries(ghostState).forEach(([name, pose]) => {
        const type = labState.components[name]?.type || 'UNKNOWN';
        const isPending = pendingCommands.has(name);
        drawComponent(name, pose, type, isPending ? 'PENDING' : 'GHOST');
        
        // Draw Drift Line (Nominal vs Physical)
        const physical = labState.components[name];
        if (physical && physical.state === 'PLACED') {
            ctx.strokeStyle = isPending ? '#f59e0b' : 'rgba(255, 255, 255, 0.2)';
            ctx.setLineDash([5, 5]);
            ctx.beginPath();
            ctx.moveTo(physical.pose.x * MM_TO_PX, physical.pose.y * MM_TO_PX);
            ctx.lineTo(pose.x * MM_TO_PX, pose.y * MM_TO_PX);
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
    if (!labState) return;

    const status = labState.system_status;
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
    if (selectedComponent) {
        // updateSidebarSelection(); // function removed previously
    }

    componentList.innerHTML = '';
    // libraryList.innerHTML = ''; // DO NOT TOUCH LIBRARY LIST IN UPDATE LOOP
    
    const components = labState.components || {};
    
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
        if (name === selectedComponent) card.style.borderColor = '#3b82f6'; 
        // card.draggable = true; // Dragging from sidebar to move? Maybe, but mostly we select and use context panel.
        
        // card.addEventListener('dragstart', (e) => handleInventoryDragStart(e, name));
        const isPlaced = comp.state === 'PLACED';
        
        // Resolve Real Name from Catalog using Tag ID
        let displayName = name; // Default to key if unknown
        let displayType = comp.type;
        let unknownTag = false;

        if (catalogMap[comp.id]) {
            displayName = catalogMap[comp.id].name;
            // displayType = catalogMap[comp.id].type; // Ensure type matches catalog
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

        card.innerHTML = `
            <div class="comp-icon material-icons-round">${icon}</div>
            <div class="comp-info">
                <span class="comp-name" style="${unknownTag ? 'color: #f59e0b;' : ''}">${displayName}</span>
                <span class="comp-meta">${displayType.replace('OPTICAL_', '')} • ${comp.id}</span>
            </div>
            ${statusDot}
        `;
        
        // Click listener for selection
        card.addEventListener('click', () => {
            selectedComponent = name;
            updateContextPanel(name);
            render();
        });

        componentList.appendChild(card);
    });

    render();
}

// --- Library Controls ---
let catalogCache = null;

addComponentBtn.addEventListener('click', async () => {
    libraryPopup.style.display = 'block'; // Show immediately
    
    // Use cached catalog if available to avoid refetching
    // if (catalogCache) {
    //    renderCatalog(catalogCache);
    //    return;
    // }

    libraryList.innerHTML = '<div style="padding:10px; text-align:center; color:#64748b">Loading catalog...</div>';
    
    try {
        const response = await fetch('/api/catalog');
        if (!response.ok) throw new Error("Failed to load catalog");
        catalogCache = await response.json();
        renderCatalog(catalogCache);
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
    const labComponents = labState ? labState.components : {};
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
    if (availableRecipes.length === 0) {
        recipeList.innerHTML = '<div style="color: #64748b; font-size: 11px; padding: 10px; text-align: center;">No recipes saved.</div>';
        return;
    }

    availableRecipes.forEach(recipe => {
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
    if (currentRecipeSteps.length === 0) {
        recipeStepsContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b; font-size: 12px;">No steps recorded yet.</div>';
        return;
    }

    currentRecipeSteps.forEach((step, index) => {
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
    currentRecipeSteps.splice(index, 1);
    // Re-assign step numbers if needed, though mostly visual
    updateRecipeEditorList();
}

recordBtn.addEventListener('click', () => {
    isRecording = !isRecording; // Toggle recording
    
    if (isRecording) {
        currentRecipeSteps = [];
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
    if (currentRecipeSteps.length === 0) {
        alert("No actions recorded!");
        return;
    }
    
    const name = recipeEditorName.value || "Untitled Recipe";
    const id = name.toLowerCase().replace(/[^a-z0-9]/g, '_') + '_' + Math.floor(Math.random() * 1000);

    // Re-number steps just in case
    const steps = currentRecipeSteps.map((s, i) => ({ ...s, step: i + 1 }));

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
            isRecording = false;
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
    const recipe = availableRecipes.find(r => r.id === id);
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
    if (!labState || !labState.components) return { valid: false, error: "Lab state not loaded" };
    
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
        if (!labState.components[id]) {
            // Check if in catalog
            if (catalogMap[id]) {
                missingRequestable.push(catalogMap[id]);
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

function init() {
    log("Interface loaded.");
    fetchStrategies();
    fetchRecipes();
    fetchLabState();
    setInterval(fetchLabState, POLLING_INTERVAL);
    refreshBtn.addEventListener('click', () => {
        log("Forcing state sync...", "warn");
        fetchLabState();
        checkVideoStatus();
    });
    
    initVideoFeed();
    initUnifiedPanel();
}

// --- Unified Panel Logic (REMOVED - Panel is static) ---
function initUnifiedPanel() {
    // No dynamic minimization or tabs anymore
}

// --- Video Feed Logic ---
function initVideoFeed() {
    // Check status periodically
    setInterval(checkVideoStatus, 5000);
    checkVideoStatus();
}

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
                // Refresh src to retry connection if it was broken
                if (videoImg.src.indexOf(data.source) === -1) {
                    videoImg.src = data.source;
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

init();

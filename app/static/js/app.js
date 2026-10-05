/**
 * NexusDude Frontend Core
 * Manejo interactivo de Canvas, Búsqueda de Inventario NetBox, Mapas y Submapas
 */

let stage = null;
let linksLayer = null;
let nodesLayer = null;
let uiLayer = null; // Capa superior para selección de área y herramientas interactivas
let layer = null; // Alias para compatibilidad con nodesLayer
let currentMap = null;
let currentUser = null;
let snapToGrid = true;
const GRID_SIZE = 20;

// Estado de modo conexión de enlaces
let linkMode = false;
let linkSourceNode = null;
const linkTargetNodes = new Set(); // Conjunto de nodos destino seleccionados en Paso 2 de Modo Enlace

// Referencias de shapes en Konva
const nodeGroups = new Map();
const linkLines = new Map();

// Estado de selección
let selectedNode = null;
const selectedNodes = new Set(); // Conjunto de nodos en selección múltiple
let selectionRect = null;
let isAreaSelecting = false;
let wasDragSelecting = false;
let selectionStartPos = { x: 0, y: 0 };

// Helper para obtener el color del estado de un nodo o submapa (reglas Red, Yellow, Green)
function getNodeStatusColor(status, isSubmap = false) {
  if (status === 'warning') return '#f59e0b';
  if (status === 'down' || status === 'error') return '#ef4444';
  if (status === 'ok' || status === 'active') return '#10b981';
  return '#64748b';
}

// Modal State
let isCreatingSubmap = false;

// Definición global inmediata para evitar errores de ReferenceError
window.loadMapsTree = function(filterText = '') {
  if (typeof refreshMapsTabList === 'function') {
    return refreshMapsTabList(filterText);
  }
};

window.openSettingsModal = function() {
  const modal = document.getElementById('modal-settings');
  if (modal) {
    modal.style.display = 'flex';
    if (typeof loadSettingsData === 'function') {
      loadSettingsData();
    }
  }
};

window.closeSettingsModal = function() {
  const modal = document.getElementById('modal-settings');
  if (modal) {
    modal.style.display = 'none';
  }
};

// ─── 1. Autenticación SSO y Detección de Tokens ─────────────────────────────
function initSSOAuth() {
  const hash = window.location.hash;
  const urlParams = new URLSearchParams(window.location.search);
  let foundToken = null;

  if (hash) {
    const hashParams = new URLSearchParams(hash.replace(/^#/, ''));
    foundToken = hashParams.get('token') || hashParams.get('access_token');
    if (foundToken) {
      hashParams.delete('token');
      hashParams.delete('access_token');
      const cleanHash = hashParams.toString() ? `#${hashParams.toString()}` : '';
      history.replaceState(null, null, window.location.pathname + cleanHash);
    }
  }
  if (!foundToken) {
    foundToken = urlParams.get('token');
  }

  if (foundToken) {
    API.setToken(foundToken);
  }
}

// ─── 2. Inicialización del Canvas Konva ──────────────────────────────────────
function initCanvas() {
  const container = document.getElementById('canvas-container');
  const width = container.clientWidth || (window.innerWidth - 360);
  const height = container.clientHeight || (window.innerHeight - 58);

  // Optimización de rendimiento gráfico: limitar pixelRatio en pantallas 4K / Retina
  Konva.pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);

  stage = new Konva.Stage({
    container: 'canvas-container',
    width: width,
    height: height,
    draggable: true
  });

  // Capa 1 (inferior): Enlaces de red y líneas de conexión
  linksLayer = new Konva.Layer();
  // Capa 2 (intermedia): Nodos y tarjetas de dispositivos
  nodesLayer = new Konva.Layer();
  // Capa 3 (superior): Recuadro de selección múltiple (Marquee)
  uiLayer = new Konva.Layer();
  layer = nodesLayer; // Mantener alias

  stage.add(linksLayer);
  stage.add(nodesLayer);
  stage.add(uiLayer);

  // Desactivar menú contextual por defecto en el canvas para usar Clic Derecho en Selección Múltiple
  container.addEventListener('contextmenu', (e) => e.preventDefault());

  // Rectángulo visual de selección múltiple (Marquee Selection) en uiLayer
  selectionRect = new Konva.Rect({
    fill: 'rgba(56, 189, 248, 0.2)',
    stroke: '#38bdf8',
    strokeWidth: 2,
    dash: [6, 4],
    visible: false,
    listening: false
  });
  uiLayer.add(selectionRect);

  // Zoom con rueda del mouse
  const scaleBy = 1.1;
  stage.on('wheel', (e) => {
    e.evt.preventDefault();
    const oldScale = stage.scaleX();
    const pointer = stage.getPointerPosition();

    const mousePointTo = {
      x: (pointer.x - stage.x()) / oldScale,
      y: (pointer.y - stage.y()) / oldScale
    };

    let direction = e.evt.deltaY > 0 ? -1 : 1;
    if (e.evt.ctrlKey) direction = -direction;

    const newScale = direction > 0 ? oldScale * scaleBy : oldScale / scaleBy;
    if (newScale < 0.25 || newScale > 3.5) return;

    stage.scale({ x: newScale, y: newScale });
    stage.position({
      x: pointer.x - mousePointTo.x * newScale,
      y: pointer.y - mousePointTo.y * newScale
    });
    stage.batchDraw();
  });

  // Evento Mousedown: Iniciar rectángulo de selección con Clic Derecho (button 2)
  stage.on('mousedown touchstart', (e) => {
    const isRightBtn = e.evt && e.evt.button === 2;

    if (isRightBtn) {
      if (e.evt) e.evt.preventDefault();
      isAreaSelecting = true;
      stage.draggable(false);

      const pointer = stage.getPointerPosition();
      const transform = stage.getAbsoluteTransform().copy().invert();
      const pos = transform.point(pointer);
      selectionStartPos = pos;

      selectionRect.setAttrs({
        x: pos.x,
        y: pos.y,
        width: 0,
        height: 0,
        visible: true
      });
      uiLayer.batchDraw();
    }
  });

  // Evento Mousemove: Redimensionar rectángulo de selección
  stage.on('mousemove touchmove', (e) => {
    if (!isAreaSelecting) return;
    const pointer = stage.getPointerPosition();
    if (!pointer) return;

    const transform = stage.getAbsoluteTransform().copy().invert();
    const pos = transform.point(pointer);

    const x = Math.min(selectionStartPos.x, pos.x);
    const y = Math.min(selectionStartPos.y, pos.y);
    const width = Math.abs(pos.x - selectionStartPos.x);
    const height = Math.abs(pos.y - selectionStartPos.y);

    selectionRect.setAttrs({
      x: x,
      y: y,
      width: width,
      height: height
    });
    uiLayer.batchDraw();
  });

  // Evento Mouseup: Finalizar selección de área y marcar o conectar nodos encerrados
  const finishSelection = async (e) => {
    if (isAreaSelecting) {
      isAreaSelecting = false;
      const selW = selectionRect.width();
      const selH = selectionRect.height();
      const selX = selectionRect.x();
      const selY = selectionRect.y();

      selectionRect.visible(false);
      uiLayer.batchDraw();
      if (!linkMode) {
        stage.draggable(true);
      }

      if (selW > 6 && selH > 6) {
        wasDragSelecting = true;
        setTimeout(() => { wasDragSelecting = false; }, 150);

        // Recorrer todos los nodos del mapa actual y verificar intersección por coordenadas
        const enclosedNodes = [];
        if (currentMap && currentMap.nodes) {
          currentMap.nodes.forEach(node => {
            const grp = nodeGroups.get(node.id);
            if (grp) {
              const { nodeWidth, nodeHeight } = computeNodeDimensions(node);
              const nx = grp.x();
              const ny = grp.y();

              // Colisión AABB en coordenadas mundiales
              if (nx < selX + selW && nx + nodeWidth > selX &&
                  ny < selY + selH && ny + nodeHeight > selY) {
                enclosedNodes.push(node);
              }
            }
          });
        }

        if (linkMode && linkSourceNode) {
          // Conectar por arrastre en Modo Enlace (Paso 2)
          const targetCandidates = enclosedNodes.filter(n => n.id !== linkSourceNode.id);
          if (targetCandidates.length > 0) {
            await connectMultipleTargetNodes(linkSourceNode, targetCandidates);
          }
        } else {
          deselectNode();
          clearMultiSelection();
          enclosedNodes.forEach(node => addNodeToMultiSelection(node));

          if (selectedNodes.size > 0) {
            showMultiSelectionNotice(selectedNodes.size);
          } else {
            hideMultiSelectionNotice();
          }
        }
      }
    }
  };

  stage.on('mouseup touchend', finishSelection);
  window.addEventListener('mouseup', (e) => {
    if (isAreaSelecting) finishSelection(e);
  });

  // Deseleccionar al hacer clic izquierdo en fondo vacío
  stage.on('click tap', (e) => {
    if (wasDragSelecting) return;
    if (e.target === stage && (!e.evt || e.evt.button === 0)) {
      if (linkMode) {
        cancelLinkMode();
      } else {
        deselectNode();
        clearMultiSelection();
      }
    }
  });

  // Redimensionar automáticamente
  window.addEventListener('resize', () => {
    const newW = container.clientWidth;
    const newH = container.clientHeight;
    stage.width(newW);
    stage.height(newH);
    stage.batchDraw();
  });

  setupDragAndDrop();
}

// ─── 3. Drag & Drop de Equipos hacia el Canvas ──────────────────────────────
function setupDragAndDrop() {
  const dropzone = document.getElementById('canvas-dropzone');

  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });

  dropzone.addEventListener('drop', async (e) => {
    e.preventDefault();
    const rawData = e.dataTransfer.getData('application/json');
    if (!rawData || !currentMap) return;

    try {
      const device = JSON.parse(rawData);
      const stageBox = stage.container().getBoundingClientRect();

      // Coordenadas mundiales dentro del canvas
      const rawX = (e.clientX - stageBox.left - stage.x()) / stage.scaleX();
      const rawY = (e.clientY - stageBox.top - stage.y()) / stage.scaleY();

      let targetX = rawX;
      let targetY = rawY;
      if (snapToGrid) {
        targetX = Math.round(rawX / GRID_SIZE) * GRID_SIZE;
        targetY = Math.round(rawY / GRID_SIZE) * GRID_SIZE;
      }

      const newNode = await API.createNode({
        map_id: currentMap.id,
        device_id: device.id,
        name: device.name,
        ip: device.ip,
        device_type: device.role || 'generic',
        site_name: device.site,
        x: targetX,
        y: targetY,
        status: 'ok',
        extra_data: {
          manufacturer: device.manufacturer || 'Genérico',
          model: device.model || '',
          serial: device.serial || '',
          role: device.role || device.device_type || 'Dispositivo',
          role_color: device.role_color || '',
          status: device.status || 'active'
        }
      });

      currentMap.nodes.push(newNode);
      renderNode(newNode);
      nodesLayer.batchDraw();
      selectNode(newNode);

    } catch (err) {
      console.error('Error soltando dispositivo en lienzo:', err);
    }
  });
}

// ─── 3b. Redimensionamiento Horizontal del Sidebar con el Mouse ──────────────
function setupSidebarResizer() {
  const resizer = document.getElementById('sidebar-resizer');
  const sidebar = document.querySelector('.sidebar');
  if (!resizer || !sidebar) return;

  // Restaurar ancho previo guardado en localStorage si es válido
  try {
    const savedWidth = localStorage.getItem('nexusdude_sidebar_width');
    if (savedWidth) {
      const w = parseInt(savedWidth, 10);
      const maxAllowed = Math.min(window.innerWidth * 0.75, 1000);
      if (!isNaN(w) && w >= 280 && w <= maxAllowed) {
        sidebar.style.width = `${w}px`;
      }
    }
  } catch (e) {
    console.warn('No se pudo acceder a localStorage para ancho del sidebar:', e);
  }

  let isResizing = false;
  let startX = 0;
  let startWidth = 0;

  resizer.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return; // Solo clic izquierdo principal
    isResizing = true;
    startX = e.clientX;
    startWidth = sidebar.getBoundingClientRect().width;
    sidebar.classList.add('resizing');
    resizer.classList.add('resizing');
    document.body.classList.add('sidebar-resizing-active');
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!isResizing) return;
    const deltaX = e.clientX - startX;
    const minW = 280;
    const maxW = Math.min(window.innerWidth * 0.75, 1000);
    let newWidth = Math.round(startWidth + deltaX);
    if (newWidth < minW) newWidth = minW;
    if (newWidth > maxW) newWidth = maxW;

    sidebar.style.width = `${newWidth}px`;

    // Redimensionar el canvas de Konva inmediatamente para respuesta fluida
    if (typeof stage !== 'undefined' && stage) {
      const container = document.getElementById('canvas-container');
      if (container) {
        stage.width(container.clientWidth);
        stage.height(container.clientHeight);
        stage.batchDraw();
      }
    }
  });

  window.addEventListener('mouseup', () => {
    if (!isResizing) return;
    isResizing = false;
    sidebar.classList.remove('resizing');
    resizer.classList.remove('resizing');
    document.body.classList.remove('sidebar-resizing-active');

    const finalWidth = Math.round(sidebar.getBoundingClientRect().width);
    try {
      localStorage.setItem('nexusdude_sidebar_width', finalWidth);
    } catch (e) {}

    window.dispatchEvent(new Event('resize'));
  });
}

// ─── 4. Renderizado de Nodos y Enlaces en Konva ──────────────────────────────
// Colores base predeterminados según funciones/roles de NetBox
const DEFAULT_NETBOX_ROLE_COLORS = {
  'router': '#8bc34a',
  'core': '#8bc34a',
  'gateway': '#8bc34a',
  'borde': '#8bc34a',
  'switch': '#4caf50',
  'distribucion': '#4caf50',
  'olt': '#e91e63',
  'ap': '#2196f3',
  'access point': '#2196f3',
  'sector access point': '#ff9800',
  'sector': '#ff9800',
  'cpe': '#f44336',
  'radio': '#2196f3',
  'ptp': '#ff9800',
  'zona wifi': '#ff5722',
  'hotspot': '#ffeb3b',
  'site monitor': '#ff66ff',
  'bridge': '#9e9e9e',
  'submap': '#9333ea',
  'parent_map': '#0284c7'
};

// Mapa en memoria sincronizado en tiempo real desde NetBox API
const netboxRoleColorsMap = new Map();

function hexToRgba(hex, alpha = 0.22) {
  if (!hex) return null;
  let clean = String(hex).replace('#', '').trim();
  if (clean.length === 3) {
    clean = clean.split('').map(c => c + c).join('');
  }
  if (clean.length !== 6) return null;
  const num = parseInt(clean, 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function getNodeRoleColor(node) {
  if (!node) return null;
  if (node.device_type === 'parent_map' || node.extra_data?.is_parent_shortcut) {
    return '#0284c7';
  }
  if (node.device_type === 'submap') {
    return '#7c3aed';
  }
  const roleName = (node.extra_data?.role || node.device_type || '').toLowerCase().trim();
  // 1. Buscar por nombre de rol en cache dinámico de NetBox (prioridad alta para reflejar cambios de NetBox en vivo)
  if (roleName && netboxRoleColorsMap.has(roleName)) {
    const c = netboxRoleColorsMap.get(roleName);
    if (c) return c.startsWith('#') ? c : `#${c}`;
  }
  // 2. Color explícito en extra_data de NetBox
  const explicitColor = node.extra_data?.role_color;
  if (explicitColor && String(explicitColor).trim()) {
    const c = String(explicitColor).trim();
    return c.startsWith('#') ? c : `#${c}`;
  }
  // 3. Buscar en tabla de correspondencia predeterminada
  for (const [key, color] of Object.entries(DEFAULT_NETBOX_ROLE_COLORS)) {
    if (roleName.includes(key)) {
      return color;
    }
  }
  return '#475569';
}

function getRoleIcon(deviceType = '', extraData = null) {
  if (deviceType === 'parent_map' || extraData?.is_parent_shortcut) return '⬆️';
  const t = (deviceType || '').toLowerCase();
  if (t.includes('parent')) return '⬆️';
  if (t.includes('router') || t.includes('core')) return '🖧';
  if (t.includes('switch')) return '⮀';
  if (t.includes('olt')) return '⚡';
  if (t.includes('ap') || t.includes('cpe') || t.includes('radio')) return '📡';
  if (t.includes('submap')) return '📁';
  return '🖥️';
}

// Medidor ultra-rápido de texto usando un canvas 2D en memoria
let _textMeasureContext = null;
function measureTextWidth(text, font) {
  if (!_textMeasureContext) {
    const c = document.createElement('canvas');
    _textMeasureContext = c.getContext('2d');
  }
  _textMeasureContext.font = font;
  return _textMeasureContext.measureText(text || '').width;
}

function computeNodeDimensions(node) {
  // ── Nodo Nota (Sticky Note) ──
  if (node.device_type === 'note') {
    const noteText = node.extra_data?.note_text || node.name || '';
    const lines = noteText.split('\n');
    const noteFont = '10px system-ui, -apple-system, sans-serif';
    let maxLineW = 0;
    lines.forEach(l => {
      const lw = measureTextWidth(l || ' ', noteFont);
      if (lw > maxLineW) maxLineW = lw;
    });
    const noteWidth  = Math.min(Math.max(120, Math.ceil(maxLineW) + 24), 320);
    const noteHeight = Math.max(56, lines.length * 14 + 24);
    return { nodeWidth: noteWidth, nodeHeight: noteHeight, subLabelText: '', pins: [] };
  }

  // ── Brazo FTTH / Ramal GPON ──
  if (node.device_type === 'ftth_branch') {
    const branchName = node.extra_data?.branch_name || node.name || 'Brazo FTTH';
    const portName = node.extra_data?.gpon_port || 'GPON';
    const titleFont = 'bold 10px system-ui, -apple-system, sans-serif';
    const nameW = measureTextWidth(`${portName}: ${branchName}`, titleFont);
    const branchWidth = Math.min(Math.max(130, Math.ceil(nameW) + 42), 270);
    const branchHeight = 36;
    return { nodeWidth: branchWidth, nodeHeight: branchHeight, subLabelText: '', pins: [] };
  }

  const isParentShortcut = node.device_type === 'parent_map' || !!node.extra_data?.is_parent_shortcut;
  const isSubmap = node.device_type === 'submap' || isParentShortcut;
  const pins = node.extra_data?.pins || [];
  const nameFont = 'bold 11px system-ui, -apple-system, sans-serif';
  const nameW = measureTextWidth(node.name, nameFont);

  let subLabelText = '';
  if (isParentShortcut) {
    const parentName = node.extra_data?.parent_map_name || 'Mapa Padre';
    subLabelText = `Subir a ${parentName} ➔`;
  } else if (isSubmap) {
    const count = node.extra_data?.device_count;
    if (count !== undefined && count !== null) {
      subLabelText = count === 0 ? 'Sin equipos ➔' : `${count} ${count === 1 ? 'equipo' : 'equipos'} ➔`;
    } else {
      subLabelText = 'Abrir Submapa ➔';
    }
  } else {
    subLabelText = node.ip || node.extra_data?.model || node.extra_data?.role || node.site_name || '0.0.0.0';
  }

  const subFont = isSubmap ? 'bold 9.5px system-ui, -apple-system, sans-serif' : 'normal 9.5px monospace';
  const subW = measureTextWidth(subLabelText, subFont);

  let maxPinW = 0;
  pins.forEach(p => {
    const pText = p.label || (isParentShortcut ? `⬅ ${p.remote_node_name}` : `➔ ${p.remote_node_name}`);
    const pw = measureTextWidth(pText, 'bold 9px monospace');
    if (pw > maxPinW) maxPinW = pw;
  });

  // Margen izquierdo del título: statusDot (14px) + gap + icon (16px) + gap = 44px + text + margen derecho (16px)
  const titleNeeded = 44 + nameW + 16;
  // Margen izquierdo del subtítulo: 14px + subW + margen derecho (16px)
  const subNeeded = 14 + subW + 16;
  const pinNeeded = maxPinW > 0 ? (28 + maxPinW + 16) : 0;

  const minWidth = isParentShortcut ? 170 : (isSubmap ? 150 : 136);
  const maxWidth = isParentShortcut ? 320 : (isSubmap ? 300 : 250);
  const nodeWidth = Math.min(Math.max(minWidth, Math.ceil(Math.max(titleNeeded, subNeeded, pinNeeded))), maxWidth);
  
  const baseHeight = isSubmap ? 56 : 52;
  const pinsHeight = pins.length * 20;
  const nodeHeight = baseHeight + pinsHeight;

  return { nodeWidth, nodeHeight, subLabelText, pins };
}

function getNodeHalfDimensions(nodeOrId) {
  const nodeId = typeof nodeOrId === 'object' ? nodeOrId.id : nodeOrId;
  const grp = nodeGroups.get(nodeId);
  if (grp) {
    const box = grp.findOne('.box');
    if (box) return { halfW: box.width() / 2, halfH: box.height() / 2 };
  }
  if (typeof nodeOrId === 'object') {
    const dims = computeNodeDimensions(nodeOrId);
    return { halfW: dims.nodeWidth / 2, halfH: dims.nodeHeight / 2 };
  }
  return { halfW: 68, halfH: 26 };
}

// ─── Renderizado de Nodo Nota (Sticky Note) ──────────────────────────────────
// Paleta de colores semitransparentes disponibles para notas
const NOTE_COLORS = {
  'yellow':  { bg: 'rgba(234, 179, 8,  0.18)', border: '#ca8a04', text: '#fef9c3' },
  'blue':    { bg: 'rgba(56,  189, 248, 0.16)', border: '#0284c7', text: '#e0f2fe' },
  'green':   { bg: 'rgba(34,  197, 94,  0.16)', border: '#16a34a', text: '#dcfce7' },
  'red':     { bg: 'rgba(239, 68,  68,  0.16)', border: '#dc2626', text: '#fee2e2' },
  'purple':  { bg: 'rgba(168, 85,  247, 0.16)', border: '#9333ea', text: '#f3e8ff' },
  'gray':    { bg: 'rgba(148, 163, 184, 0.14)', border: '#64748b', text: '#e2e8f0' },
  'orange':  { bg: 'rgba(249, 115, 22,  0.16)', border: '#ea580c', text: '#ffedd5' },
};

function _renderNoteNode(node) {
  const noteText  = node.extra_data?.note_text || node.name || '';
  const colorKey  = node.extra_data?.note_color || 'yellow';
  const theme     = NOTE_COLORS[colorKey] || NOTE_COLORS['yellow'];
  const { nodeWidth, nodeHeight } = computeNodeDimensions(node);
  const FOLD = 14; // tamaño del doblez esquina

  const group = new Konva.Group({
    x: node.x,
    y: node.y,
    draggable: true,
    id: node.id
  });
  group.isNote = true;

  // Fondo principal del sticky note (sin esquina superior-derecha)
  const bgPoints = [
    0,           0,
    nodeWidth - FOLD, 0,
    nodeWidth,       FOLD,
    nodeWidth,       nodeHeight,
    0,           nodeHeight
  ];
  const bg = new Konva.Line({
    points: bgPoints,
    closed: true,
    fill: theme.bg,
    stroke: theme.border,
    strokeWidth: 1.5,
    cornerRadius: 2,
    shadowColor: 'rgba(0,0,0,0.35)',
    shadowBlur: 6,
    shadowOffset: { x: 1, y: 2 },
    shadowOpacity: 0.5,
    perfectDrawEnabled: false,
    name: 'box'
  });

  // Triángulo del doblez en esquina superior-derecha
  const foldTriangle = new Konva.Line({
    points: [
      nodeWidth - FOLD, 0,
      nodeWidth,        FOLD,
      nodeWidth - FOLD, FOLD
    ],
    closed: true,
    fill: 'rgba(0,0,0,0.18)',
    stroke: theme.border,
    strokeWidth: 0.8,
    listening: false,
    perfectDrawEnabled: false
  });

  // Icono de nota en esquina superior-izquierda
  const iconTxt = new Konva.Text({
    x: 5, y: 4,
    text: '📝',
    fontSize: 10,
    listening: false,
    perfectDrawEnabled: false
  });

  // Texto de la nota (multilínea)
  const textEl = new Konva.Text({
    x: 8,
    y: 18,
    width: nodeWidth - 16,
    text: noteText,
    fontSize: 10,
    fontFamily: 'system-ui, -apple-system, sans-serif',
    fill: theme.text,
    wrap: 'word',
    ellipsis: true,
    lineHeight: 1.35,
    listening: false,
    perfectDrawEnabled: false,
    name: 'label'
  });

  group.add(bg);
  group.add(foldTriangle);
  group.add(iconTxt);
  group.add(textEl);

  // ── Arrastre ──
  let lastDragPos = { x: node.x, y: node.y };
  group.on('dragstart', () => {
    lastDragPos = { x: group.x(), y: group.y() };
    if (selectedNodes.size > 0 && !selectedNodes.has(node)) clearMultiSelection();
  });
  group.on('dragmove', () => {
    let curX = group.x(); let curY = group.y();
    if (snapToGrid) {
      curX = Math.round(curX / GRID_SIZE) * GRID_SIZE;
      curY = Math.round(curY / GRID_SIZE) * GRID_SIZE;
      group.position({ x: curX, y: curY });
    }
    const dx = curX - lastDragPos.x; const dy = curY - lastDragPos.y;
    lastDragPos = { x: curX, y: curY };
    if (selectedNodes.has(node) && selectedNodes.size > 1) {
      selectedNodes.forEach(otherNode => {
        if (otherNode.id !== node.id) {
          const og = nodeGroups.get(otherNode.id);
          if (og) {
            let nx = og.x() + dx; let ny = og.y() + dy;
            if (snapToGrid) { nx = Math.round(nx/GRID_SIZE)*GRID_SIZE; ny = Math.round(ny/GRID_SIZE)*GRID_SIZE; }
            og.position({ x: nx, y: ny }); otherNode.x = nx; otherNode.y = ny;
            updateAttachedLinks(otherNode.id, nx, ny);
          }
        }
      });
    }
    updateAttachedLinks(node.id, curX, curY);
  });
  group.on('dragend', async () => {
    try {
      if (selectedNodes.has(node) && selectedNodes.size > 1) {
        selectedNodes.forEach(async sNode => {
          const sg = nodeGroups.get(sNode.id);
          if (sg) { sNode.x = sg.x(); sNode.y = sg.y(); await API.updateNode(sNode.id, { x: sg.x(), y: sg.y() }); }
        });
      } else {
        await API.updateNode(node.id, { x: group.x(), y: group.y() });
        node.x = group.x(); node.y = group.y();
      }
      updateAllLinks();
    } catch(err) { console.error('Error guardando posición de nota:', err); }
  });

  // ── Clic: selección / modo enlace ──
  group.on('click tap', (e) => {
    e.cancelBubble = true;
    if (linkMode) {
      handleLinkNodeClick(node, e.evt);
    } else {
      const evt = e.evt || {};
      if (evt.shiftKey || evt.ctrlKey || evt.metaKey) {
        if (selectedNodes.has(node)) { removeNodeFromMultiSelection(node); }
        else {
          if (selectedNode) { const prev = selectedNode; deselectNode(); addNodeToMultiSelection(prev); }
          addNodeToMultiSelection(node); showMultiSelectionNotice(selectedNodes.size);
        }
      } else {
        if (selectedNodes.size > 0 && selectedNodes.has(node)) { showMultiSelectionNotice(selectedNodes.size); }
        else { clearMultiSelection(); selectNode(node); }
      }
    }
  });

  // ── Doble clic: abrir editor de nota ──
  group.on('dblclick dbltap', () => { openNoteEditorModal(node); });

  // ── Hover ──
  group.on('mouseenter', () => {
    document.body.style.cursor = linkMode ? 'crosshair' : 'pointer';
    if (!bg.isHighlighted) { bg.stroke('#f8fafc'); nodesLayer.batchDraw(); }
  });
  group.on('mouseleave', () => {
    document.body.style.cursor = 'default';
    if (!bg.isHighlighted) { bg.stroke(theme.border); nodesLayer.batchDraw(); }
  });

  nodesLayer.add(group);
  nodeGroups.set(node.id, group);
}

// ─── Renderizado de Brazo FTTH / Ramal GPON ─────────────────────────────────
const _gponTelemetryCache = new Map(); // Cache temporal de 10s para telemetría GPON

async function fetchGponBranchTelemetry(nodeId, force = false) {
  const now = Date.now();
  if (!force && _gponTelemetryCache.has(nodeId)) {
    const cached = _gponTelemetryCache.get(nodeId);
    if (now - cached.time < 10000) return cached.data;
  }
  try {
    const data = await API.getGponBranchTelemetry(nodeId);
    if (data) {
      _gponTelemetryCache.set(nodeId, { time: now, data });
    }
    return data;
  } catch (e) {
    console.warn(`Error obteniendo telemetría GPON para ${nodeId}:`, e);
    return null;
  }
}

function _renderFtthBranchNode(node) {
  const extra = node.extra_data || {};
  const branchName = extra.branch_name || node.name || 'Brazo FTTH';
  const gponPort = extra.gpon_port || 'GPON';
  const { nodeWidth, nodeHeight } = computeNodeDimensions(node);

  const group = new Konva.Group({
    x: node.x,
    y: node.y,
    draggable: true,
    id: node.id
  });
  group.isFtthBranch = true;

  // Estado del puerto (1 = Up, 2 = Down, o desde ping_status / status)
  const isDown = node.status === 'down' || node.ping_status === 'down' || extra.port_status === 'down' || extra.port_status_code === 2;
  const hasAtypical = (extra.atypical_count || 0) > 0;
  
  let branchBorderColor = '#10b981'; // Verde por defecto (Up)
  let branchBgColor = 'rgba(15, 23, 42, 0.92)';
  let glowColor = 'rgba(16, 185, 129, 0.35)';

  if (isDown) {
    branchBorderColor = '#ef4444'; // Rojo si está Link Down
    branchBgColor = 'rgba(69, 10, 10, 0.88)';
    glowColor = 'rgba(239, 68, 68, 0.5)';
  } else if (hasAtypical) {
    branchBorderColor = '#f59e0b'; // Amarillo si hay alertas ópticas
    glowColor = 'rgba(245, 158, 11, 0.4)';
  }

  // 2. Cápsula / Nodo compacto donde se ubica el nombre del brazo
  const capsule = new Konva.Rect({
    x: 0,
    y: 0,
    width: nodeWidth,
    height: nodeHeight,
    fill: branchBgColor,
    stroke: branchBorderColor,
    strokeWidth: isDown ? 2 : 1.5,
    cornerRadius: 18,
    shadowColor: glowColor,
    shadowBlur: isDown ? 8 : 4,
    shadowOpacity: 0.6,
    perfectDrawEnabled: false,
    name: 'box'
  });

  // 3. LED de estado circular
  const statusLed = new Konva.Circle({
    x: 14,
    y: nodeHeight / 2,
    radius: 4.5,
    fill: branchBorderColor,
    stroke: 'rgba(0,0,0,0.5)',
    strokeWidth: 1,
    listening: false,
    name: 'statusLed'
  });

  // 4. Ícono de fibra óptica
  const iconText = new Konva.Text({
    x: 23,
    y: (nodeHeight / 2) - 6,
    text: '⚡',
    fontSize: 10.5,
    listening: false,
    perfectDrawEnabled: false
  });

  // 5. Etiqueta de Título (Puerto GPON + Nombre del Brazo)
  const maxLabelW = nodeWidth - 44;
  const labelText = `${gponPort}: ${branchName}`;
  const label = new Konva.Text({
    x: 37,
    y: (nodeHeight / 2) - 5.5,
    text: labelText,
    width: maxLabelW,
    ellipsis: true,
    wrap: 'none',
    fontSize: 9.5,
    fontStyle: 'bold',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    fill: isDown ? '#fca5a5' : '#f8fafc',
    listening: false,
    perfectDrawEnabled: false,
    name: 'label'
  });

  group.add(capsule);
  group.add(statusLed);
  group.add(iconText);
  group.add(label);

  // ── Arrastre ──
  let lastDragPos = { x: node.x, y: node.y };
  group.on('dragstart', () => {
    lastDragPos = { x: group.x(), y: group.y() };
    if (selectedNodes.size > 0 && !selectedNodes.has(node)) clearMultiSelection();
  });
  group.on('dragmove', () => {
    let curX = group.x(); let curY = group.y();
    if (snapToGrid) {
      curX = Math.round(curX / GRID_SIZE) * GRID_SIZE;
      curY = Math.round(curY / GRID_SIZE) * GRID_SIZE;
      group.position({ x: curX, y: curY });
    }
    const dx = curX - lastDragPos.x; const dy = curY - lastDragPos.y;
    lastDragPos = { x: curX, y: curY };
    if (selectedNodes.has(node) && selectedNodes.size > 1) {
      selectedNodes.forEach(otherNode => {
        if (otherNode.id !== node.id) {
          const og = nodeGroups.get(otherNode.id);
          if (og) {
            let nx = og.x() + dx; let ny = og.y() + dy;
            if (snapToGrid) { nx = Math.round(nx/GRID_SIZE)*GRID_SIZE; ny = Math.round(ny/GRID_SIZE)*GRID_SIZE; }
            og.position({ x: nx, y: ny }); otherNode.x = nx; otherNode.y = ny;
            updateAttachedLinks(otherNode.id, nx, ny);
          }
        }
      });
    }
    updateAttachedLinks(node.id, curX, curY);
  });
  group.on('dragend', async () => {
    try {
      if (selectedNodes.has(node) && selectedNodes.size > 1) {
        selectedNodes.forEach(async sNode => {
          const sg = nodeGroups.get(sNode.id);
          if (sg) { sNode.x = sg.x(); sNode.y = sg.y(); await API.updateNode(sNode.id, { x: sg.x(), y: sg.y() }); }
        });
      } else {
        await API.updateNode(node.id, { x: group.x(), y: group.y() });
        node.x = group.x(); node.y = group.y();
      }
      updateAllLinks();
    } catch(err) { console.error('Error guardando posición de brazo:', err); }
  });

  // ── Clic: selección / modo enlace ──
  group.on('click tap', (e) => {
    e.cancelBubble = true;
    if (linkMode) {
      handleLinkNodeClick(node, e.evt);
    } else {
      const evt = e.evt || {};
      if (evt.shiftKey || evt.ctrlKey || evt.metaKey) {
        if (selectedNodes.has(node)) { removeNodeFromMultiSelection(node); }
        else {
          if (selectedNode) { const prev = selectedNode; deselectNode(); addNodeToMultiSelection(prev); }
          addNodeToMultiSelection(node); showMultiSelectionNotice(selectedNodes.size);
        }
      } else {
        if (selectedNodes.size > 0 && selectedNodes.has(node)) { showMultiSelectionNotice(selectedNodes.size); }
        else { clearMultiSelection(); selectNode(node); }
      }
    }
  });

  // ── Doble clic: abrir editor de brazo ──
  group.on('dblclick dbltap', () => { openFtthBranchEditorModal(node); });

  // ── Hover: Tooltip flotante enriquecido con telemetría de ONUs y tráfico ──
  group.on('mouseenter mousemove', (e) => {
    document.body.style.cursor = linkMode ? 'crosshair' : 'pointer';
    if (!capsule.isHighlighted) {
      capsule.stroke('#38bdf8');
      nodesLayer.batchDraw();
    }

    const tooltip = document.getElementById('canvas-gpon-tooltip');
    if (tooltip) {
      const evt = e.evt || window.event;
      if (evt) {
        tooltip.style.left = `${evt.clientX + 14}px`;
        tooltip.style.top = `${evt.clientY + 14}px`;
      }

      // Poblado inicial inmediato
      const titleEl = document.getElementById('tooltip-gpon-title');
      const portEl = document.getElementById('tooltip-gpon-port');
      const oltEl = document.getElementById('tooltip-gpon-olt');
      const statusEl = document.getElementById('tooltip-gpon-status');
      
      if (titleEl) titleEl.textContent = `⚡ ${branchName}`;
      if (portEl) portEl.textContent = gponPort;
      if (oltEl) oltEl.textContent = extra.olt_name || extra.olt_ip || 'OLT';
      if (statusEl) {
        statusEl.textContent = isDown ? '● Link Down (Offline)' : '● Link Up (Operativo)';
        statusEl.style.color = isDown ? '#f87171' : '#10b981';
        statusEl.style.background = isDown ? 'rgba(239,68,68,0.2)' : 'rgba(16,185,129,0.2)';
      }

      tooltip.style.display = 'block';

      // Carga asíncrona de telemetría viva de ONUs y tráfico
      fetchGponBranchTelemetry(node.id).then(telem => {
        if (!telem || tooltip.style.display === 'none') return;
        
        const inEl = document.getElementById('tooltip-gpon-traffic-in');
        const outEl = document.getElementById('tooltip-gpon-traffic-out');
        const volEl = document.getElementById('tooltip-gpon-volume');
        const txEl = document.getElementById('tooltip-gpon-txpower');

        if (inEl) inEl.textContent = telem.traffic_in_fmt || '—';
        if (outEl) outEl.textContent = telem.traffic_out_fmt || '—';
        if (volEl) volEl.textContent = telem.volume_total_fmt || '—';
        if (txEl) txEl.textContent = telem.tx_power_dbm !== null ? `${telem.tx_power_dbm} dBm` : '—';

        const typCountEl = document.getElementById('tooltip-gpon-typical-count');
        const typAvgEl = document.getElementById('tooltip-gpon-typical-avg');
        const atypCountEl = document.getElementById('tooltip-gpon-atypical-count');
        const atypAvgEl = document.getElementById('tooltip-gpon-atypical-avg');

        if (typCountEl) typCountEl.textContent = `${telem.typical_count || 0} ONUs`;
        if (typAvgEl) typAvgEl.textContent = telem.typical_avg_dbm !== null ? `${telem.typical_avg_dbm} dBm` : '—';
        if (atypCountEl) atypCountEl.textContent = `${telem.atypical_count || 0} ONUs`;
        if (atypAvgEl) atypAvgEl.textContent = telem.atypical_avg_dbm !== null ? `${telem.atypical_avg_dbm} dBm` : '—';

        if (statusEl) {
          statusEl.textContent = telem.port_status === 'down' ? '● Link Down (Offline)' : (telem.atypical_count > 0 ? '● Alerta Óptica' : '● Link Up (Operativo)');
          statusEl.style.color = telem.port_status === 'down' ? '#f87171' : (telem.atypical_count > 0 ? '#fbbf24' : '#10b981');
          statusEl.style.background = telem.port_status === 'down' ? 'rgba(239,68,68,0.2)' : (telem.atypical_count > 0 ? 'rgba(245,158,11,0.2)' : 'rgba(16,185,129,0.2)');
        }
      });
    }
  });

  group.on('mouseleave', () => {
    document.body.style.cursor = 'default';
    if (!capsule.isHighlighted) {
      capsule.stroke(branchBorderColor);
      nodesLayer.batchDraw();
    }
    const tooltip = document.getElementById('canvas-gpon-tooltip');
    if (tooltip) tooltip.style.display = 'none';
  });

  nodesLayer.add(group);
  nodeGroups.set(node.id, group);
}

function renderNode(node) {
  // ── Nodo Nota: Sticky Note visual ──
  if (node.device_type === 'note') {
    return _renderNoteNode(node);
  }

  // ── Brazo FTTH / Ramal GPON ──
  if (node.device_type === 'ftth_branch') {
    return _renderFtthBranchNode(node);
  }

  const isParentShortcut = node.device_type === 'parent_map' || !!node.extra_data?.is_parent_shortcut;
  const isSubmap = node.device_type === 'submap' || isParentShortcut;
  const { nodeWidth, nodeHeight, subLabelText, pins } = computeNodeDimensions(node);

  const group = new Konva.Group({
    x: node.x,
    y: node.y,
    draggable: true,
    id: node.id
  });
  group.isSubmap = isSubmap;
  group.isParentShortcut = isParentShortcut;

  // Estado PING (Contorno del nodo) y Estado SNMP (Punto interior)
  const pingStatus = node.ping_status || node.status;
  const snmpStatus = node.snmp_status || node.status;
  const pingColor = isParentShortcut ? '#38bdf8' : getNodeStatusColor(pingStatus, isSubmap);
  const snmpColor = isParentShortcut ? '#38bdf8' : getNodeStatusColor(snmpStatus, isSubmap);
  const roleHex = getNodeRoleColor(node);

  // Caja de fondo: relleno translúcido con el color del rol/función de NetBox (22-26% alpha)
  let boxFill = '#162235';
  if (isParentShortcut) {
    boxFill = 'rgba(12, 74, 110, 0.88)';
  } else if (node.device_type === 'submap') {
    boxFill = 'rgba(74, 14, 122, 0.78)';
  } else if (roleHex) {
    boxFill = hexToRgba(roleHex, 0.24) || '#162235';
  }

  const box = new Konva.Rect({
    width: nodeWidth,
    height: nodeHeight,
    fill: boxFill,
    stroke: isParentShortcut ? '#38bdf8' : pingColor,
    strokeWidth: isParentShortcut ? 2 : 1.5,
    dash: isParentShortcut ? [5, 3] : undefined,
    cornerRadius: 8,
    shadowColor: isParentShortcut ? 'rgba(56, 189, 248, 0.4)' : (roleHex ? hexToRgba(roleHex, 0.25) : 'rgba(0, 0, 0, 0.45)'),
    shadowBlur: isParentShortcut ? 8 : (roleHex ? 4 : 0),
    shadowOpacity: 0.4,
    shadowOffset: { x: 0, y: 2 },
    shadowForStrokeEnabled: false,
    perfectDrawEnabled: false,
    name: 'box'
  });

  // Indicador de estado circular (SNMP): aumentado 50% de tamaño (de radio 4.5 a 6.75)
  const statusDot = new Konva.Circle({
    x: 13,
    y: isSubmap ? 16 : 15,
    radius: 6.75,
    fill: isParentShortcut ? '#38bdf8' : snmpColor,
    stroke: 'rgba(0, 0, 0, 0.35)',
    strokeWidth: 1,
    listening: false,
    perfectDrawEnabled: false,
    name: 'statusDot'
  });

  // Icono
  const iconEmoji = getRoleIcon(node.device_type, node.extra_data);
  const iconText = new Konva.Text({
    x: 24,
    y: isSubmap ? 10 : 9,
    text: iconEmoji,
    fontSize: 12,
    listening: false,
    perfectDrawEnabled: false
  });

  // Nombre responsivo (muestra el nombre completo; aplica elipsis inteligente solo si excede el ancho máximo)
  const maxLabelWidth = nodeWidth - 48;
  const label = new Konva.Text({
    x: 44,
    y: isSubmap ? 11 : 10,
    text: node.name,
    width: maxLabelWidth,
    ellipsis: true,
    wrap: 'none',
    fontSize: 11,
    fontStyle: 'bold',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    fill: isParentShortcut ? '#bae6fd' : '#f8fafc',
    listening: false,
    perfectDrawEnabled: false,
    name: 'label'
  });

  // Subtexto (cantidad de equipos, IP o subir nivel)
  const maxSubWidth = nodeWidth - 24;
  const ipText = new Konva.Text({
    x: 14,
    y: isSubmap ? 33 : 29,
    text: subLabelText,
    width: maxSubWidth,
    ellipsis: true,
    wrap: 'none',
    fontSize: 9.5,
    fontFamily: isSubmap ? 'system-ui, -apple-system, sans-serif' : 'monospace',
    fontStyle: isSubmap ? 'bold' : 'normal',
    fill: isParentShortcut ? '#38bdf8' : (isSubmap ? '#c084fc' : '#38bdf8'),
    listening: false,
    perfectDrawEnabled: false,
    name: 'ipText'
  });

  group.add(box);
  group.add(statusDot);
  group.add(iconText);
  group.add(label);
  group.add(ipText);

  // Si tiene pines de interconexión (Bornes virtuales)
  if (pins && pins.length > 0) {
    const divider = new Konva.Line({
      points: [6, 48, nodeWidth - 6, 48],
      stroke: 'rgba(255, 255, 255, 0.18)',
      strokeWidth: 1,
      dash: [3, 2],
      listening: false
    });
    group.add(divider);

    pins.forEach((pin, idx) => {
      const pinY = 52 + idx * 20 + 8;
      
      const pinDot = new Konva.Circle({
        x: 14,
        y: pinY + 2,
        radius: 3.5,
        fill: isParentShortcut ? '#38bdf8' : '#a855f7',
        stroke: '#ffffff',
        strokeWidth: 0.8,
        listening: false
      });

      const pinLabel = new Konva.Text({
        x: 24,
        y: pinY - 3,
        text: pin.label || (isParentShortcut ? `⬅ ${pin.remote_node_name}` : `➔ ${pin.remote_node_name}`),
        width: nodeWidth - 30,
        ellipsis: true,
        wrap: 'none',
        fontSize: 9,
        fontFamily: 'monospace',
        fontStyle: 'bold',
        fill: isParentShortcut ? '#7dd3fc' : '#e9d5ff',
        listening: false
      });

      group.add(pinDot);
      group.add(pinLabel);
    });
  }

  // Arrastre conjunto si está en selección múltiple
  let lastDragPos = { x: node.x, y: node.y };

  group.on('dragstart', () => {
    lastDragPos = { x: group.x(), y: group.y() };
    // Si el nodo arrastrado no está en el conjunto seleccionado, limpiar multi-selección
    if (selectedNodes.size > 0 && !selectedNodes.has(node)) {
      clearMultiSelection();
    }
  });

  group.on('dragmove', () => {
    let curX = group.x();
    let curY = group.y();

    if (snapToGrid) {
      curX = Math.round(curX / GRID_SIZE) * GRID_SIZE;
      curY = Math.round(curY / GRID_SIZE) * GRID_SIZE;
      group.position({ x: curX, y: curY });
    }

    const dx = curX - lastDragPos.x;
    const dy = curY - lastDragPos.y;
    lastDragPos = { x: curX, y: curY };

    // Si hay selección múltiple activa, mover los demás nodos del grupo por el delta dx, dy
    if (selectedNodes.has(node) && selectedNodes.size > 1) {
      selectedNodes.forEach(otherNode => {
        if (otherNode.id !== node.id) {
          const otherGrp = nodeGroups.get(otherNode.id);
          if (otherGrp) {
            let newX = otherGrp.x() + dx;
            let newY = otherGrp.y() + dy;
            if (snapToGrid) {
              newX = Math.round(newX / GRID_SIZE) * GRID_SIZE;
              newY = Math.round(newY / GRID_SIZE) * GRID_SIZE;
            }
            otherGrp.position({ x: newX, y: newY });
            otherNode.x = newX;
            otherNode.y = newY;
            updateAttachedLinks(otherNode.id, newX, newY);
          }
        }
      });
    }

    updateAttachedLinks(node.id, curX, curY);
    if (selectedNode && selectedNode.id === node.id) {
      const coordsEl = document.getElementById('prop-node-coords');
      if (coordsEl) coordsEl.textContent = `X: ${Math.round(curX)}, Y: ${Math.round(curY)}`;
    }
  });

  group.on('dragend', async () => {
    try {
      if (selectedNodes.has(node) && selectedNodes.size > 1) {
        // Guardar la posición de todos los nodos seleccionados
        selectedNodes.forEach(async (sNode) => {
          const sGrp = nodeGroups.get(sNode.id);
          if (sGrp) {
            sNode.x = sGrp.x();
            sNode.y = sGrp.y();
            await API.updateNode(sNode.id, { x: sGrp.x(), y: sGrp.y() });
          }
        });
      } else {
        await API.updateNode(node.id, {
          x: group.x(),
          y: group.y()
        });
        node.x = group.x();
        node.y = group.y();
      }

      if (selectedNode && selectedNode.id === node.id) {
        const coordsEl = document.getElementById('prop-node-coords');
        if (coordsEl) coordsEl.textContent = `X: ${Math.round(group.x())}, Y: ${Math.round(group.y())}`;
      }

      updateAllLinks();
    } catch (err) {
      console.error('Error guardando posición:', err);
    }
  });

  // Clic en nodo: selección individual o conexión de enlace
  group.on('click tap', (e) => {
    e.cancelBubble = true;
    if (linkMode) {
      handleLinkNodeClick(node, e.evt);
    } else {
      const evt = e.evt || {};
      if (evt.shiftKey || evt.ctrlKey || evt.metaKey) {
        // Modo toggle de selección múltiple
        if (selectedNodes.has(node)) {
          removeNodeFromMultiSelection(node);
        } else {
          if (selectedNode) {
            const prev = selectedNode;
            deselectNode();
            addNodeToMultiSelection(prev);
          }
          addNodeToMultiSelection(node);
          showMultiSelectionNotice(selectedNodes.size);
        }
      } else {
        if (selectedNodes.size > 0 && selectedNodes.has(node)) {
          // El nodo ya forma parte de la selección múltiple activa
          showMultiSelectionNotice(selectedNodes.size);
        } else {
          clearMultiSelection();
          selectNode(node);
        }
      }
    }
  });

  // Doble clic: drill-down si es submapa o ascenso a mapa padre
  group.on('dblclick dbltap', () => {
    if (isSubmap || isParentShortcut) {
      let targetId = node.extra_data?.target_map_id;
      if (!targetId && isParentShortcut) {
        targetId = currentMap?.parent_map_id;
      }
      if (!targetId && Array.isArray(cachedMaps)) {
        const cleanName = node.name.replace("📁", "").replace("⬆", "").trim().toLowerCase();
        const matched = cachedMaps.find(m => m.name.toLowerCase().trim() === cleanName);
        if (matched) targetId = matched.id;
      }
      if (targetId) {
        loadMap(targetId);
      }
    }
  });

  // Hover visual optimizado en nodesLayer
  group.on('mouseenter', () => {
    document.body.style.cursor = linkMode ? 'crosshair' : 'pointer';
    if (!box.isHighlighted) {
      if (linkMode && linkSourceNode && linkSourceNode.id === node.id) {
        return; // Mantener resaltado de origen
      }
      box.stroke('#38bdf8');
      nodesLayer.batchDraw();
    }
  });
  group.on('mouseleave', () => {
    document.body.style.cursor = 'default';
    if (!box.isHighlighted) {
      if (linkMode && linkSourceNode && linkSourceNode.id === node.id) {
        box.stroke('#10b981');
        box.strokeWidth(3);
      } else if (linkMode && linkTargetNodes.has(node)) {
        box.stroke('#38bdf8');
        box.strokeWidth(2.5);
      } else if (selectedNodes.has(node)) {
        box.stroke('#a855f7');
        box.strokeWidth(2);
      } else if (selectedNode && selectedNode.id === node.id) {
        box.stroke('#38bdf8');
        box.strokeWidth(2);
      } else {
        const curPingStatus = node.ping_status || node.status;
        box.stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
        box.strokeWidth(isSubmap ? 2 : 1.5);
      }
      nodesLayer.batchDraw();
    }
  });

  nodesLayer.add(group);
  nodeGroups.set(node.id, group);
}

function getNodePerimeterIntersection(cx, cy, halfW, halfH, targetCx, targetCy) {
  const dx = targetCx - cx;
  const dy = targetCy - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };

  const scaleX = (halfW - 0.5) / Math.abs(dx);
  const scaleY = (halfH - 0.5) / Math.abs(dy);
  const scale = Math.min(scaleX, scaleY);

  return {
    x: cx + dx * scale,
    y: cy + dy * scale
  };
}

function lineIntersectsBox(p1, p2, box) {
  const minX = Math.min(p1.x, p2.x);
  const maxX = Math.max(p1.x, p2.x);
  const minY = Math.min(p1.y, p2.y);
  const maxY = Math.max(p1.y, p2.y);

  if (maxX < box.left || minX > box.right || maxY < box.top || minY > box.bottom) {
    return false;
  }

  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;

  let tMin = 0.0;
  let tMax = 1.0;

  if (Math.abs(dx) > 1e-7) {
    let t1 = (box.left - p1.x) / dx;
    let t2 = (box.right - p1.x) / dx;
    if (t1 > t2) { const tmp = t1; t1 = t2; t2 = tmp; }
    tMin = Math.max(tMin, t1);
    tMax = Math.min(tMax, t2);
    if (tMin > tMax) return false;
  } else {
    if (p1.x < box.left || p1.x > box.right) return false;
  }

  if (Math.abs(dy) > 1e-7) {
    let t1 = (box.top - p1.y) / dy;
    let t2 = (box.bottom - p1.y) / dy;
    if (t1 > t2) { const tmp = t1; t1 = t2; t2 = tmp; }
    tMin = Math.max(tMin, t1);
    tMax = Math.min(tMax, t2);
    if (tMin > tMax) return false;
  } else {
    if (p1.y < box.top || p1.y > box.bottom) return false;
  }

  return tMin <= tMax && tMax >= 0.0 && tMin <= 1.0;
}

function reversePoints(pts) {
  const rev = [];
  for (let i = pts.length - 2; i >= 0; i -= 2) {
    rev.push(pts[i], pts[i + 1]);
  }
  return rev;
}

function getArrowPointsForDirection(pts, direction) {
  if (direction === 'target_to_source') {
    return reversePoints(pts);
  }
  return pts;
}

function roundCorners(waypoints, radius = 8) {
  if (waypoints.length < 3) return waypoints;

  const res = [waypoints[0]];
  for (let i = 1; i < waypoints.length - 1; i++) {
    const prev = waypoints[i - 1];
    const curr = waypoints[i];
    const nxt = waypoints[i + 1];

    const vInX = curr.x - prev.x;
    const vInY = curr.y - prev.y;
    const vOutX = nxt.x - curr.x;
    const vOutY = nxt.y - curr.y;

    const lenIn = Math.hypot(vInX, vInY);
    const lenOut = Math.hypot(vOutX, vOutY);

    if (lenIn < 1e-5 || lenOut < 1e-5) {
      res.push(curr);
      continue;
    }

    const r = Math.min(radius, lenIn * 0.45, lenOut * 0.45);

    const pBefore = {
      x: curr.x - (vInX / lenIn) * r,
      y: curr.y - (vInY / lenIn) * r
    };
    const pAfter = {
      x: curr.x + (vOutX / lenOut) * r,
      y: curr.y + (vOutY / lenOut) * r
    };

    res.push(pBefore, pAfter);
  }

  res.push(waypoints[waypoints.length - 1]);
  return res;
}

function determineNodeFace(srcCx, srcCy, tgtCx, tgtCy, halfW, halfH) {
  const dx = tgtCx - srcCx;
  const dy = tgtCy - srcCy;
  if (Math.abs(dx) < 1e-5 && Math.abs(dy) < 1e-5) return 'bottom';
  const scaleX = Math.abs(dx) > 1e-5 ? halfW / Math.abs(dx) : Infinity;
  const scaleY = Math.abs(dy) > 1e-5 ? halfH / Math.abs(dy) : Infinity;
  if (scaleY <= scaleX) {
    return dy >= 0 ? 'bottom' : 'top';
  } else {
    return dx >= 0 ? 'right' : 'left';
  }
}

function getNodeFaceCenter(node, face) {
  const half = getNodeHalfDimensions(node);
  const grp = nodeGroups.get(node.id);
  const nx = grp ? grp.x() : (node.x || 0);
  const ny = grp ? grp.y() : (node.y || 0);
  const cx = nx + half.halfW;
  const cy = ny + half.halfH;

  if (face === 'top') return { x: cx, y: ny, cx, cy, halfW: half.halfW, halfH: half.halfH };
  if (face === 'bottom') return { x: cx, y: ny + half.halfH * 2, cx, cy, halfW: half.halfW, halfH: half.halfH };
  if (face === 'left') return { x: nx, y: cy, cx, cy, halfW: half.halfW, halfH: half.halfH };
  return { x: nx + half.halfW * 2, y: cy, cx, cy, halfW: half.halfW, halfH: half.halfH }; // right
}

function getObstaclesForLink(sourceId, targetId) {
  const allNodes = (currentMap && currentMap.nodes) ? currentMap.nodes : [];
  const obstacles = [];
  const boxPad = 14; // Margen de seguridad para no tocar el contorno de ningún nodo

  for (let i = 0; i < allNodes.length; i++) {
    const n = allNodes[i];
    if (n.id === sourceId || n.id === targetId) continue;
    const half = getNodeHalfDimensions(n);
    const grp = nodeGroups.get(n.id);
    const nx = grp ? grp.x() : (n.x || 0);
    const ny = grp ? grp.y() : (n.y || 0);
    obstacles.push({
      id: n.id,
      left: nx - boxPad,
      right: nx + half.halfW * 2 + boxPad,
      top: ny - boxPad,
      bottom: ny + half.halfH * 2 + boxPad,
      cx: nx + half.halfW,
      cy: ny + half.halfH
    });
  }
  return obstacles;
}

function segmentHitsBox(p1, p2, box) {
  const minX = Math.min(p1.x, p2.x);
  const maxX = Math.max(p1.x, p2.x);
  const minY = Math.min(p1.y, p2.y);
  const maxY = Math.max(p1.y, p2.y);

  if (maxX <= box.left || minX >= box.right || maxY <= box.top || minY >= box.bottom) {
    return false;
  }

  if (Math.abs(p1.x - p2.x) < 1e-4) {
    return (p1.x > box.left && p1.x < box.right && minY < box.bottom && maxY > box.top);
  }
  if (Math.abs(p1.y - p2.y) < 1e-4) {
    return (p1.y > box.top && p1.y < box.bottom && minX < box.right && maxX > box.left);
  }
  return lineIntersectsBox(p1, p2, box);
}

function avoidObstaclesOrthogonal(waypoints, obstacles) {
  if (!obstacles || obstacles.length === 0 || waypoints.length < 2) return waypoints;

  let current = waypoints;
  for (let pass = 0; pass < 2; pass++) {
    let changed = false;
    const nextWaypoints = [current[0]];

    for (let i = 0; i < current.length - 1; i++) {
      const p1 = current[i];
      const p2 = current[i + 1];

      const isVert = Math.abs(p1.x - p2.x) < 1e-4;
      const isHoriz = Math.abs(p1.y - p2.y) < 1e-4;

      if (!isVert && !isHoriz) {
        nextWaypoints.push(p2);
        continue;
      }

      const hitting = [];
      for (let j = 0; j < obstacles.length; j++) {
        const obs = obstacles[j];
        if (segmentHitsBox(p1, p2, obs)) {
          hitting.push(obs);
        }
      }

      if (hitting.length === 0) {
        nextWaypoints.push(p2);
        continue;
      }

      changed = true;

      if (isVert) {
        const goingDown = p2.y > p1.y;
        hitting.sort((a, b) => goingDown ? (a.cy - b.cy) : (b.cy - a.cy));

        let curP = p1;
        for (let k = 0; k < hitting.length; k++) {
          const obs = hitting[k];
          const detourLeft = curP.x <= obs.cx;
          const detourX = detourLeft ? obs.left : obs.right;

          if (goingDown) {
            nextWaypoints.push({ x: curP.x, y: obs.top });
            nextWaypoints.push({ x: detourX, y: obs.top });
            nextWaypoints.push({ x: detourX, y: obs.bottom });
            nextWaypoints.push({ x: curP.x, y: obs.bottom });
          } else {
            nextWaypoints.push({ x: curP.x, y: obs.bottom });
            nextWaypoints.push({ x: detourX, y: obs.bottom });
            nextWaypoints.push({ x: detourX, y: obs.top });
            nextWaypoints.push({ x: curP.x, y: obs.top });
          }
        }
        nextWaypoints.push(p2);
      } else {
        const goingRight = p2.x > p1.x;
        hitting.sort((a, b) => goingRight ? (a.cx - b.cx) : (b.cx - a.cx));

        let curP = p1;
        for (let k = 0; k < hitting.length; k++) {
          const obs = hitting[k];
          const detourAbove = curP.y <= obs.cy;
          const detourY = detourAbove ? obs.top : obs.bottom;

          if (goingRight) {
            nextWaypoints.push({ x: obs.left, y: curP.y });
            nextWaypoints.push({ x: obs.left, y: detourY });
            nextWaypoints.push({ x: obs.right, y: detourY });
            nextWaypoints.push({ x: obs.right, y: curP.y });
          } else {
            nextWaypoints.push({ x: obs.right, y: curP.y });
            nextWaypoints.push({ x: obs.right, y: detourY });
            nextWaypoints.push({ x: obs.left, y: detourY });
            nextWaypoints.push({ x: obs.left, y: curP.y });
          }
        }
        nextWaypoints.push(p2);
      }
    }

    current = nextWaypoints;
    if (!changed) break;
  }

  return current;
}

function calculateLinkEndpoints(sourceNode, targetNode, link = null) {
  if (!sourceNode || !targetNode) return [0, 0, 0, 0];

  const srcHalf = getNodeHalfDimensions(sourceNode);
  const tgtHalf = getNodeHalfDimensions(targetNode);
  const srcGrp = nodeGroups.get(sourceNode.id);
  const tgtGrp = nodeGroups.get(targetNode.id);

  const srcNx = srcGrp ? srcGrp.x() : (sourceNode.x || 0);
  const srcNy = srcGrp ? srcGrp.y() : (sourceNode.y || 0);
  const tgtNx = tgtGrp ? tgtGrp.x() : (targetNode.x || 0);
  const tgtNy = tgtGrp ? tgtGrp.y() : (targetNode.y || 0);

  const srcCx = srcNx + srcHalf.halfW;
  const srcCy = srcNy + srcHalf.halfH;
  const tgtCx = tgtNx + tgtHalf.halfW;
  const tgtCy = tgtNy + tgtHalf.halfH;

  const sFace = determineNodeFace(srcCx, srcCy, tgtCx, tgtCy, srcHalf.halfW, srcHalf.halfH);
  const tFace = determineNodeFace(tgtCx, tgtCy, srcCx, srcCy, tgtHalf.halfW, tgtHalf.halfH);

  const allNodes = (currentMap && currentMap.nodes) ? currentMap.nodes : [];
  const allLinks = (currentMap && currentMap.links) ? currentMap.links : [];

  const nodeMap = new Map();
  allNodes.forEach(n => nodeMap.set(n.id, n));

  const isSVert = (sFace === 'top' || sFace === 'bottom');
  const isTVert = (tFace === 'top' || tFace === 'bottom');

  // 1. Recolectar todas las conexiones que salen por la cara de origen (Source Face)
  const allSrcSiblings = [];
  for (let i = 0; i < allLinks.length; i++) {
    const l = allLinks[i];
    let otherId = null;
    if (l.source_node_id === sourceNode.id) otherId = l.target_node_id;
    else if (l.target_node_id === sourceNode.id) otherId = l.source_node_id;

    if (otherId) {
      const other = nodeMap.get(otherId);
      if (other) {
        const oHalf = getNodeHalfDimensions(other);
        const oGrp = nodeGroups.get(other.id);
        const oNx = oGrp ? oGrp.x() : (other.x || 0);
        const oNy = oGrp ? oGrp.y() : (other.y || 0);
        const oCx = oNx + oHalf.halfW;
        const oCy = oNy + oHalf.halfH;

        const oFace = determineNodeFace(srcCx, srcCy, oCx, oCy, srcHalf.halfW, srcHalf.halfH);
        if (oFace === sFace) {
          allSrcSiblings.push({
            linkId: l.id,
            id: other.id,
            cx: oCx,
            cy: oCy
          });
        }
      }
    }
  }

  // Ordenar TODOS los enlaces de la cara de origen de izquierda a derecha (o de arriba a abajo)
  if (isSVert) {
    allSrcSiblings.sort((a, b) => a.cx !== b.cx ? (a.cx - b.cx) : String(a.linkId).localeCompare(String(b.linkId)));
  } else {
    allSrcSiblings.sort((a, b) => a.cy !== b.cy ? (a.cy - b.cy) : String(a.linkId).localeCompare(String(b.linkId)));
  }

  const srcN = allSrcSiblings.length;
  let srcIdx = allSrcSiblings.findIndex(s => (link && s.linkId === link.id) || s.id === targetNode.id);
  if (srcIdx === -1) srcIdx = 0;

  // Distribuir pines uniformemente a lo largo de la cara: CADA ENLACE TIENE SU PROPIO PIN EXCLUSIVO
  let srcPinOffset = 0;
  if (srcN > 1) {
    const maxSpan = isSVert ? ((srcHalf.halfW - 8) * 2) : ((srcHalf.halfH - 8) * 2);
    const step = Math.min(14, maxSpan / srcN);
    srcPinOffset = (-(srcN - 1) / 2.0 + srcIdx) * step;
  }

  let srcPt = { x: srcCx, y: srcCy };
  if (sFace === 'bottom') {
    srcPt = { x: srcCx + srcPinOffset, y: srcNy + srcHalf.halfH * 2 };
  } else if (sFace === 'top') {
    srcPt = { x: srcCx + srcPinOffset, y: srcNy };
  } else if (sFace === 'right') {
    srcPt = { x: srcNx + srcHalf.halfW * 2, y: srcCy + srcPinOffset };
  } else {
    srcPt = { x: srcNx, y: srcCy + srcPinOffset };
  }

  // 2. Recolectar y distribuir pines en la cara de destino (Target Face)
  const allTgtSiblings = [];
  for (let i = 0; i < allLinks.length; i++) {
    const l = allLinks[i];
    let otherId = null;
    if (l.target_node_id === targetNode.id) otherId = l.source_node_id;
    else if (l.source_node_id === targetNode.id) otherId = l.target_node_id;

    if (otherId) {
      const other = nodeMap.get(otherId);
      if (other) {
        const oHalf = getNodeHalfDimensions(other);
        const oGrp = nodeGroups.get(other.id);
        const oNx = oGrp ? oGrp.x() : (other.x || 0);
        const oNy = oGrp ? oGrp.y() : (other.y || 0);
        const oCx = oNx + oHalf.halfW;
        const oCy = oNy + oHalf.halfH;

        const oFace = determineNodeFace(tgtCx, tgtCy, oCx, oCy, tgtHalf.halfW, tgtHalf.halfH);
        if (oFace === tFace) {
          allTgtSiblings.push({
            linkId: l.id,
            id: other.id,
            cx: oCx,
            cy: oCy
          });
        }
      }
    }
  }

  if (isTVert) {
    allTgtSiblings.sort((a, b) => a.cx !== b.cx ? (a.cx - b.cx) : String(a.linkId).localeCompare(String(b.linkId)));
  } else {
    allTgtSiblings.sort((a, b) => a.cy !== b.cy ? (a.cy - b.cy) : String(a.linkId).localeCompare(String(b.linkId)));
  }

  const tgtN = allTgtSiblings.length;
  let tgtIdx = allTgtSiblings.findIndex(s => (link && s.linkId === link.id) || s.id === sourceNode.id);
  if (tgtIdx === -1) tgtIdx = 0;

  let tgtPinOffset = 0;
  if (tgtN > 1) {
    const maxSpan = isTVert ? ((tgtHalf.halfW - 8) * 2) : ((tgtHalf.halfH - 8) * 2);
    const step = Math.min(14, maxSpan / tgtN);
    tgtPinOffset = (-(tgtN - 1) / 2.0 + tgtIdx) * step;
  }

  let tgtPt = { x: tgtCx, y: tgtCy };
  if (tFace === 'top') {
    tgtPt = { x: tgtCx + tgtPinOffset, y: tgtNy };
  } else if (tFace === 'bottom') {
    tgtPt = { x: tgtCx + tgtPinOffset, y: tgtNy + tgtHalf.halfH * 2 };
  } else if (tFace === 'left') {
    tgtPt = { x: tgtNx, y: tgtCy + tgtPinOffset };
  } else {
    tgtPt = { x: tgtNx + tgtHalf.halfW * 2, y: tgtCy + tgtPinOffset };
  }

  // 3. Enrutamiento del corredor con Abanico Bilateral Adaptativo (sin cruces y pistas paralelas)
  let rawPath = [];

  if (isSVert) {
    const gapY = Math.abs(tgtPt.y - srcPt.y);
    const stub = Math.min(22, Math.max(14, gapY * 0.18));
    const usableY = Math.max(0, gapY - 2 * stub);

    // Separar hermanos en grupo Izquierdo (< srcCx) y grupo Derecho (>= srcCx)
    const leftGroup = allSrcSiblings.filter(s => s.cx < srcCx);
    const rightGroup = allSrcSiblings.filter(s => s.cx >= srcCx);

    const isGoingLeft = tgtCx < srcCx;
    let trackFraction = 0.5;

    if (isGoingLeft && leftGroup.length > 1) {
      // En grupo izquierdo: ordenado por cx ascendente (0 = más a la izquierda / exterior, n-1 = más cercano al centro / interior)
      // El exterior gira más arriba (cerca del origen), el interior gira más abajo (lejos del origen)
      const idxInLeft = leftGroup.findIndex(s => (link && s.linkId === link.id) || s.id === targetNode.id);
      if (idxInLeft !== -1) {
        trackFraction = idxInLeft / (leftGroup.length - 1);
      }
    } else if (!isGoingLeft && rightGroup.length > 1) {
      // En grupo derecho: ordenado por cx ascendente (0 = más cercano al centro / interior, n-1 = más a la derecha / exterior)
      // El exterior gira más arriba (cerca del origen), el interior gira más abajo (lejos del origen)
      const idxInRight = rightGroup.findIndex(s => (link && s.linkId === link.id) || s.id === targetNode.id);
      if (idxInRight !== -1) {
        trackFraction = 1.0 - (idxInRight / (rightGroup.length - 1));
      }
    }

    let midY = (srcPt.y + tgtPt.y) / 2.0;
    if (sFace === 'bottom') {
      midY = (srcPt.y + stub) + trackFraction * usableY;
    } else {
      midY = (srcPt.y - stub) - trackFraction * usableY;
    }

    if (isTVert) {
      rawPath = [
        srcPt,
        { x: srcPt.x, y: midY },
        { x: tgtPt.x, y: midY },
        tgtPt
      ];
    } else {
      rawPath = [
        srcPt,
        { x: srcPt.x, y: tgtPt.y },
        tgtPt
      ];
    }
  } else {
    const gapX = Math.abs(tgtPt.x - srcPt.x);
    const stubX = Math.min(22, Math.max(14, gapX * 0.18));
    const usableX = Math.max(0, gapX - 2 * stubX);

    // Separar hermanos en grupo Superior (< srcCy) y grupo Inferior (>= srcCy)
    const topGroup = allSrcSiblings.filter(s => s.cy < srcCy);
    const bottomGroup = allSrcSiblings.filter(s => s.cy >= srcCy);

    const isGoingTop = tgtCy < srcCy;
    let trackFraction = 0.5;

    if (isGoingTop && topGroup.length > 1) {
      // topGroup ordenado por cy ascendente (0 = más arriba / exterior, n-1 = más cerca del centro / interior)
      const idxInTop = topGroup.findIndex(s => (link && s.linkId === link.id) || s.id === targetNode.id);
      if (idxInTop !== -1) {
        trackFraction = idxInTop / (topGroup.length - 1);
      }
    } else if (!isGoingTop && bottomGroup.length > 1) {
      // bottomGroup ordenado por cy ascendente (0 = más cerca del centro / interior, n-1 = más abajo / exterior)
      const idxInBottom = bottomGroup.findIndex(s => (link && s.linkId === link.id) || s.id === targetNode.id);
      if (idxInBottom !== -1) {
        trackFraction = 1.0 - (idxInBottom / (bottomGroup.length - 1));
      }
    }

    let midX = (srcPt.x + tgtPt.x) / 2.0;
    if (sFace === 'right') {
      midX = (srcPt.x + stubX) + trackFraction * usableX;
    } else {
      midX = (srcPt.x - stubX) - trackFraction * usableX;
    }

    if (!isTVert) {
      rawPath = [
        srcPt,
        { x: midX, y: srcPt.y },
        { x: midX, y: tgtPt.y },
        tgtPt
      ];
    } else {
      rawPath = [
        srcPt,
        { x: tgtPt.x, y: srcPt.y },
        tgtPt
      ];
    }
  }

  // 4. Esquivar obstáculos de nodos intermedios (Evita tocar el contorno de otros nodos)
  const obstacles = getObstaclesForLink(sourceNode.id, targetNode.id);
  const clearedPath = avoidObstaclesOrthogonal(rawPath, obstacles);

  // 5. Simplificar puntos colineales / redundantes
  const simplified = [clearedPath[0]];
  for (let i = 1; i < clearedPath.length; i++) {
    const pt = clearedPath[i];
    const prev = simplified[simplified.length - 1];
    if (Math.hypot(pt.x - prev.x, pt.y - prev.y) > 0.5) {
      if (simplified.length >= 2) {
        const pPrev = simplified[simplified.length - 2];
        const isCollinearX = Math.abs(pPrev.x - prev.x) < 0.5 && Math.abs(prev.x - pt.x) < 0.5;
        const isCollinearY = Math.abs(pPrev.y - prev.y) < 0.5 && Math.abs(prev.y - pt.y) < 0.5;
        if (isCollinearX || isCollinearY) {
          simplified[simplified.length - 1] = pt;
          continue;
        }
      }
      simplified.push(pt);
    }
  }

  // 6. Suavizar esquinas con curvas de 8px
  const rounded = roundCorners(simplified, 8);

  const pts = [];
  for (let i = 0; i < rounded.length; i++) {
    pts.push(rounded[i].x, rounded[i].y);
  }
  return pts;
}

// ─── Colores y Estados Fijos de Enlaces (Verde OK / Rojo Problema) ─────────────

function getLinkColor(link, sourceNode, targetNode) {
  // ── Enlaces hacia Brazo FTTH / Ramal GPON ──
  const isGponBranch = (targetNode && targetNode.device_type === 'ftth_branch') || (sourceNode && sourceNode.device_type === 'ftth_branch') || !!(link && link.extra_data && link.extra_data.is_gpon_branch);
  if (isGponBranch) {
    const branchNode = (targetNode && targetNode.device_type === 'ftth_branch') ? targetNode : sourceNode;
    const isDown = branchNode.status === 'down' || branchNode.ping_status === 'down' || branchNode.extra_data?.port_status === 'down' || branchNode.extra_data?.port_status_code === 2;
    const hasAtyp = (branchNode.extra_data?.atypical_count || 0) > 0;
    if (isDown) return '#ef4444'; // Rojo si el puerto GPON está caído
    if (hasAtyp) return '#f59e0b'; // Amarillo si hay alertas ópticas
    return '#10b981'; // Verde fibra GPON
  }

  const isIntermap = !!(link && link.extra_data && (link.extra_data.is_intermap || link.extra_data.remote_node_id));

  const tgtPing = targetNode ? (targetNode.ping_status || targetNode.status || 'ok') : (link?.status || 'ok');
  const srcPing = sourceNode ? (sourceNode.ping_status || sourceNode.status || 'ok') : 'ok';
  const linkStat = link ? link.status : 'ok';

  const isProblem = (
    tgtPing === 'problem' || tgtPing === 'down' || tgtPing === 'critical' || tgtPing === 'error' ||
    srcPing === 'problem' || srcPing === 'down' || srcPing === 'critical' || srcPing === 'error' ||
    linkStat === 'problem' || linkStat === 'down' || linkStat === 'critical'
  );

  if (isProblem) {
    return '#ef4444'; // Rojo fijo para problemas de ping / conectividad
  }
  if (isIntermap) {
    return '#a855f7'; // Violeta para enlaces intermapa cuando está OK
  }
  return '#22c55e'; // Verde fijo cuando el ping al equipo está OK
}

function updateAllLinkColors() {
  if (!currentMap || !currentMap.nodes || !linksLayer) return;
  const nodeMap = new Map();
  currentMap.nodes.forEach(n => nodeMap.set(n.id, n));

  linkLines.forEach((linkObj) => {
    const { line, sourceId, targetId, link } = linkObj;
    const srcNode = nodeMap.get(sourceId);
    const tgtNode = nodeMap.get(targetId);
    if (line && line.getStage()) {
      const isIntermap = !!(link && link.extra_data && (link.extra_data.is_intermap || link.extra_data.remote_node_id));
      const color = getLinkColor(link, srcNode, tgtNode);
      line.stroke(color);
      line.fill(color);
      line.strokeWidth(isIntermap ? 2.5 : 2);
      line.shadowBlur(0);
      line.shadowOpacity(0);
    }
  });
  linksLayer.batchDraw();
}

function createLinkPortBadge(text, color = '#38bdf8') {
  const group = new Konva.Group({
    listening: false,
    perfectDrawEnabled: false
  });
  const padX = 3.5;
  const padY = 1.5;
  const txt = new Konva.Text({
    text: text,
    fontSize: 7.5,
    fontFamily: 'monospace',
    fontStyle: 'bold',
    fill: '#f8fafc',
    padding: 0
  });
  const w = txt.width() + padX * 2;
  const h = txt.height() + padY * 2;
  const bg = new Konva.Rect({
    width: w,
    height: h,
    fill: 'rgba(15, 23, 42, 0.95)',
    stroke: color,
    strokeWidth: 1,
    cornerRadius: 2.5,
    shadowColor: 'rgba(0, 0, 0, 0.6)',
    shadowBlur: 3,
    shadowOffset: { x: 0, y: 1 },
    shadowOpacity: 0.4
  });
  txt.x(padX);
  txt.y(padY);
  group.add(bg);
  group.add(txt);
  group.offset({ x: w / 2, y: h / 2 });
  return group;
}

function renderLink(link, nodesDict) {
  const source = nodesDict.get(link.source_node_id);
  const target = nodesDict.get(link.target_node_id);
  if (!source || !target) return;

  const pts = calculateLinkEndpoints(source, target, link);
  const isIntermap = !!(link.extra_data && (link.extra_data.is_intermap || link.extra_data.remote_node_id));
  const direction = link.extra_data?.direction || 'source_to_target';
  const arrowPts = getArrowPointsForDirection(pts, direction);

  const color = getLinkColor(link, source, target);

  const line = new Konva.Arrow({
    points: arrowPts,
    tension: 0,
    pointerLength: 9,
    pointerWidth: 8,
    stroke: color,
    fill: color,
    strokeWidth: isIntermap ? 2.5 : 2,
    dash: isIntermap ? [6, 4] : undefined,
    hitStrokeWidth: 14,
    lineCap: 'round',
    lineJoin: 'round',
    perfectDrawEnabled: false,
    id: link.id
  });

  // Etiquetas flotantes de interfaces/puertos
  let srcBadge = null;
  let tgtBadge = null;

  const updateBadgesPos = (ptsVec) => {
    if (!ptsVec || ptsVec.length < 4) return;

    // --- 1. UBICACIÓN Y ROTACIÓN DE ETIQUETA ORIGEN (SRC BADGE) ---
    if (srcBadge) {
      const p0x = ptsVec[0], p0y = ptsVec[1];
      const p1x = ptsVec[2], p1y = ptsVec[3];
      const dx0 = p1x - p0x;
      const dy0 = p1y - p0y;
      const segLen0 = Math.hypot(dx0, dy0);

      if (segLen0 > 14) {
        // Separación suficiente para no invadir el rectángulo del nodo origen
        const offsetDist = segLen0 > 75 ? 38 : Math.max(20, segLen0 * 0.42);
        const bx = p0x + (dx0 / segLen0) * offsetDist;
        const by = p0y + (dy0 / segLen0) * offsetDist;

        let angleDeg = Math.atan2(dy0, dx0) * (180 / Math.PI);
        if (angleDeg > 90) angleDeg -= 180;
        else if (angleDeg < -90) angleDeg += 180;

        srcBadge.position({ x: bx, y: by });
        srcBadge.rotation(angleDeg);
        srcBadge.visible(true);
        srcBadge.moveToTop();
      } else {
        srcBadge.visible(false);
      }
    }

    // --- 2. UBICACIÓN Y ROTACIÓN DE ETIQUETA DESTINO (TGT BADGE) ---
    if (tgtBadge) {
      const n = ptsVec.length;
      const pLast1x = ptsVec[n - 4], pLast1y = ptsVec[n - 3];
      const pLast2x = ptsVec[n - 2], pLast2y = ptsVec[n - 1];
      const dx1 = pLast2x - pLast1x;
      const dy1 = pLast2y - pLast1y;
      const segLen1 = Math.hypot(dx1, dy1);

      if (segLen1 > 14) {
        // Separación suficiente para no tocar la punta de flecha ni el nodo destino
        const offsetDist = segLen1 > 80 ? 42 : Math.max(22, segLen1 * 0.45);
        const bx = pLast2x - (dx1 / segLen1) * offsetDist;
        const by = pLast2y - (dy1 / segLen1) * offsetDist;

        let angleDeg = Math.atan2(dy1, dx1) * (180 / Math.PI);
        if (angleDeg > 90) angleDeg -= 180;
        else if (angleDeg < -90) angleDeg += 180;

        tgtBadge.position({ x: bx, y: by });
        tgtBadge.rotation(angleDeg);
        tgtBadge.visible(true);
        tgtBadge.moveToTop();
      } else {
        tgtBadge.visible(false);
      }
    }
  };

  // Agregar primero la línea y LUEGO las etiquetas encima para garantizar Z-index superior
  linksLayer.add(line);

  if (link.source_interface) {
    srcBadge = createLinkPortBadge(link.source_interface, '#38bdf8');
    linksLayer.add(srcBadge);
  }
  if (link.target_interface) {
    tgtBadge = createLinkPortBadge(link.target_interface, '#c084fc');
    linksLayer.add(tgtBadge);
  }
  updateBadgesPos(arrowPts);

  line.on('mouseenter mousemove', (e) => {
    document.body.style.cursor = 'pointer';
    line.stroke('#38bdf8');
    line.fill('#38bdf8');
    line.strokeWidth(3.5);
    linksLayer.batchDraw();

    const isGponBranch = target.device_type === 'ftth_branch' || source.device_type === 'ftth_branch' || !!link.extra_data?.is_gpon_branch;
    const gponTooltip = document.getElementById('canvas-gpon-tooltip');
    const standardTooltip = document.getElementById('canvas-link-tooltip');

    if (isGponBranch && gponTooltip) {
      const ftthNode = target.device_type === 'ftth_branch' ? target : source;
      const otherNode = target.device_type === 'ftth_branch' ? source : target;
      const extra = ftthNode.extra_data || {};
      const gponPort = extra.gpon_port || link.source_interface || 'GPON';

      const evt = e.evt || window.event;
      if (evt) {
        gponTooltip.style.left = `${evt.clientX + 14}px`;
        gponTooltip.style.top = `${evt.clientY + 14}px`;
      }

      const titleEl = document.getElementById('tooltip-gpon-title');
      const portEl = document.getElementById('tooltip-gpon-port');
      const oltEl = document.getElementById('tooltip-gpon-olt');
      const statusEl = document.getElementById('tooltip-gpon-status');
      
      if (titleEl) titleEl.textContent = `⚡ ${extra.branch_name || ftthNode.name}`;
      if (portEl) portEl.textContent = gponPort;
      if (oltEl) oltEl.textContent = extra.olt_name || otherNode.name || 'OLT';

      gponTooltip.style.display = 'block';
      if (standardTooltip) standardTooltip.style.display = 'none';

      fetchGponBranchTelemetry(ftthNode.id).then(telem => {
        if (!telem || gponTooltip.style.display === 'none') return;
        const inEl = document.getElementById('tooltip-gpon-traffic-in');
        const outEl = document.getElementById('tooltip-gpon-traffic-out');
        const volEl = document.getElementById('tooltip-gpon-volume');
        if (inEl) inEl.textContent = telem.traffic_in_fmt || '—';
        if (outEl) outEl.textContent = telem.traffic_out_fmt || '—';
        if (volEl) volEl.textContent = telem.volume_total_fmt || '—';

        const typCountEl = document.getElementById('tooltip-gpon-typical-count');
        const typAvgEl = document.getElementById('tooltip-gpon-typical-avg');
        const atypCountEl = document.getElementById('tooltip-gpon-atypical-count');
        const atypAvgEl = document.getElementById('tooltip-gpon-atypical-avg');

        if (typCountEl) typCountEl.textContent = `${telem.typical_count || 0} ONUs`;
        if (typAvgEl) typAvgEl.textContent = telem.typical_avg_dbm !== null ? `${telem.typical_avg_dbm} dBm` : '—';
        if (atypCountEl) atypCountEl.textContent = `${telem.atypical_count || 0} ONUs`;
        if (atypAvgEl) atypAvgEl.textContent = telem.atypical_avg_dbm !== null ? `${telem.atypical_avg_dbm} dBm` : '—';

        if (statusEl) {
          statusEl.textContent = telem.port_status === 'down' ? '● Link Down (Offline)' : (telem.atypical_count > 0 ? '● Alerta Óptica' : '● Link Up (Operativo)');
          statusEl.style.color = telem.port_status === 'down' ? '#f87171' : (telem.atypical_count > 0 ? '#fbbf24' : '#10b981');
          statusEl.style.background = telem.port_status === 'down' ? 'rgba(239,68,68,0.2)' : (telem.atypical_count > 0 ? 'rgba(245,158,11,0.2)' : 'rgba(16,185,129,0.2)');
        }
      });
      return;
    }

    const tooltip = document.getElementById('canvas-link-tooltip');
    if (tooltip) {
      const evt = e.evt || window.event;
      if (evt) {
        tooltip.style.left = `${evt.clientX + 14}px`;
        tooltip.style.top = `${evt.clientY + 14}px`;
      }

      const sNodeName = source.name || 'Nodo A';
      const tNodeName = target.name || 'Nodo B';
      const srcEl = document.getElementById('tooltip-link-src');
      const tgtEl = document.getElementById('tooltip-link-tgt');
      const srcPortEl = document.getElementById('tooltip-link-src-port');
      const tgtPortEl = document.getElementById('tooltip-link-tgt-port');
      const cableEl = document.getElementById('tooltip-link-cable');
      const cableBadge = document.getElementById('tooltip-link-cable-badge');

      if (srcEl) srcEl.textContent = sNodeName;
      if (tgtEl) tgtEl.textContent = tNodeName;
      if (srcPortEl) srcPortEl.textContent = link.source_interface ? `[${link.source_interface}]` : '';
      if (tgtPortEl) tgtPortEl.textContent = link.target_interface ? `[${link.target_interface}]` : '';
      if (cableEl) cableEl.textContent = (link.cable_type || 'cat6').toUpperCase();
      if (cableBadge) cableBadge.textContent = link.netbox_cable_id ? `NetBox Cable #${link.netbox_cable_id}` : 'Lógico / Visual';

      API.getLinkTelemetry(link.id).then(telem => {
        if (!telem) return;
        const statusEl = document.getElementById('tooltip-link-status');
        if (statusEl) {
          statusEl.textContent = telem.status === 'down' ? '● Caído (Down)' : '● Operativo (Up)';
          statusEl.style.color = telem.status === 'down' ? '#f87171' : '#10b981';
          statusEl.style.background = telem.status === 'down' ? 'rgba(239,68,68,0.2)' : 'rgba(16,185,129,0.2)';
        }

        const tIn = telem.source?.telemetry?.traffic_in_fmt || telem.target?.telemetry?.traffic_in_fmt || '—';
        const tOut = telem.source?.telemetry?.traffic_out_fmt || telem.target?.telemetry?.traffic_out_fmt || '—';
        const inEl = document.getElementById('tooltip-traffic-in');
        const outEl = document.getElementById('tooltip-traffic-out');
        if (inEl) inEl.textContent = tIn;
        if (outEl) outEl.textContent = tOut;

        const optRow = document.getElementById('tooltip-optical-row');
        const optData = telem.source?.telemetry?.optical || telem.target?.telemetry?.optical;
        if (optData && optData.rx_power_dbm !== undefined && optRow) {
          optRow.style.display = 'flex';
          const rxEl = document.getElementById('tooltip-optical-rx');
          const txEl = document.getElementById('tooltip-optical-tx');
          if (rxEl) rxEl.textContent = `${optData.rx_power_dbm} dBm`;
          if (txEl) txEl.textContent = optData.tx_power_dbm !== undefined ? `${optData.tx_power_dbm} dBm` : '—';
        } else if (optRow) {
          optRow.style.display = 'none';
        }

        const wRow = document.getElementById('tooltip-wireless-row');
        const wData = telem.source?.telemetry?.wireless || telem.target?.telemetry?.wireless;
        if (wData && wData.rssi_dbm !== undefined && wRow) {
          wRow.style.display = 'flex';
          const rssiEl = document.getElementById('tooltip-wireless-rssi');
          const snrEl = document.getElementById('tooltip-wireless-snr');
          if (rssiEl) rssiEl.textContent = `${wData.rssi_dbm} dBm`;
          if (snrEl) snrEl.textContent = wData.snr_db !== undefined ? `${wData.snr_db} dB` : '—';
        } else if (wRow) {
          wRow.style.display = 'none';
        }
      }).catch(() => {});

      tooltip.style.display = 'block';
    }
  });

  line.on('mouseleave', () => {
    document.body.style.cursor = 'default';
    const curColor = getLinkColor(link, source, target);
    line.stroke(curColor);
    line.fill(curColor);
    line.strokeWidth(isIntermap ? 2.5 : 2);
    linksLayer.batchDraw();

    const tooltip = document.getElementById('canvas-link-tooltip');
    if (tooltip) tooltip.style.display = 'none';
    const gponTooltip = document.getElementById('canvas-gpon-tooltip');
    if (gponTooltip) gponTooltip.style.display = 'none';
  });

  line.on('click tap', (e) => {
    e.cancelBubble = true;
    const tooltip = document.getElementById('canvas-link-tooltip');
    if (tooltip) tooltip.style.display = 'none';
    const gponTooltip = document.getElementById('canvas-gpon-tooltip');
    if (gponTooltip) gponTooltip.style.display = 'none';
    openLinkPropertiesModal(link, source, target);
  });

  linkLines.set(link.id, { line, srcBadge, tgtBadge, updateBadgesPos, sourceId: source.id, targetId: target.id, link, sourceNode: source, targetNode: target });
}

function openLinkPropertiesModal(link, sourceNode, targetNode) {
  const modal = document.getElementById('modal-link-properties');
  if (!modal) return;

  const inputId = document.getElementById('link-modal-id');
  const txtSrcToTgt = document.getElementById('link-text-src-to-tgt');
  const srcNamePadre = document.getElementById('link-src-name-padre');
  const tgtNameHijo = document.getElementById('link-tgt-name-hijo');
  const txtTgtToSrc = document.getElementById('link-text-tgt-to-src');
  const tgtNamePadre = document.getElementById('link-tgt-name-padre');
  const srcNameHijo = document.getElementById('link-src-name-hijo');

  const lblSrcIface = document.getElementById('link-lbl-src-iface');
  const lblTgtIface = document.getElementById('link-lbl-tgt-iface');
  const selectSrcIface = document.getElementById('select-link-src-iface');
  const selectTgtIface = document.getElementById('select-link-tgt-iface');
  const inputSrcIface = document.getElementById('input-link-src-iface');
  const inputTgtIface = document.getElementById('input-link-tgt-iface');
  const selectCableType = document.getElementById('select-link-cable-type');
  const badgeNetbox = document.getElementById('link-netbox-cable-badge');

  const selectZbxSrc = document.getElementById('select-link-zbx-src-iface');
  const selectZbxTgt = document.getElementById('select-link-zbx-tgt-iface');
  const previewIn = document.getElementById('link-preview-in');
  const previewOut = document.getElementById('link-preview-out');
  const previewExtra = document.getElementById('link-preview-extra');
  const zbxStatusBadge = document.getElementById('link-zbx-status-badge');

  const radioSrcToTgt = document.getElementById('radio-dir-source-to-target');
  const radioTgtToSrc = document.getElementById('radio-dir-target-to-source');

  const btnClose = document.getElementById('btn-close-link-modal');
  const btnCancel = document.getElementById('btn-cancel-link-modal');
  const btnSave = document.getElementById('btn-save-link-modal');
  const btnDelete = document.getElementById('btn-delete-link-modal');

  inputId.value = link.id;
  const sName = sourceNode.name || 'Nodo A';
  const tName = targetNode.name || 'Nodo B';

  if (txtSrcToTgt) txtSrcToTgt.textContent = `${sName} ➔ ${tName}`;
  if (srcNamePadre) srcNamePadre.textContent = sName;
  if (tgtNameHijo) tgtNameHijo.textContent = tName;

  if (txtTgtToSrc) txtTgtToSrc.textContent = `${tName} ➔ ${sName}`;
  if (tgtNamePadre) tgtNamePadre.textContent = tName;
  if (srcNameHijo) srcNameHijo.textContent = sName;

  if (lblSrcIface) lblSrcIface.textContent = `Puerto en ${sName}:`;
  if (lblTgtIface) lblTgtIface.textContent = `Puerto en ${tName}:`;

  if (inputSrcIface) {
    inputSrcIface.value = link.source_interface || '';
    inputSrcIface.style.display = 'none';
  }
  if (inputTgtIface) {
    inputTgtIface.value = link.target_interface || '';
    inputTgtIface.style.display = 'none';
  }

  if (selectCableType) {
    selectCableType.value = link.cable_type || 'cat6';
  }

  if (badgeNetbox) {
    if (link.netbox_cable_id) {
      badgeNetbox.textContent = `NetBox Cable #${link.netbox_cable_id} (${link.cable_status || 'connected'})`;
      badgeNetbox.style.background = 'rgba(16, 185, 129, 0.15)';
      badgeNetbox.style.color = '#10b981';
      badgeNetbox.style.borderColor = 'rgba(16, 185, 129, 0.3)';
    } else {
      badgeNetbox.textContent = 'Sin Cable en NetBox';
      badgeNetbox.style.background = 'rgba(148, 163, 184, 0.1)';
      badgeNetbox.style.color = 'var(--text-muted)';
      badgeNetbox.style.borderColor = 'var(--border-color)';
    }
  }

  const curDir = link.extra_data?.direction || 'source_to_target';
  if (curDir === 'target_to_source') {
    if (radioTgtToSrc) radioTgtToSrc.checked = true;
  } else {
    if (radioSrcToTgt) radioSrcToTgt.checked = true;
  }

  const checkVisualOnly = document.getElementById('check-link-visual-only');
  const isVisualOnly = !!(link.extra_data?.is_visual_only || link.extra_data?.sync_zabbix === false || link.extra_data?.is_simple_link);
  if (checkVisualOnly) checkVisualOnly.checked = isVisualOnly;

  // Carga asíncrona de puertos disponibles desde NetBox
  const populateIfaceSelect = async (node, selectEl, inputEl, currentIfaceName, currentIfaceId) => {
    if (!selectEl) return;
    selectEl.innerHTML = '<option value="">⏳ Cargando puertos...</option>';
    let ifaces = [];
    const devId = node ? (node.device_id || node.extra_data?.device_id || node.extra_data?.netbox_id) : null;
    if (devId) {
      try {
        ifaces = await API.getDeviceInterfaces(devId);
      } catch (err) {
        console.warn(`Error obteniendo interfaces del dispositivo ${devId}:`, err);
      }
    }

    selectEl.innerHTML = '';
    const defOpt = document.createElement('option');
    defOpt.value = '';
    defOpt.textContent = '-- Seleccionar Puerto --';
    selectEl.appendChild(defOpt);

    let matchFound = false;
    if (Array.isArray(ifaces) && ifaces.length > 0) {
      ifaces.forEach(iface => {
        const opt = document.createElement('option');
        opt.value = iface.name;
        opt.dataset.ifaceId = iface.id;
        const speedStr = iface.type ? ` (${iface.type})` : '';
        const connStr = iface.is_connected && iface.id !== currentIfaceId ? ' [Ocupado]' : '';
        opt.textContent = `${iface.name}${speedStr}${connStr}`;
        if (currentIfaceId && iface.id === currentIfaceId) {
          opt.selected = true;
          matchFound = true;
        } else if (!currentIfaceId && currentIfaceName && (iface.name === currentIfaceName || iface.name.toLowerCase() === currentIfaceName.toLowerCase())) {
          opt.selected = true;
          matchFound = true;
        }
        selectEl.appendChild(opt);
      });
    }

    const manualOpt = document.createElement('option');
    manualOpt.value = '__manual__';
    manualOpt.textContent = '✏️ Puerto Personalizado...';
    if (currentIfaceName && !matchFound) {
      manualOpt.selected = true;
      if (inputEl) {
        inputEl.style.display = 'block';
        inputEl.value = currentIfaceName;
      }
    }
    selectEl.appendChild(manualOpt);

    selectEl.onchange = () => {
      if (selectEl.value === '__manual__') {
        if (inputEl) {
          inputEl.style.display = 'block';
          inputEl.focus();
        }
      } else {
        if (inputEl) {
          inputEl.style.display = 'none';
          inputEl.value = selectEl.value;
        }
      }
    };
  };

  // Carga asíncrona de interfaces monitoreadas desde Zabbix
  const populateZabbixSelect = async (node, selectEl, currentZbxIface, fallbackPhysicalName) => {
    if (!selectEl) return;
    selectEl.innerHTML = '<option value="">⏳ Cargando Zabbix...</option>';
    let ifaces = [];
    try {
      ifaces = await API.getNodeZabbixInterfaces(node.id);
    } catch (e) {
      console.warn('Error fetching zabbix ifaces:', e);
    }

    selectEl.innerHTML = '<option value="">-- Detectar Automáticamente --</option>';
    let matchFound = false;

    if (Array.isArray(ifaces) && ifaces.length > 0) {
      ifaces.forEach(iface => {
        const opt = document.createElement('option');
        opt.value = iface.name;
        const speedText = iface.speed && iface.speed !== '—' ? ` (${iface.speed})` : '';
        const trafficText = (iface.traffic_in_fmt && iface.traffic_in_fmt !== '—') ? ` [⬇ ${iface.traffic_in_fmt} / ⬆ ${iface.traffic_out_fmt}]` : '';
        const optText = iface.optical && iface.optical.rx_power_dbm !== undefined ? ` [Rx: ${iface.optical.rx_power_dbm} dBm]` : '';
        const wText = iface.wireless && iface.wireless.rssi_dbm !== undefined ? ` [RSSI: ${iface.wireless.rssi_dbm} dBm]` : '';

        opt.textContent = `${iface.display_name}${speedText}${trafficText}${optText}${wText}`;

        if (currentZbxIface && (iface.name.toLowerCase() === currentZbxIface.toLowerCase() || iface.key.toLowerCase() === currentZbxIface.toLowerCase())) {
          opt.selected = true;
          matchFound = true;
        } else if (!currentZbxIface && fallbackPhysicalName && (iface.name.toLowerCase() === fallbackPhysicalName.toLowerCase() || iface.key.toLowerCase() === fallbackPhysicalName.toLowerCase())) {
          opt.selected = true;
          matchFound = true;
        }
        selectEl.appendChild(opt);
      });
    }

    if (currentZbxIface && !matchFound) {
      const customOpt = document.createElement('option');
      customOpt.value = currentZbxIface;
      customOpt.textContent = `⚡ ${currentZbxIface} (Configurado)`;
      customOpt.selected = true;
      selectEl.appendChild(customOpt);
    }
  };

  let chosenSrcSubmapDev = null;
  let chosenTgtSubmapDev = null;
  let srcTargetMapId = null;
  let tgtTargetMapId = null;

  const setupSideControls = async (node, isSource, subContainerId, subSelectId, ifaceSelectEl, ifaceInputEl, zbxSelectEl, currentIfaceName, currentIfaceId, currentZbxIface) => {
    const isSubmap = node.device_type === 'submap' || node.device_type === 'parent_map' || !!node.extra_data?.is_parent_shortcut;
    const subContainer = document.getElementById(subContainerId);
    const subSelect = document.getElementById(subSelectId);

    if (isSubmap) {
      if (subContainer) subContainer.style.display = 'block';
      let tMapId = node.extra_data?.target_map_id;
      if (!tMapId) {
        if (node.device_type === 'parent_map' || node.extra_data?.is_parent_shortcut) {
          tMapId = currentMap.parent_map_id;
        } else if (Array.isArray(allMaps)) {
          const clean = (node.name || '').replace('📁', '').trim().toLowerCase();
          const matched = allMaps.find(m => m.name.toLowerCase().trim() === clean || m.id === clean);
          if (matched) tMapId = matched.id;
        }
      }

      if (isSource) srcTargetMapId = tMapId; else tgtTargetMapId = tMapId;

      let submapNodes = [];
      if (tMapId) {
        try {
          const mDetail = await API.getMapDetail(tMapId);
          if (mDetail && Array.isArray(mDetail.nodes)) {
            submapNodes = mDetail.nodes.filter(n => n.device_type !== 'submap' && n.device_type !== 'parent_map' && !n.extra_data?.is_parent_shortcut);
          }
        } catch (e) {
          console.warn('Error cargando equipos de submapa:', e);
        }
      }

      if (subSelect) {
        subSelect.innerHTML = '';
        if (submapNodes.length === 0) {
          subSelect.innerHTML = '<option value="">(Sin equipos en este submapa)</option>';
        } else {
          const defOpt = document.createElement('option');
          defOpt.value = '';
          defOpt.textContent = `-- Seleccionar Equipo (${submapNodes.length}) --`;
          subSelect.appendChild(defOpt);

          const linkExtra = link.extra_data || {};
          const targetRemoteId = isSource ? (linkExtra.source_submap_node_id || linkExtra.remote_node_id) : (linkExtra.target_submap_node_id || linkExtra.remote_node_id);
          const targetRemoteName = isSource ? (linkExtra.source_submap_device_name || linkExtra.remote_node_name) : (linkExtra.target_submap_device_name || linkExtra.remote_node_name);

          let matchedDev = null;
          submapNodes.forEach(sn => {
            const opt = document.createElement('option');
            opt.value = sn.id;
            opt.dataset.deviceId = sn.device_id || '';
            opt.textContent = `🖥️ ${sn.name} [${sn.ip || 'Sin IP'}] (${sn.extra_data?.model || sn.device_type || 'Dispositivo'})`;
            if ((targetRemoteId && sn.id === targetRemoteId) || (targetRemoteName && sn.name === targetRemoteName)) {
              opt.selected = true;
              matchedDev = sn;
            }
            subSelect.appendChild(opt);
          });

          if (!matchedDev && submapNodes.length > 0) {
            subSelect.selectedIndex = 1;
            matchedDev = submapNodes[0];
          }

          const applySubmapDev = (dev) => {
            if (isSource) chosenSrcSubmapDev = dev; else chosenTgtSubmapDev = dev;
            populateIfaceSelect(dev, ifaceSelectEl, ifaceInputEl, currentIfaceName, currentIfaceId);
            populateZabbixSelect(dev, zbxSelectEl, currentZbxIface, currentIfaceName);
          };

          if (matchedDev) applySubmapDev(matchedDev);

          subSelect.onchange = () => {
            const sel = submapNodes.find(n => n.id === subSelect.value);
            if (sel) applySubmapDev(sel);
          };
        }
      }
    } else {
      if (subContainer) subContainer.style.display = 'none';
      populateIfaceSelect(node, ifaceSelectEl, ifaceInputEl, currentIfaceName, currentIfaceId);
      populateZabbixSelect(node, zbxSelectEl, currentZbxIface, currentIfaceName);
    }
  };

  setupSideControls(sourceNode, true, 'link-src-submap-container', 'select-link-src-submap-dev', selectSrcIface, inputSrcIface, selectZbxSrc, link.source_interface, link.source_interface_id, link.zabbix_src_interface);
  setupSideControls(targetNode, false, 'link-tgt-submap-container', 'select-link-tgt-submap-dev', selectTgtIface, inputTgtIface, selectZbxTgt, link.target_interface, link.target_interface_id, link.zabbix_tgt_interface);

  // Consultar telemetría viva del enlace para el preview en modal
  API.getLinkTelemetry(link.id).then(telemetry => {
    if (!telemetry) return;
    if (previewIn) previewIn.textContent = telemetry.source?.telemetry?.traffic_in_fmt || telemetry.target?.telemetry?.traffic_in_fmt || '—';
    if (previewOut) previewOut.textContent = telemetry.source?.telemetry?.traffic_out_fmt || telemetry.target?.telemetry?.traffic_out_fmt || '—';
    if (previewExtra) {
      let extraTxt = [];
      const opt = telemetry.source?.telemetry?.optical || telemetry.target?.telemetry?.optical;
      if (opt && opt.rx_power_dbm !== undefined) extraTxt.push(`Rx: ${opt.rx_power_dbm} dBm`);
      const w = telemetry.source?.telemetry?.wireless || telemetry.target?.telemetry?.wireless;
      if (w && w.rssi_dbm !== undefined) extraTxt.push(`RSSI: ${w.rssi_dbm} dBm`);
      if (extraTxt.length > 0) previewExtra.textContent = extraTxt.join(' · ');
    }
    if (zbxStatusBadge) {
      if (telemetry.status === 'down') {
        zbxStatusBadge.textContent = '● Caído (Down)';
        zbxStatusBadge.style.color = '#f87171';
        zbxStatusBadge.style.background = 'rgba(239, 68, 68, 0.2)';
        zbxStatusBadge.style.borderColor = 'rgba(239, 68, 68, 0.4)';
      } else {
        zbxStatusBadge.textContent = '● Operativo (Up)';
        zbxStatusBadge.style.color = '#10b981';
        zbxStatusBadge.style.background = 'rgba(16, 185, 129, 0.15)';
        zbxStatusBadge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
      }
    }
  }).catch(() => {});

  modal.style.display = 'flex';

  const cleanUp = () => {
    modal.style.display = 'none';
    if (btnClose) btnClose.onclick = null;
    if (btnCancel) btnCancel.onclick = null;
    if (btnSave) btnSave.onclick = null;
    if (btnDelete) btnDelete.onclick = null;
    if (selectSrcIface) selectSrcIface.onchange = null;
    if (selectTgtIface) selectTgtIface.onchange = null;
  };

  if (btnClose) btnClose.onclick = cleanUp;
  if (btnCancel) btnCancel.onclick = cleanUp;

  if (btnSave) {
    btnSave.onclick = async () => {
      const chosenDir = radioTgtToSrc && radioTgtToSrc.checked ? 'target_to_source' : 'source_to_target';

      let srcIface = '';
      let srcIfaceId = null;
      if (selectSrcIface && selectSrcIface.value === '__manual__') {
        srcIface = inputSrcIface ? inputSrcIface.value.trim() : '';
      } else if (selectSrcIface && selectSrcIface.value) {
        srcIface = selectSrcIface.value;
        const selectedOpt = selectSrcIface.options[selectSrcIface.selectedIndex];
        if (selectedOpt && selectedOpt.dataset.ifaceId) {
          srcIfaceId = parseInt(selectedOpt.dataset.ifaceId, 10);
        }
      } else if (inputSrcIface) {
        srcIface = inputSrcIface.value.trim();
      }

      let tgtIface = '';
      let tgtIfaceId = null;
      if (selectTgtIface && selectTgtIface.value === '__manual__') {
        tgtIface = inputTgtIface ? inputTgtIface.value.trim() : '';
      } else if (selectTgtIface && selectTgtIface.value) {
        tgtIface = selectTgtIface.value;
        const selectedOpt = selectTgtIface.options[selectTgtIface.selectedIndex];
        if (selectedOpt && selectedOpt.dataset.ifaceId) {
          tgtIfaceId = parseInt(selectedOpt.dataset.ifaceId, 10);
        }
      } else if (inputTgtIface) {
        tgtIface = inputTgtIface.value.trim();
      }

      const cableType = selectCableType ? selectCableType.value : (link.cable_type || 'cat6');
      const zbxSrcIface = selectZbxSrc ? selectZbxSrc.value.trim() : '';
      const zbxTgtIface = selectZbxTgt ? selectZbxTgt.value.trim() : '';
      const visualOnly = checkVisualOnly ? checkVisualOnly.checked : false;

      const updatedExtra = Object.assign({}, link.extra_data || {}, {
        direction: chosenDir,
        is_visual_only: visualOnly,
        sync_zabbix: !visualOnly,
        is_simple_link: visualOnly
      });

      if (chosenSrcSubmapDev) {
        updatedExtra.is_intermap = true;
        updatedExtra.source_submap_node_id = chosenSrcSubmapDev.id;
        updatedExtra.source_submap_device_name = chosenSrcSubmapDev.name;
        updatedExtra.source_submap_device_id = chosenSrcSubmapDev.device_id;
        updatedExtra.remote_node_id = chosenSrcSubmapDev.id;
        updatedExtra.remote_node_name = chosenSrcSubmapDev.name;
        if (srcTargetMapId) updatedExtra.remote_map_id = srcTargetMapId;
      }

      if (chosenTgtSubmapDev) {
        updatedExtra.is_intermap = true;
        updatedExtra.target_submap_node_id = chosenTgtSubmapDev.id;
        updatedExtra.target_submap_device_name = chosenTgtSubmapDev.name;
        updatedExtra.target_submap_device_id = chosenTgtSubmapDev.device_id;
        updatedExtra.remote_node_id = chosenTgtSubmapDev.id;
        updatedExtra.remote_node_name = chosenTgtSubmapDev.name;
        if (tgtTargetMapId) updatedExtra.remote_map_id = tgtTargetMapId;
      }

      try {
        const updatePayload = {
          source_interface: srcIface,
          target_interface: tgtIface,
          source_interface_id: srcIfaceId,
          target_interface_id: tgtIfaceId,
          cable_type: cableType,
          zabbix_src_interface: zbxSrcIface || null,
          zabbix_tgt_interface: zbxTgtIface || null,
          extra_data: updatedExtra
        };

        const res = await API.updateLink(link.id, updatePayload);

        link.extra_data = updatedExtra;
        link.source_interface = res.source_interface ?? srcIface;
        link.target_interface = res.target_interface ?? tgtIface;
        link.source_interface_id = res.source_interface_id ?? srcIfaceId;
        link.target_interface_id = res.target_interface_id ?? tgtIfaceId;
        link.netbox_cable_id = res.netbox_cable_id ?? link.netbox_cable_id;
        link.cable_type = res.cable_type ?? cableType;
        link.cable_status = res.cable_status ?? link.cable_status;
        link.zabbix_src_interface = res.zabbix_src_interface ?? zbxSrcIface;
        link.zabbix_tgt_interface = res.zabbix_tgt_interface ?? zbxTgtIface;

        // Re-renderizar el enlace para actualizar etiquetas de puertos y flechas
        const linkEntry = linkLines.get(link.id);
        if (linkEntry) {
          if (linkEntry.line) linkEntry.line.destroy();
          if (linkEntry.srcBadge) linkEntry.srcBadge.destroy();
          if (linkEntry.tgtBadge) linkEntry.tgtBadge.destroy();
          linkLines.delete(link.id);
        }
        const dict = new Map();
        if (currentMap && currentMap.nodes) currentMap.nodes.forEach(n => dict.set(n.id, n));
        renderLink(link, dict);
        if (linksLayer) linksLayer.batchDraw();

        cleanUp();
      } catch (err) {
        alert('Error guardando enlace: ' + err.message);
      }
    };
  }

  if (btnDelete) {
    btnDelete.onclick = async () => {
      if (!confirm(`¿Estás seguro de eliminar el enlace entre "${sName}" y "${tName}"?`)) return;
      try {
        await API.deleteLink(link.id);
        const linkEntry = linkLines.get(link.id);
        if (linkEntry) {
          if (linkEntry.line) linkEntry.line.destroy();
          if (linkEntry.srcBadge) linkEntry.srcBadge.destroy();
          if (linkEntry.tgtBadge) linkEntry.tgtBadge.destroy();
        }
        linkLines.delete(link.id);
        if (currentMap && currentMap.links) {
          currentMap.links = currentMap.links.filter(l => l.id !== link.id);
        }
        linksLayer.batchDraw();
        cleanUp();
      } catch (err) {
        alert('Error eliminando enlace: ' + err.message);
      }
    };
  }
}

function updateAttachedLinks(nodeId, newX, newY) {
  let hasUpdated = false;
  if (!currentMap || !currentMap.nodes) return;

  const nodeMap = new Map();
  currentMap.nodes.forEach(n => nodeMap.set(n.id, n));

  linkLines.forEach((linkObj, linkId) => {
    const { line, srcBadge, tgtBadge, updateBadgesPos, sourceId, targetId, link } = linkObj;
    if (sourceId === nodeId || targetId === nodeId) {
      const srcNode = nodeMap.get(sourceId);
      const tgtNode = nodeMap.get(targetId);
      if (srcNode && tgtNode && line) {
        const pts = calculateLinkEndpoints(srcNode, tgtNode, link);
        const direction = link.extra_data?.direction || 'source_to_target';
        const arrowPts = getArrowPointsForDirection(pts, direction);
        line.points(arrowPts);
        line.tension(0);
        if (updateBadgesPos) updateBadgesPos(arrowPts);
        hasUpdated = true;
      }
    }
  });

  if (hasUpdated && linksLayer) {
    linksLayer.batchDraw();
  }
}

function updateAllLinks() {
  if (!currentMap || !currentMap.nodes || !linksLayer) return;
  const nodeMap = new Map();
  currentMap.nodes.forEach(n => nodeMap.set(n.id, n));

  linkLines.forEach((linkObj, linkId) => {
    const { line, srcBadge, tgtBadge, updateBadgesPos, sourceId, targetId, link } = linkObj;
    const srcNode = nodeMap.get(sourceId);
    const tgtNode = nodeMap.get(targetId);
    if (srcNode && tgtNode && line) {
      const pts = calculateLinkEndpoints(srcNode, tgtNode, link);
      const direction = link.extra_data?.direction || 'source_to_target';
      const arrowPts = getArrowPointsForDirection(pts, direction);
      line.points(arrowPts);
      line.tension(0);
      if (updateBadgesPos) updateBadgesPos(arrowPts);
    }
  });
  linksLayer.batchDraw();
}

// ─── 5. Herramienta de Conexión de Enlaces ──────────────────────────────────
function startLinkMode() {
  linkMode = true;
  linkSourceNode = null;
  linkTargetNodes.clear();
  nodeGroups.forEach(grp => grp.draggable(false));
  document.getElementById('btn-toggle-link-mode').classList.add('btn-active');
  const banner = document.getElementById('link-mode-banner');
  if (banner) banner.style.display = 'flex';
  const bannerText = document.getElementById('link-mode-text');
  if (bannerText) bannerText.textContent = 'Paso 1: Haz clic en el nodo origen';
  const btnMulti = document.getElementById('btn-confirm-multi-link');
  if (btnMulti) btnMulti.style.display = 'none';
}

function cancelLinkMode() {
  linkMode = false;
  nodeGroups.forEach(grp => grp.draggable(true));
  if (stage) stage.draggable(true);

  if (linkSourceNode) {
    const grp = nodeGroups.get(linkSourceNode.id);
    if (grp) {
      const isParentShortcut = linkSourceNode.device_type === 'parent_map' || !!linkSourceNode.extra_data?.is_parent_shortcut;
      const isSubmap = linkSourceNode.device_type === 'submap' || isParentShortcut;
      const curPingStatus = linkSourceNode.ping_status || linkSourceNode.status;
      grp.findOne('.box').stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
      grp.findOne('.box').strokeWidth(isSubmap ? 2 : 1.5);
    }
  }

  linkTargetNodes.forEach(tgtNode => {
    const grp = nodeGroups.get(tgtNode.id);
    if (grp) {
      const isParentShortcut = tgtNode.device_type === 'parent_map' || !!tgtNode.extra_data?.is_parent_shortcut;
      const isSubmap = tgtNode.device_type === 'submap' || isParentShortcut;
      const curPingStatus = tgtNode.ping_status || tgtNode.status;
      grp.findOne('.box').stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
      grp.findOne('.box').strokeWidth(isSubmap ? 2 : 1.5);
    }
  });

  linkSourceNode = null;
  linkTargetNodes.clear();
  document.getElementById('btn-toggle-link-mode').classList.remove('btn-active');
  const banner = document.getElementById('link-mode-banner');
  if (banner) banner.style.display = 'none';
  const btnMulti = document.getElementById('btn-confirm-multi-link');
  if (btnMulti) btnMulti.style.display = 'none';
  nodesLayer.batchDraw();
}

function promptIntermapLink(sourceNode, targetNode, targetMapId, remoteNodes, isSourceNav) {
  return new Promise((resolve) => {
    const modal = document.getElementById('modal-intermap-link');
    const select = document.getElementById('select-intermap-remote-node');
    const searchInput = document.getElementById('input-search-intermap-remote');
    const localNameEl = document.getElementById('intermap-local-name');
    const targetNameEl = document.getElementById('intermap-target-name');
    const countEl = document.getElementById('intermap-candidate-count');
    const emptyNotice = document.getElementById('intermap-empty-notice');
    const btnConfirm = document.getElementById('btn-confirm-intermap-link');
    const btnSimple = document.getElementById('btn-simple-intermap-link');
    const btnCancel = document.getElementById('btn-cancel-intermap-link');
    const btnClose = document.getElementById('btn-close-intermap-modal');

    if (!modal || !select) {
      resolve(remoteNodes[0]);
      return;
    }

    const deviceNode = isSourceNav ? targetNode : sourceNode;
    const navNode = isSourceNav ? sourceNode : targetNode;

    if (localNameEl) localNameEl.textContent = `${deviceNode.name} (${deviceNode.ip || deviceNode.device_type || 'Local'})`;
    if (targetNameEl) targetNameEl.textContent = navNode.name || 'Submapa';

    if (searchInput) searchInput.value = '';

    const renderOptions = (filterText = '') => {
      select.innerHTML = '';
      const q = filterText.toLowerCase().trim();
      const filtered = remoteNodes.filter(rn => {
        if (!q) return true;
        const txt = `${rn.name} ${rn.ip || ''} ${rn.device_type || ''} ${rn.extra_data?.model || ''} ${rn.extra_data?.role || ''}`.toLowerCase();
        return txt.includes(q);
      });

      if (countEl) countEl.textContent = `${filtered.length} de ${remoteNodes.length} equipos`;

      if (filtered.length === 0) {
        if (remoteNodes.length === 0) {
          if (emptyNotice) emptyNotice.style.display = 'block';
        } else {
          select.innerHTML = '<option value="" disabled>Sin coincidencias con la búsqueda</option>';
        }
      } else {
        if (emptyNotice) emptyNotice.style.display = 'none';
        filtered.forEach((rn, idx) => {
          const opt = document.createElement('option');
          opt.value = rn.id;
          const ipStr = rn.ip ? ` [${rn.ip}]` : '';
          const roleStr = rn.device_type || rn.extra_data?.role || 'Dispositivo';
          opt.textContent = `🖥️ ${rn.name}${ipStr} — (${roleStr})`;
          if (idx === 0) opt.selected = true;
          select.appendChild(opt);
        });
      }
    };

    renderOptions('');

    if (searchInput) {
      searchInput.oninput = (e) => renderOptions(e.target.value);
    }

    modal.style.display = 'flex';

    // Doble clic en una opción para confirmar inmediatamente
    select.ondblclick = () => {
      const chosenId = select.value;
      const chosenNode = remoteNodes.find(n => n.id === chosenId) || remoteNodes[0];
      cleanUp();
      resolve(chosenNode);
    };

    const cleanUp = () => {
      modal.style.display = 'none';
      btnConfirm.onclick = null;
      if (btnSimple) btnSimple.onclick = null;
      btnCancel.onclick = null;
      btnClose.onclick = null;
      if (searchInput) searchInput.oninput = null;
      select.ondblclick = null;
    };

    btnConfirm.onclick = () => {
      const chosenId = select.value;
      const chosenNode = remoteNodes.find(n => n.id === chosenId) || remoteNodes[0];
      cleanUp();
      resolve(chosenNode);
    };

    if (btnSimple) {
      btnSimple.onclick = () => {
        cleanUp();
        resolve({ is_simple: true });
      };
    }

    btnCancel.onclick = () => {
      cleanUp();
      resolve(null);
    };

    btnClose.onclick = () => {
      cleanUp();
      resolve(null);
    };
  });
}

async function handleLinkNodeClick(node, evt = {}) {
  if (!linkSourceNode) {
    // ── PASO 1: Selección de Origen ──
    linkSourceNode = node;
    linkTargetNodes.clear();
    const grp = nodeGroups.get(node.id);
    if (grp) {
      grp.findOne('.box').stroke('#10b981');
      grp.findOne('.box').strokeWidth(3);
      nodesLayer.batchDraw();
    }
    const bannerText = document.getElementById('link-mode-text');
    if (bannerText) {
      bannerText.textContent = `Paso 2: Haz clic en el destino o arrastra con Clic Derecho para seleccionar múltiples nodos (desde "${node.name}")`;
    }
    const btnMulti = document.getElementById('btn-confirm-multi-link');
    if (btnMulti) btnMulti.style.display = 'none';
  } else {
    // ── PASO 2: Selección de Destino(s) ──
    if (linkSourceNode.id === node.id) {
      alert('No puedes conectar un nodo consigo mismo.');
      return;
    }

    const isMultiModifier = !!(evt && (evt.shiftKey || evt.ctrlKey || evt.metaKey));

    if (isMultiModifier) {
      // Toggle en el conjunto de destinos seleccionados
      const grp = nodeGroups.get(node.id);
      const isParentShortcut = node.device_type === 'parent_map' || !!node.extra_data?.is_parent_shortcut;
      const isSubmap = node.device_type === 'submap' || isParentShortcut;

      if (linkTargetNodes.has(node)) {
        linkTargetNodes.delete(node);
        if (grp) {
          const curPingStatus = node.ping_status || node.status;
          grp.findOne('.box').stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
          grp.findOne('.box').strokeWidth(isSubmap ? 2 : 1.5);
        }
      } else {
        linkTargetNodes.add(node);
        if (grp) {
          grp.findOne('.box').stroke('#38bdf8');
          grp.findOne('.box').strokeWidth(2.5);
        }
      }
      nodesLayer.batchDraw();

      const btnMulti = document.getElementById('btn-confirm-multi-link');
      const countSpan = document.getElementById('multi-link-count');
      const bannerText = document.getElementById('link-mode-text');

      if (linkTargetNodes.size > 0) {
        if (btnMulti) btnMulti.style.display = 'inline-flex';
        if (countSpan) countSpan.textContent = linkTargetNodes.size;
        if (bannerText) {
          bannerText.textContent = `Origen: "${linkSourceNode.name}" ➔ ${linkTargetNodes.size} destino(s) seleccionado(s). Pulsa Conectar o Enter.`;
        }
      } else {
        if (btnMulti) btnMulti.style.display = 'none';
        if (bannerText) {
          bannerText.textContent = `Paso 2: Haz clic en el destino o arrastra con Clic Derecho para seleccionar múltiples nodos (desde "${linkSourceNode.name}")`;
        }
      }
      return;
    }

    // Si ya teníamos nodos en linkTargetNodes acumulados y se hace clic normal, conectar todos incluyendo este
    if (linkTargetNodes.size > 0) {
      linkTargetNodes.add(node);
      await connectMultipleTargetNodes(linkSourceNode, Array.from(linkTargetNodes));
      return;
    }

    // Clic simple en un solo nodo destino
    await connectSingleTargetNode(linkSourceNode, node);
  }
}

async function connectMultipleTargetNodes(sourceNode, targetNodes) {
  if (!sourceNode || !targetNodes || targetNodes.length === 0) return;

  const validTargets = targetNodes.filter(t => t.id !== sourceNode.id);
  if (validTargets.length === 0) return;

  let createdCount = 0;
  const dict = new Map();
  if (currentMap && currentMap.nodes) {
    currentMap.nodes.forEach(n => dict.set(n.id, n));
  }

  for (const tgtNode of validTargets) {
    // Evitar enlaces duplicados entre el mismo par de nodos
    const alreadyLinked = currentMap.links && currentMap.links.some(l => 
      (l.source_node_id === sourceNode.id && l.target_node_id === tgtNode.id) ||
      (l.source_node_id === tgtNode.id && l.target_node_id === sourceNode.id)
    );
    if (alreadyLinked) continue;

    const isSourceNav = sourceNode.device_type === 'submap' || sourceNode.device_type === 'parent_map' || !!sourceNode.extra_data?.is_parent_shortcut;
    const isTargetNav = tgtNode.device_type === 'submap' || tgtNode.device_type === 'parent_map' || !!tgtNode.extra_data?.is_parent_shortcut;
    const isSourceNote = sourceNode.device_type === 'note';
    const isTargetNote = tgtNode.device_type === 'note';

    let linkExtra = {
      direction: 'source_to_target'
    };

    if (isSourceNav || isTargetNav) {
      // Para conexiones múltiples hacia submapas se aplica enlace visual directo
      linkExtra.is_visual_only = true;
      linkExtra.is_simple_link = true;
      linkExtra.sync_zabbix = false;
    }

    if (isSourceNote || isTargetNote) {
      // Las notas actúan como puente visual; el enlace no se sincroniza a Zabbix/BSM
      linkExtra.is_visual_only = true;
      linkExtra.is_note_bridge = true;
      linkExtra.sync_zabbix = false;
    }

    try {
      const newLink = await API.createLink({
        map_id: currentMap.id,
        source_node_id: sourceNode.id,
        target_node_id: tgtNode.id,
        status: 'ok',
        extra_data: linkExtra
      });

      if (!currentMap.links) currentMap.links = [];
      currentMap.links.push(newLink);
      renderLink(newLink, dict);
      createdCount++;
    } catch (err) {
      console.warn(`Error al conectar ${sourceNode.name} con ${tgtNode.name}:`, err);
    }
  }

  if (linksLayer) linksLayer.batchDraw();
  cancelLinkMode();
}

function promptPortConnectModal(sourceNode, targetNode) {
  return new Promise((resolve) => {
    const modal = document.getElementById('modal-port-connect');
    if (!modal) {
      resolve({ confirmed: true, source_interface: '', target_interface: '', cable_type: 'cat6', direction: 'source_to_target' });
      return;
    }

    const srcName = document.getElementById('port-connect-src-name');
    const srcModel = document.getElementById('port-connect-src-model');
    const tgtName = document.getElementById('port-connect-tgt-name');
    const tgtModel = document.getElementById('port-connect-tgt-model');
    const selectSrc = document.getElementById('port-connect-select-src');
    const selectTgt = document.getElementById('port-connect-select-tgt');
    const inputSrc = document.getElementById('port-connect-input-src');
    const inputTgt = document.getElementById('port-connect-input-tgt');
    const chipsSrc = document.getElementById('port-connect-src-chips');
    const chipsTgt = document.getElementById('port-connect-tgt-chips');
    const badgeSrc = document.getElementById('port-connect-src-badge');
    const badgeTgt = document.getElementById('port-connect-tgt-badge');
    const selectCable = document.getElementById('port-connect-cable-type');
    const selectDir = document.getElementById('port-connect-direction');

    const btnConfirm = document.getElementById('btn-confirm-port-connect');
    const btnQuick = document.getElementById('btn-port-connect-quick');
    const btnCancel = document.getElementById('btn-cancel-port-connect');
    const btnClose = document.getElementById('btn-close-port-connect');

    const sName = sourceNode.name || 'Nodo A';
    const tName = targetNode.name || 'Nodo B';
    if (srcName) srcName.textContent = sName;
    if (srcModel) srcModel.textContent = `${sourceNode.extra_data?.model || sourceNode.device_type || 'Dispositivo'} (${sourceNode.ip || ''})`;
    if (tgtName) tgtName.textContent = tName;
    if (tgtModel) tgtModel.textContent = `${targetNode.extra_data?.model || targetNode.device_type || 'Dispositivo'} (${targetNode.ip || ''})`;

    if (inputSrc) { inputSrc.value = ''; inputSrc.style.display = 'none'; }
    if (inputTgt) { inputTgt.value = ''; inputTgt.style.display = 'none'; }
    if (selectCable) selectCable.value = 'cat6';
    if (selectDir) selectDir.value = 'source_to_target';

    let chosenSrcSubmapDev = null;
    let chosenTgtSubmapDev = null;
    let srcTargetMapId = null;
    let tgtTargetMapId = null;

    const setupSidePortControls = async (node, isSource, subContainerId, subSelectId, selectEl, inputEl, chipsEl, badgeEl) => {
      if (!selectEl) return;
      const isSubmap = node.device_type === 'submap' || node.device_type === 'parent_map' || !!node.extra_data?.is_parent_shortcut;
      const subContainer = document.getElementById(subContainerId);
      const subSelect = document.getElementById(subSelectId);

      const renderDevicePorts = async (devNode) => {
        selectEl.innerHTML = '<option value="">⏳ Cargando puertos...</option>';
        if (chipsEl) chipsEl.innerHTML = '';
        if (badgeEl) badgeEl.textContent = 'Cargando...';

        const devId = devNode ? (devNode.device_id || devNode.extra_data?.device_id || devNode.extra_data?.netbox_id) : null;
        let ifaces = [];
        if (devId) {
          try {
            ifaces = await API.getDeviceInterfaces(devId);
          } catch (e) {
            console.warn('Error fetching ifaces:', e);
          }
        }

        selectEl.innerHTML = '';
        const defOpt = document.createElement('option');
        defOpt.value = '';
        defOpt.textContent = '-- Seleccionar Puerto --';
        selectEl.appendChild(defOpt);

        if (badgeEl) badgeEl.textContent = `${ifaces.length} puertos (${devNode.name})`;

        if (Array.isArray(ifaces) && ifaces.length > 0) {
          ifaces.forEach((iface, idx) => {
            const opt = document.createElement('option');
            opt.value = iface.name;
            opt.dataset.ifaceId = iface.id;
            const speedStr = iface.type ? ` (${iface.type})` : '';
            const connStr = iface.is_connected ? ' [Ocupado]' : '';
            opt.textContent = `${iface.name}${speedStr}${connStr}`;
            if (idx === 0 && !iface.is_connected) {
              opt.selected = true;
            }
            selectEl.appendChild(opt);

            if (chipsEl) {
              const chip = document.createElement('button');
              chip.type = 'button';
              chip.className = 'badge';
              const isFiber = (iface.type || '').includes('sfp');
              const isConn = iface.is_connected;
              chip.style.cssText = `font-size: 0.68rem; padding: 3px 6px; cursor: pointer; border-radius: 4px; border: 1px solid ${isConn ? 'rgba(239,68,68,0.4)' : (isFiber ? 'rgba(168,85,247,0.4)' : 'rgba(56,189,248,0.4)')}; background: ${isConn ? 'rgba(239,68,68,0.1)' : (isFiber ? 'rgba(168,85,247,0.1)' : 'rgba(56,189,248,0.1)')}; color: ${isConn ? '#f87171' : (isFiber ? '#c084fc' : '#38bdf8')};`;
              chip.textContent = `${isFiber ? '⚡ ' : '🔌 '}${iface.name}${isConn ? ' ●' : ''}`;
              chip.title = `${iface.name} (${iface.type || 'Port'})${isConn ? ' - Conectado' : ' - Disponible'}`;
              chip.onclick = () => {
                selectEl.value = iface.name;
                if (inputEl) inputEl.style.display = 'none';
                Array.from(chipsEl.children).forEach(c => c.style.outline = 'none');
                chip.style.outline = '2px solid #38bdf8';
              };
              chipsEl.appendChild(chip);
            }
          });
        }

        const manualOpt = document.createElement('option');
        manualOpt.value = '__manual__';
        manualOpt.textContent = '✏️ Puerto Personalizado...';
        selectEl.appendChild(manualOpt);

        selectEl.onchange = () => {
          if (selectEl.value === '__manual__') {
            if (inputEl) {
              inputEl.style.display = 'block';
              inputEl.focus();
            }
          } else {
            if (inputEl) {
              inputEl.style.display = 'none';
              inputEl.value = selectEl.value;
            }
          }
        };
      };

      if (isSubmap) {
        if (subContainer) subContainer.style.display = 'block';
        let tMapId = node.extra_data?.target_map_id;
        if (!tMapId) {
          if (node.device_type === 'parent_map' || node.extra_data?.is_parent_shortcut) {
            tMapId = currentMap.parent_map_id;
          } else if (Array.isArray(allMaps)) {
            const clean = (node.name || '').replace('📁', '').trim().toLowerCase();
            const matched = allMaps.find(m => m.name.toLowerCase().trim() === clean || m.id === clean);
            if (matched) tMapId = matched.id;
          }
        }

        if (isSource) srcTargetMapId = tMapId; else tgtTargetMapId = tMapId;

        let submapNodes = [];
        if (tMapId) {
          try {
            const mDetail = await API.getMapDetail(tMapId);
            if (mDetail && Array.isArray(mDetail.nodes)) {
              submapNodes = mDetail.nodes.filter(n => n.device_type !== 'submap' && n.device_type !== 'parent_map' && !n.extra_data?.is_parent_shortcut);
            }
          } catch (e) {
            console.warn('Error cargando equipos de submapa:', e);
          }
        }

        if (subSelect) {
          subSelect.innerHTML = '';
          if (submapNodes.length === 0) {
            subSelect.innerHTML = '<option value="">(Sin equipos en este submapa)</option>';
            if (badgeEl) badgeEl.textContent = '0 puertos';
          } else {
            const defOpt = document.createElement('option');
            defOpt.value = '';
            defOpt.textContent = `-- Seleccionar Equipo (${submapNodes.length}) --`;
            subSelect.appendChild(defOpt);

            submapNodes.forEach((sn, idx) => {
              const opt = document.createElement('option');
              opt.value = sn.id;
              opt.dataset.deviceId = sn.device_id || '';
              opt.textContent = `🖥️ ${sn.name} [${sn.ip || 'Sin IP'}] (${sn.extra_data?.model || sn.device_type || 'Dispositivo'})`;
              if (idx === 0) opt.selected = true;
              subSelect.appendChild(opt);
            });

            const initialDev = submapNodes[0];
            if (isSource) chosenSrcSubmapDev = initialDev; else chosenTgtSubmapDev = initialDev;
            renderDevicePorts(initialDev);

            subSelect.onchange = () => {
              const selectedDev = submapNodes.find(n => n.id === subSelect.value);
              if (selectedDev) {
                if (isSource) chosenSrcSubmapDev = selectedDev; else chosenTgtSubmapDev = selectedDev;
                renderDevicePorts(selectedDev);
              }
            };
          }
        }
      } else {
        if (subContainer) subContainer.style.display = 'none';
        renderDevicePorts(node);
      }
    };

    setupSidePortControls(sourceNode, true, 'port-connect-src-submap-container', 'port-connect-src-submap-dev', selectSrc, inputSrc, chipsSrc, badgeSrc);
    setupSidePortControls(targetNode, false, 'port-connect-tgt-submap-container', 'port-connect-tgt-submap-dev', selectTgt, inputTgt, chipsTgt, badgeTgt);

    modal.style.display = 'flex';

    const cleanUp = () => {
      modal.style.display = 'none';
      btnConfirm.onclick = null;
      btnQuick.onclick = null;
      btnCancel.onclick = null;
      btnClose.onclick = null;
    };

    btnConfirm.onclick = () => {
      let srcIface = selectSrc && selectSrc.value === '__manual__' ? (inputSrc ? inputSrc.value.trim() : '') : (selectSrc ? selectSrc.value : '');
      let tgtIface = selectTgt && selectTgt.value === '__manual__' ? (inputTgt ? inputTgt.value.trim() : '') : (selectTgt ? selectTgt.value : '');
      const cableType = selectCable ? selectCable.value : 'cat6';
      const direction = selectDir ? selectDir.value : 'source_to_target';
      cleanUp();
      resolve({
        confirmed: true,
        source_interface: srcIface,
        target_interface: tgtIface,
        cable_type: cableType,
        direction,
        src_submap_dev: chosenSrcSubmapDev,
        tgt_submap_dev: chosenTgtSubmapDev,
        src_target_map_id: srcTargetMapId,
        tgt_target_map_id: tgtTargetMapId
      });
    };

    btnQuick.onclick = () => {
      cleanUp();
      resolve({ confirmed: true, source_interface: '', target_interface: '', cable_type: 'cat6', direction: 'source_to_target', is_quick: true });
    };

    btnCancel.onclick = () => {
      cleanUp();
      resolve({ confirmed: false });
    };

    btnClose.onclick = () => {
      cleanUp();
      resolve({ confirmed: false });
    };
  });
}

async function connectSingleTargetNode(sourceNode, node) {
  const isSourceNav = sourceNode.device_type === 'submap' || sourceNode.device_type === 'parent_map' || !!sourceNode.extra_data?.is_parent_shortcut;
  const isTargetNav = node.device_type === 'submap' || node.device_type === 'parent_map' || !!node.extra_data?.is_parent_shortcut;
  const isSourceNote = sourceNode.device_type === 'note';
  const isTargetNote = node.device_type === 'note';

  let linkExtra = {};
  let srcIface = '';
  let tgtIface = '';
  let cableType = 'cat6';

  const isSourceFtth = sourceNode.device_type === 'ftth_branch';
  const isTargetFtth = node.device_type === 'ftth_branch';

  // Si alguno de los nodos es un brazo FTTH / Ramal GPON, crear el enlace de fibra interactivo directo
  if (isSourceFtth || isTargetFtth) {
    const ftthNode = isTargetFtth ? node : sourceNode;
    const otherNode = isTargetFtth ? sourceNode : node;
    const gponPort = ftthNode.extra_data?.gpon_port || 'GPON';

    linkExtra = {
      direction: isTargetFtth ? 'source_to_target' : 'target_to_source',
      is_gpon_branch: true,
      cable_type: 'fiber',
      gpon_port: gponPort,
      gpon_index: ftthNode.extra_data?.gpon_index || '',
      olt_ip: ftthNode.extra_data?.olt_ip || '',
      olt_name: ftthNode.extra_data?.olt_name || ''
    };
    srcIface = isTargetFtth ? gponPort : '';
    tgtIface = isTargetFtth ? '' : gponPort;
    cableType = 'fiber';

    try {
      const alreadyLinked = currentMap.links && currentMap.links.some(l =>
        (l.source_node_id === sourceNode.id && l.target_node_id === node.id) ||
        (l.source_node_id === node.id && l.target_node_id === sourceNode.id)
      );
      if (!alreadyLinked) {
        const newLink = await API.createLink({
          map_id: currentMap.id,
          source_node_id: sourceNode.id,
          target_node_id: node.id,
          source_interface: srcIface,
          target_interface: tgtIface,
          cable_type: cableType,
          status: 'ok',
          extra_data: linkExtra
        });
        if (!currentMap.links) currentMap.links = [];
        currentMap.links.push(newLink);
        const dict = new Map();
        if (currentMap.nodes) currentMap.nodes.forEach(n => dict.set(n.id, n));
        renderLink(newLink, dict);
        if (linksLayer) linksLayer.batchDraw();
      }
    } catch(err) {
      alert('Error creando enlace con brazo FTTH: ' + err.message);
    } finally {
      cancelLinkMode();
    }
    return;
  }

  // Si alguno de los nodos es una nota, crear el enlace visual directamente sin abrir el modal de puertos
  if (isSourceNote || isTargetNote) {
    linkExtra = {
      direction: 'source_to_target',
      is_visual_only: true,
      is_note_bridge: true,
      sync_zabbix: false
    };
    try {
      const alreadyLinked = currentMap.links && currentMap.links.some(l =>
        (l.source_node_id === sourceNode.id && l.target_node_id === node.id) ||
        (l.source_node_id === node.id && l.target_node_id === sourceNode.id)
      );
      if (!alreadyLinked) {
        const newLink = await API.createLink({
          map_id: currentMap.id,
          source_node_id: sourceNode.id,
          target_node_id: node.id,
          status: 'ok',
          extra_data: linkExtra
        });
        if (!currentMap.links) currentMap.links = [];
        currentMap.links.push(newLink);
        const dict = new Map();
        if (currentMap.nodes) currentMap.nodes.forEach(n => dict.set(n.id, n));
        renderLink(newLink, dict);
        if (linksLayer) linksLayer.batchDraw();
      }
    } catch(err) {
      alert('Error creando enlace con nota: ' + err.message);
    } finally {
      cancelLinkMode();
    }
    return;
  }

  // Solicitar selección interactiva de puertos físicos y resolución de submapas
  const portRes = await promptPortConnectModal(sourceNode, node);
  if (!portRes || !portRes.confirmed) {
    cancelLinkMode();
    return;
  }

  srcIface = portRes.source_interface || '';
  tgtIface = portRes.target_interface || '';
  cableType = portRes.cable_type || 'cat6';
  linkExtra.direction = portRes.direction || 'source_to_target';

  // Si es un enlace inter-mapa (hacia o desde un submapa / mapa padre)
  if (isSourceNav || isTargetNav) {
    const navNode = isTargetNav ? node : sourceNode;
    const deviceNode = isTargetNav ? sourceNode : node;
    const chosenRemoteDev = isTargetNav ? portRes.tgt_submap_dev : portRes.src_submap_dev;
    const targetMapId = isTargetNav ? portRes.tgt_target_map_id : portRes.src_target_map_id;

    if (targetMapId && chosenRemoteDev) {
      const pinId = 'pin-' + Date.now().toString(36);
      const isParentNav = navNode.device_type === 'parent_map' || !!navNode.extra_data?.is_parent_shortcut;

      if (!navNode.extra_data) navNode.extra_data = {};
      if (!navNode.extra_data.pins) navNode.extra_data.pins = [];
      navNode.extra_data.pins.push({
        pin_id: pinId,
        remote_node_id: chosenRemoteDev.id,
        remote_node_name: chosenRemoteDev.name,
        remote_map_id: targetMapId,
        label: isParentNav ? `⬅ ${chosenRemoteDev.name}` : `➔ ${chosenRemoteDev.name}`
      });
      await API.updateNode(navNode.id, { extra_data: navNode.extra_data });

      try {
        const targetMapDetail = await API.getMapDetail(targetMapId);
        if (targetMapDetail && targetMapDetail.nodes) {
          const complementaryNav = targetMapDetail.nodes.find(n => (isParentNav ? n.device_type === 'submap' : (n.device_type === 'parent_map' || n.extra_data?.is_parent_shortcut)));
          if (complementaryNav) {
            if (!complementaryNav.extra_data) complementaryNav.extra_data = {};
            if (!complementaryNav.extra_data.pins) complementaryNav.extra_data.pins = [];
            complementaryNav.extra_data.pins.push({
              pin_id: pinId,
              remote_node_id: deviceNode.id,
              remote_node_name: deviceNode.name,
              remote_map_id: currentMap.id,
              label: isParentNav ? `➔ ${deviceNode.name}` : `⬅ ${deviceNode.name}`
            });
            await API.updateNode(complementaryNav.id, { extra_data: complementaryNav.extra_data });

            await API.createLink({
              map_id: targetMapId,
              source_node_id: isParentNav ? chosenRemoteDev.id : complementaryNav.id,
              target_node_id: isParentNav ? complementaryNav.id : chosenRemoteDev.id,
              source_interface: isParentNav ? tgtIface : srcIface,
              target_interface: isParentNav ? srcIface : tgtIface,
              cable_type: cableType,
              status: 'ok',
              extra_data: {
                is_intermap: true,
                pin_id: pinId,
                local_node_id: chosenRemoteDev.id,
                local_node_name: chosenRemoteDev.name,
                remote_node_id: deviceNode.id,
                remote_node_name: deviceNode.name,
                remote_map_id: currentMap.id,
                target_submap_device_id: deviceNode.device_id,
                target_submap_device_name: deviceNode.name
              }
            });
          }
        }
      } catch (cErr) {
        console.warn('Error sincronizando nodo complementario en submapa:', cErr);
      }

      linkExtra = {
        is_intermap: true,
        pin_id: pinId,
        local_node_id: deviceNode.id,
        local_node_name: deviceNode.name,
        remote_node_id: chosenRemoteDev.id,
        remote_node_name: chosenRemoteDev.name,
        remote_map_id: targetMapId,
        target_submap_device_id: chosenRemoteDev.device_id,
        target_submap_device_name: chosenRemoteDev.name,
        direction: portRes.direction || 'source_to_target'
      };

      const grpNav = nodeGroups.get(navNode.id);
      if (grpNav) grpNav.destroy();
      renderNode(navNode);
      nodesLayer.batchDraw();
    }
  }

  try {
    if (!linkExtra.direction) {
      linkExtra.direction = 'source_to_target';
    }

    const newLink = await API.createLink({
      map_id: currentMap.id,
      source_node_id: sourceNode.id,
      target_node_id: node.id,
      source_interface: srcIface,
      target_interface: tgtIface,
      cable_type: cableType,
      status: 'ok',
      extra_data: linkExtra
    });

    if (!currentMap.links) currentMap.links = [];
    currentMap.links.push(newLink);
    const dict = new Map();
    if (currentMap.nodes) currentMap.nodes.forEach(n => dict.set(n.id, n));
    renderLink(newLink, dict);

    linksLayer.batchDraw();

  } catch (err) {
    alert('Error creando enlace: ' + err.message);
  } finally {
    cancelLinkMode();
  }
}

// ─── 6. Inspector de Propiedades del Nodo ───────────────────────────────────

let _realtimePollInterval = null;

// ─── Helper: Panel de Propiedades para Nodo Nota ────────────────────────────
function _selectNoteNode(node) {
  const extra = node.extra_data || {};
  const colorKey = extra.note_color || 'yellow';
  const theme = NOTE_COLORS[colorKey] || NOTE_COLORS['yellow'];

  const titleEl = document.getElementById('prop-node-title');
  if (titleEl) titleEl.textContent = '📝 Nota';

  const subtitleEl = document.getElementById('prop-node-subtitle');
  if (subtitleEl) subtitleEl.textContent = 'Anotación visual del lienzo';

  const badgeEl = document.getElementById('prop-node-type-badge');
  if (badgeEl) {
    badgeEl.textContent = 'Nota';
    badgeEl.style.backgroundColor = theme.bg;
    badgeEl.style.borderColor    = theme.border;
    badgeEl.style.color          = theme.border;
  }

  // Ocultar campos que no aplican a notas
  const ipRow = document.getElementById('prop-node-ip-link');
  if (ipRow) ipRow.closest('.device-info-row') && (ipRow.closest('.device-info-row').style.display = 'none');
  const netboxBtn = document.getElementById('btn-open-netbox');
  if (netboxBtn) netboxBtn.style.display = 'none';
  const zabbixBtn = document.getElementById('btn-open-zabbix');
  if (zabbixBtn) zabbixBtn.style.display = 'none';
  const webAdminBtn = document.getElementById('btn-open-device-web');
  if (webAdminBtn) webAdminBtn.style.display = 'none';
  const telemetryPanel = document.getElementById('telemetry-panel');
  if (telemetryPanel) telemetryPanel.style.display = 'none';
  const portsCard = document.getElementById('node-ports-card');
  if (portsCard) portsCard.style.display = 'none';
  const convertBox = document.getElementById('convert-submap-action-box');
  if (convertBox) convertBox.style.display = 'none';
  const submapBox = document.getElementById('submap-action-box');
  if (submapBox) submapBox.style.display = 'none';

  // Mostrar el panel de nota en el sidebar
  let notePanel = document.getElementById('note-properties-panel');
  if (!notePanel) {
    notePanel = document.createElement('div');
    notePanel.id = 'note-properties-panel';
    notePanel.style.cssText = 'margin-top:8px;';
    notePanel.innerHTML = `
      <div style="background:rgba(15,23,42,0.6);border:1px solid var(--border-color);border-radius:8px;padding:10px;margin-bottom:6px;">
        <div style="font-size:0.72rem;font-weight:700;color:#f8fafc;margin-bottom:6px;display:flex;align-items:center;gap:5px;">
          <i class="fas fa-sticky-note"></i> Contenido de la Nota
        </div>
        <div id="note-preview-text" style="font-size:0.82rem;color:#e2e8f0;white-space:pre-wrap;word-break:break-word;min-height:36px;max-height:120px;overflow-y:auto;line-height:1.4;"></div>
        <div style="margin-top:8px;display:flex;gap:6px;">
          <button id="btn-edit-note-inline" class="btn btn-primary" style="flex:1;justify-content:center;font-size:0.76rem;padding:6px;">
            <i class="fas fa-edit"></i> Editar Nota
          </button>
        </div>
      </div>`;
    const propertiesPanel = document.getElementById('node-properties-panel');
    if (propertiesPanel) propertiesPanel.appendChild(notePanel);
  }

  notePanel.style.display = 'block';
  const previewEl = document.getElementById('note-preview-text');
  if (previewEl) previewEl.textContent = extra.note_text || node.name || '';

  const btnEdit = document.getElementById('btn-edit-note-inline');
  if (btnEdit) {
    btnEdit.onclick = () => openNoteEditorModal(node);
  }

  document.getElementById('prop-node-coords').textContent = `X: ${Math.round(node.x)}, Y: ${Math.round(node.y)}`;
  document.getElementById('prop-node-site').textContent = 'N/A';
  document.getElementById('prop-node-role').textContent = 'Nota de lienzo';
  document.getElementById('prop-node-mfr').textContent = '—';
  document.getElementById('prop-node-model').textContent = '—';
  document.getElementById('prop-node-serial').textContent = '—';
  const statusEl = document.getElementById('prop-node-status');
  if (statusEl) { statusEl.textContent = 'Visual'; statusEl.style.color = theme.border; }
}

// ─── Editor de Nota (Modal) ──────────────────────────────────────────────────
function openNoteEditorModal(node) {
  const modal = document.getElementById('modal-note-editor');
  if (!modal) return;
  const extra = node.extra_data || {};
  const textarea = document.getElementById('note-editor-textarea');
  const colorSelect = document.getElementById('note-editor-color');
  if (textarea) textarea.value = extra.note_text || node.name || '';
  if (colorSelect) colorSelect.value = extra.note_color || 'yellow';

  modal.style.display = 'flex';

  const btnSave = document.getElementById('btn-save-note');
  const btnCancel = document.getElementById('btn-cancel-note');
  const btnDelete = document.getElementById('btn-delete-note');
  const btnClose = document.getElementById('btn-close-note-modal');

  const closeModal = () => { modal.style.display = 'none'; };

  if (btnClose) btnClose.onclick = closeModal;
  if (btnCancel) btnCancel.onclick = closeModal;

  if (btnSave) {
    btnSave.onclick = async () => {
      const newText = textarea ? textarea.value : '';
      const newColor = colorSelect ? colorSelect.value : 'yellow';
      const updatedExtra = Object.assign({}, extra, {
        note_text: newText,
        note_color: newColor
      });
      try {
        await API.updateNode(node.id, { name: newText.split('\n')[0].slice(0, 80) || 'Nota', extra_data: updatedExtra });
        node.name = newText.split('\n')[0].slice(0, 80) || 'Nota';
        node.extra_data = updatedExtra;

        // Re-renderizar el nodo nota en el canvas
        const grp = nodeGroups.get(node.id);
        if (grp) grp.destroy();
        nodeGroups.delete(node.id);
        _renderNoteNode(node);
        nodesLayer.batchDraw();

        // Actualizar preview en sidebar
        const previewEl = document.getElementById('note-preview-text');
        if (previewEl) previewEl.textContent = newText;

        closeModal();
      } catch(err) {
        alert('Error guardando nota: ' + (err.message || err));
      }
    };
  }

  if (btnDelete) {
    btnDelete.onclick = async () => {
      if (!confirm('¿Eliminar esta nota del lienzo?')) return;
      try {
        await API.deleteNode(node.id);
        const grp = nodeGroups.get(node.id);
        if (grp) grp.destroy();
        nodeGroups.delete(node.id);
        if (currentMap && currentMap.nodes) {
          currentMap.nodes = currentMap.nodes.filter(n => n.id !== node.id);
        }
        nodesLayer.batchDraw();
        deselectNode();
        closeModal();
      } catch(err) {
        alert('Error eliminando nota: ' + (err.message || err));
      }
    };
  }
}

// ─── Helper: Panel de Propiedades para Brazo FTTH / Ramal GPON ─────────────
function _selectFtthBranchNode(node) {
  const extra = node.extra_data || {};
  const branchName = extra.branch_name || node.name || 'Brazo FTTH';
  const gponPort = extra.gpon_port || 'GPON';

  const titleEl = document.getElementById('prop-node-title');
  if (titleEl) titleEl.textContent = `⚡ ${branchName}`;

  const subtitleEl = document.getElementById('prop-node-subtitle');
  if (subtitleEl) subtitleEl.textContent = `Puerto: ${gponPort} · OLT: ${extra.olt_name || extra.olt_ip || 'Huizache'}`;

  const badgeEl = document.getElementById('prop-node-type-badge');
  if (badgeEl) {
    badgeEl.textContent = 'Brazo FTTH';
    badgeEl.style.backgroundColor = 'rgba(14, 165, 233, 0.15)';
    badgeEl.style.borderColor = '#0284c7';
    badgeEl.style.color = '#38bdf8';
  }

  // Ocultar campos que no aplican
  const ipRow = document.getElementById('prop-node-ip-link');
  if (ipRow) ipRow.closest('.device-info-row') && (ipRow.closest('.device-info-row').style.display = 'none');
  const netboxBtn = document.getElementById('btn-open-netbox');
  if (netboxBtn) netboxBtn.style.display = 'none';
  const zabbixBtn = document.getElementById('btn-open-zabbix');
  if (zabbixBtn) zabbixBtn.style.display = 'none';
  const webAdminBtn = document.getElementById('btn-open-device-web');
  if (webAdminBtn) webAdminBtn.style.display = 'none';
  const telemetryPanel = document.getElementById('telemetry-panel');
  if (telemetryPanel) telemetryPanel.style.display = 'none';
  const portsCard = document.getElementById('node-ports-card');
  if (portsCard) portsCard.style.display = 'none';
  const convertBox = document.getElementById('convert-submap-action-box');
  if (convertBox) convertBox.style.display = 'none';
  const submapBox = document.getElementById('submap-action-box');
  if (submapBox) submapBox.style.display = 'none';
  const notePanel = document.getElementById('note-properties-panel');
  if (notePanel) notePanel.style.display = 'none';

  // Mostrar el panel de GPON en el sidebar
  let gponPanel = document.getElementById('gpon-properties-panel');
  if (!gponPanel) {
    gponPanel = document.createElement('div');
    gponPanel.id = 'gpon-properties-panel';
    gponPanel.style.cssText = 'margin-top:8px;';
    gponPanel.innerHTML = `
      <div style="background:rgba(15,23,42,0.65);border:1px solid var(--border-color);border-radius:8px;padding:10px;margin-bottom:6px;">
        
        <!-- Estado del Puerto GPON -->
        <div id="gpon-prop-status-box" style="display:flex;align-items:center;justify-content:space-between;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);border-radius:6px;padding:8px 10px;margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:7px;">
            <span id="gpon-prop-status-dot" style="width:9px;height:9px;border-radius:50%;background:#10b981;display:inline-block;"></span>
            <strong id="gpon-prop-status-label" style="font-size:0.8rem;color:#10b981;">Puerto Link Up (Operativo)</strong>
          </div>
          <button id="btn-refresh-gpon-prop" title="Actualizar telemetría de ONUs" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:0.75rem;">
            <i class="fas fa-sync-alt"></i>
          </button>
        </div>

        <!-- Tráfico & Volumen -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:8px;font-size:0.74rem;">
          <div style="background:var(--bg-card);border-radius:5px;padding:5px 6px;">
            <span style="color:var(--text-muted);font-size:0.62rem;display:block;">⬇ Bajada:</span>
            <strong id="gpon-prop-traffic-in" style="color:#10b981;font-family:monospace;">—</strong>
          </div>
          <div style="background:var(--bg-card);border-radius:5px;padding:5px 6px;">
            <span style="color:var(--text-muted);font-size:0.62rem;display:block;">⬆ Subida:</span>
            <strong id="gpon-prop-traffic-out" style="color:#38bdf8;font-family:monospace;">—</strong>
          </div>
          <div style="background:var(--bg-card);border-radius:5px;padding:5px 6px;">
            <span style="color:var(--text-muted);font-size:0.62rem;display:block;">Consumo Volumen:</span>
            <strong id="gpon-prop-volume" style="color:#f8fafc;font-family:monospace;">—</strong>
          </div>
          <div style="background:var(--bg-card);border-radius:5px;padding:5px 6px;">
            <span style="color:var(--text-muted);font-size:0.62rem;display:block;">Potencia TX SFP:</span>
            <strong id="gpon-prop-txpower" style="color:#c084fc;font-family:monospace;">—</strong>
          </div>
        </div>

        <!-- Niveles de Señal Óptica (Típicos vs Atípicos) -->
        <div style="font-size:0.72rem;font-weight:700;color:#38bdf8;margin:8px 0 5px 0;display:flex;align-items:center;justify-content:space-between;">
          <span><i class="fas fa-satellite-dish"></i> Clientes Ópticos (ONUs)</span>
          <span id="gpon-prop-total-onus" style="font-size:0.65rem;color:#cbd5e1;background:rgba(255,255,255,0.08);padding:1px 5px;border-radius:4px;">0 ONUs</span>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">
          <!-- Card Típicos -->
          <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.3);border-radius:6px;padding:6px;text-align:center;">
            <div style="font-size:0.62rem;color:#6ee7b7;font-weight:600;margin-bottom:2px;">CLIENTES TÍPICOS (> -27 dBm)</div>
            <div id="gpon-prop-typical-count" style="font-size:1.1rem;font-weight:800;color:#10b981;">0</div>
            <div style="font-size:0.65rem;color:#cbd5e1;margin-top:2px;">Prom: <strong id="gpon-prop-typical-avg" style="color:#10b981;">—</strong></div>
          </div>
          <!-- Card Atípicos -->
          <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:6px;padding:6px;text-align:center;">
            <div style="font-size:0.62rem;color:#fca5a5;font-weight:600;margin-bottom:2px;">CLIENTES ATÍPICOS (≤ -27 dBm)</div>
            <div id="gpon-prop-atypical-count" style="font-size:1.1rem;font-weight:800;color:#ef4444;">0</div>
            <div style="font-size:0.65rem;color:#cbd5e1;margin-top:2px;">Prom: <strong id="gpon-prop-atypical-avg" style="color:#ef4444;">—</strong></div>
          </div>
        </div>

        <!-- Muestra de ONUs -->
        <div id="gpon-prop-onus-container" style="max-height:100px;overflow-y:auto;background:rgba(2,6,23,0.5);border:1px solid var(--border-color);border-radius:5px;padding:4px;font-size:0.65rem;font-family:monospace;display:none;flex-direction:column;gap:3px;margin-bottom:8px;">
        </div>

        <button id="btn-edit-gpon-branch" class="btn btn-primary" style="width:100%;justify-content:center;font-size:0.76rem;padding:7px;">
          <i class="fas fa-edit"></i> Configurar Brazo FTTH
        </button>
        <button id="btn-open-diag-from-branch" class="btn" style="width:100%;justify-content:center;font-size:0.76rem;padding:7px;margin-top:6px;background:rgba(14,165,233,0.15);border-color:#0284c7;color:#38bdf8;">
          <i class="fas fa-network-wired"></i> Abrir Diagnóstico OLT
        </button>
      </div>`;
    const propertiesPanel = document.getElementById('node-properties-panel');
    if (propertiesPanel) propertiesPanel.appendChild(gponPanel);
  }

  gponPanel.style.display = 'block';

  const updatePropData = (telem) => {
    if (!telem) return;
    const isDown = telem.port_status === 'down' || telem.port_status_code === 2;
    const statusBox = document.getElementById('gpon-prop-status-box');
    const statusDot = document.getElementById('gpon-prop-status-dot');
    const statusLbl = document.getElementById('gpon-prop-status-label');

    if (statusBox && statusDot && statusLbl) {
      if (isDown) {
        statusBox.style.background = 'rgba(239,68,68,0.15)';
        statusBox.style.borderColor = 'rgba(239,68,68,0.4)';
        statusDot.style.background = '#ef4444';
        statusLbl.style.color = '#f87171';
        statusLbl.textContent = 'Puerto Link Down (Caído / Offline)';
      } else if (telem.atypical_count > 0) {
        statusBox.style.background = 'rgba(245,158,11,0.15)';
        statusBox.style.borderColor = 'rgba(245,158,11,0.4)';
        statusDot.style.background = '#f59e0b';
        statusLbl.style.color = '#fbbf24';
        statusLbl.textContent = `Alerta Óptica (${telem.atypical_count} atípicos)`;
      } else {
        statusBox.style.background = 'rgba(16,185,129,0.12)';
        statusBox.style.borderColor = 'rgba(16,185,129,0.3)';
        statusDot.style.background = '#10b981';
        statusLbl.style.color = '#10b981';
        statusLbl.textContent = 'Puerto Link Up (Operativo)';
      }
    }

    const inEl = document.getElementById('gpon-prop-traffic-in');
    const outEl = document.getElementById('gpon-prop-traffic-out');
    const volEl = document.getElementById('gpon-prop-volume');
    const txEl = document.getElementById('gpon-prop-txpower');
    const totalOnusEl = document.getElementById('gpon-prop-total-onus');

    if (inEl) inEl.textContent = telem.traffic_in_fmt || '—';
    if (outEl) outEl.textContent = telem.traffic_out_fmt || '—';
    if (volEl) volEl.textContent = telem.volume_total_fmt || '—';
    if (txEl) txEl.textContent = telem.tx_power_dbm !== null ? `${telem.tx_power_dbm} dBm` : '—';
    if (totalOnusEl) totalOnusEl.textContent = `${telem.onus_online || 0} ONUs`;

    const typCountEl = document.getElementById('gpon-prop-typical-count');
    const typAvgEl = document.getElementById('gpon-prop-typical-avg');
    const atypCountEl = document.getElementById('gpon-prop-atypical-count');
    const atypAvgEl = document.getElementById('gpon-prop-atypical-avg');

    if (typCountEl) typCountEl.textContent = telem.typical_count || 0;
    if (typAvgEl) typAvgEl.textContent = telem.typical_avg_dbm !== null ? `${telem.typical_avg_dbm} dBm` : '—';
    if (atypCountEl) atypCountEl.textContent = telem.atypical_count || 0;
    if (atypAvgEl) atypAvgEl.textContent = telem.atypical_avg_dbm !== null ? `${telem.atypical_avg_dbm} dBm` : '—';

    // Lista de muestras de ONUs
    const onusCont = document.getElementById('gpon-prop-onus-container');
    if (onusCont) {
      if (telem.onus_sample && telem.onus_sample.length > 0) {
        onusCont.style.display = 'flex';
        onusCont.innerHTML = telem.onus_sample.map(o => {
          const col = o.is_typical ? '#10b981' : '#ef4444';
          const tag = o.is_typical ? 'TÍPICO' : 'ATÍPICO';
          return `<div style="display:flex;justify-content:space-between;padding:1px 3px;border-bottom:1px solid rgba(255,255,255,0.05);">
            <span style="color:#cbd5e1;">ONT #${o.ont_id}</span>
            <span style="color:${col};font-weight:bold;">${o.rx_power_dbm} dBm [${tag}]</span>
          </div>`;
        }).join('');
      } else {
        onusCont.style.display = 'none';
      }
    }
  };

  fetchGponBranchTelemetry(node.id).then(updatePropData);

  const btnRefresh = document.getElementById('btn-refresh-gpon-prop');
  if (btnRefresh) {
    btnRefresh.onclick = () => {
      btnRefresh.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
      fetchGponBranchTelemetry(node.id, true).then(d => {
        btnRefresh.innerHTML = '<i class="fas fa-sync-alt"></i>';
        updatePropData(d);
      });
    };
  }

  const btnEdit = document.getElementById('btn-edit-gpon-branch');
  if (btnEdit) {
    btnEdit.onclick = () => openFtthBranchEditorModal(node);
  }

  const btnOpenDiag = document.getElementById('btn-open-diag-from-branch');
  if (btnOpenDiag) {
    btnOpenDiag.onclick = () => {
      if (typeof window.openOltDiagnosticsModal === 'function') {
        window.openOltDiagnosticsModal(extra.olt_ip, extra.gpon_index);
      }
    };
  }

  document.getElementById('prop-node-coords').textContent = `X: ${Math.round(node.x)}, Y: ${Math.round(node.y)}`;
  document.getElementById('prop-node-site').textContent = node.site_name || 'Huizache';
  document.getElementById('prop-node-role').textContent = 'Brazo FTTH / Ramal GPON';
  document.getElementById('prop-node-mfr').textContent = 'Huawei / GPON';
  document.getElementById('prop-node-model').textContent = gponPort;
  document.getElementById('prop-node-serial').textContent = extra.olt_name || 'OLT_HUAWEI';
  const statusEl = document.getElementById('prop-node-status');
  if (statusEl) {
    const isDown = node.status === 'down' || node.ping_status === 'down' || extra.port_status === 'down';
    statusEl.textContent = isDown ? 'Link Down' : 'Link Up';
    statusEl.style.color = isDown ? 'var(--danger)' : 'var(--success)';
  }
}

// ─── Editor / Creador de Brazo FTTH (Modal) ──────────────────────────────────
async function openFtthBranchEditorModal(node) {
  const modal = document.getElementById('modal-ftth-branch-editor');
  if (!modal) return;
  const extra = node.extra_data || {};

  const inputName = document.getElementById('input-branch-name');
  const selectOlt = document.getElementById('select-branch-olt');
  const selectPort = document.getElementById('select-branch-gpon-port');
  const inputThreshold = document.getElementById('input-branch-threshold');
  const selectDir = document.getElementById('select-branch-arrow-dir');

  if (inputName) inputName.value = extra.branch_name || node.name || 'Brazo FTTH';
  if (inputThreshold) inputThreshold.value = extra.typical_threshold_dbm || -27.0;
  if (selectDir) selectDir.value = extra.arrow_direction || 'left';

  // Poblado de OLTs y equipos disponibles en el mapa actual
  if (selectOlt) {
    selectOlt.innerHTML = '<option value="">-- Seleccionar Equipo Origen (OLT) --</option>';
    let candidateNodes = [];
    if (currentMap && currentMap.nodes) {
      // Excluir el propio nodo del brazo FTTH y notas
      candidateNodes = currentMap.nodes.filter(n => n.id !== node.id && n.device_type !== 'note');
      // Priorizar OLTs / GPON primero, luego el resto de equipos
      candidateNodes.sort((a, b) => {
        const isOltA = (a.extra_data?.role || a.device_type || a.name || '').toLowerCase().includes('olt');
        const isOltB = (b.extra_data?.role || b.device_type || b.name || '').toLowerCase().includes('olt');
        if (isOltA && !isOltB) return -1;
        if (!isOltA && isOltB) return 1;
        return a.name.localeCompare(b.name);
      });
    }

    if (candidateNodes.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No hay equipos disponibles en este mapa';
      selectOlt.appendChild(opt);
    } else {
      candidateNodes.forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.id;
        opt.dataset.nodeId = o.id;
        opt.dataset.oltName = o.name;
        opt.dataset.oltIp = o.ip || '';
        opt.textContent = `⚡ ${o.name} (${o.ip || 'Sin IP'})`;
        if (extra.olt_node_id && o.id === extra.olt_node_id) {
          opt.selected = true;
        } else if (extra.olt_ip && o.ip && o.ip === extra.olt_ip) {
          opt.selected = true;
        } else if (extra.olt_name && o.name.toLowerCase() === extra.olt_name.toLowerCase()) {
          opt.selected = true;
        }
        selectOlt.appendChild(opt);
      });
    }

    const getSelectedOltQuery = () => {
      const opt = selectOlt.selectedOptions[0];
      if (!opt) return '10.20.0.2';
      return opt.dataset.oltIp || opt.dataset.oltName || '10.20.0.2';
    };

    // Función para cargar puertos GPON de la OLT seleccionada
    const loadGponPorts = async (oltVal) => {
      if (!selectPort) return;
      selectPort.innerHTML = '<option value="">⏳ Consultando puertos GPON de la OLT...</option>';
      try {
        const ports = await API.getOltGponPorts(oltVal);
        selectPort.innerHTML = '';
        if (ports && ports.length > 0) {
          ports.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.name;
            opt.dataset.index = p.index;
            const downStr = p.status === 'down' ? ' [Link Down]' : ' [Link Up]';
            const onusStr = p.onus_online ? ` (${p.onus_online} ONUs)` : '';
            opt.textContent = `${p.name}${downStr}${onusStr}`;
            if (extra.gpon_port && p.name.toLowerCase() === extra.gpon_port.toLowerCase()) opt.selected = true;
            else if (extra.gpon_index && String(p.index) === String(extra.gpon_index)) opt.selected = true;
            selectPort.appendChild(opt);
          });
        } else {
          // Si no retornó puertos descubiertos, agregar puertos GPON 0/1/0 a 0/1/15
          for (let i = 0; i <= 15; i++) {
            const opt = document.createElement('option');
            const pName = `GPON 0/1/${i}`;
            opt.value = pName;
            opt.textContent = pName;
            if (extra.gpon_port === pName) opt.selected = true;
            selectPort.appendChild(opt);
          }
        }
      } catch (err) {
        selectPort.innerHTML = '<option value="GPON 0/1/0">GPON 0/1/0</option>';
      }
    };

    loadGponPorts(getSelectedOltQuery());

    selectOlt.onchange = () => {
      loadGponPorts(getSelectedOltQuery());
    };
  }

  modal.style.display = 'flex';

  const btnSave = document.getElementById('btn-save-ftth-branch');
  const btnCancel = document.getElementById('btn-cancel-ftth-branch');
  const btnDelete = document.getElementById('btn-delete-ftth-branch');
  const btnClose = document.getElementById('btn-close-ftth-branch-modal');

  const closeModal = () => { modal.style.display = 'none'; };

  if (btnClose) btnClose.onclick = closeModal;
  if (btnCancel) btnCancel.onclick = closeModal;

  if (btnSave) {
    btnSave.onclick = async () => {
      const newName = inputName ? inputName.value.trim() : 'Brazo FTTH';
      const selOltOpt = selectOlt && selectOlt.selectedOptions[0];
      const selPortOpt = selectPort && selectPort.selectedOptions[0];

      const gponPortVal = selectPort ? selectPort.value : 'GPON 0/1/0';
      const gponIdxVal = selPortOpt ? (selPortOpt.dataset.index || '') : '';
      const oltNodeIdVal = selOltOpt ? (selOltOpt.dataset.nodeId || selOltOpt.value) : '';
      const oltIpVal = selOltOpt ? (selOltOpt.dataset.oltIp || '') : '';
      const oltNameVal = selOltOpt ? (selOltOpt.dataset.oltName || '') : '';
      const threshVal = inputThreshold ? parseFloat(inputThreshold.value) || -27.0 : -27.0;
      const arrowDirVal = selectDir ? selectDir.value : 'left';

      const updatedExtra = Object.assign({}, extra, {
        branch_name: newName,
        gpon_port: gponPortVal,
        gpon_index: gponIdxVal,
        olt_ip: oltIpVal,
        olt_name: oltNameVal,
        olt_node_id: oltNodeIdVal,
        typical_threshold_dbm: threshVal,
        arrow_direction: arrowDirVal
      });

      try {
        await API.updateNode(node.id, {
          name: newName,
          device_type: 'ftth_branch',
          extra_data: updatedExtra
        });
        node.name = newName;
        node.device_type = 'ftth_branch';
        node.extra_data = updatedExtra;

        // Limpiar cache para refrescar
        _gponTelemetryCache.delete(node.id);

        // Re-renderizar nodo en canvas
        const grp = nodeGroups.get(node.id);
        if (grp) grp.destroy();
        nodeGroups.delete(node.id);
        _renderFtthBranchNode(node);

        // ── Auto-vincular / Actualizar arista interactiva de fibra con la OLT ──
        if (currentMap && currentMap.nodes && oltNodeIdVal) {
          const oltNode = currentMap.nodes.find(n => n.id === oltNodeIdVal || (oltIpVal && n.ip === oltIpVal) || (oltNameVal && n.name === oltNameVal));
          if (oltNode && oltNode.id !== node.id) {
            // Buscar si ya existe enlace hacia este brazo
            let existingLink = currentMap.links && currentMap.links.find(l =>
              (l.source_node_id === node.id || l.target_node_id === node.id)
            );

            if (existingLink) {
              const updatedLinkExtra = Object.assign({}, existingLink.extra_data, {
                direction: 'source_to_target',
                is_gpon_branch: true,
                cable_type: 'fiber',
                gpon_port: gponPortVal,
                gpon_index: gponIdxVal,
                olt_ip: oltIpVal,
                olt_name: oltNameVal
              });
              await API.updateLink(existingLink.id, {
                source_node_id: oltNode.id,
                target_node_id: node.id,
                source_interface: gponPortVal,
                target_interface: '',
                cable_type: 'fiber',
                extra_data: updatedLinkExtra
              });
              existingLink.source_node_id = oltNode.id;
              existingLink.target_node_id = node.id;
              existingLink.source_interface = gponPortVal;
              existingLink.target_interface = '';
              existingLink.cable_type = 'fiber';
              existingLink.extra_data = updatedLinkExtra;

              // Destruir gráficos Konva previos del enlace y volver a dibujarlo
              const oldLinkEntry = linkLines.get(existingLink.id);
              if (oldLinkEntry) {
                if (oldLinkEntry.line) oldLinkEntry.line.destroy();
                if (oldLinkEntry.srcBadge) oldLinkEntry.srcBadge.destroy();
                if (oldLinkEntry.tgtBadge) oldLinkEntry.tgtBadge.destroy();
                linkLines.delete(existingLink.id);
              }
              const dict = new Map();
              currentMap.nodes.forEach(n => dict.set(n.id, n));
              renderLink(existingLink, dict);
            } else {
              // Crear nuevo enlace
              const newLink = await API.createLink({
                map_id: currentMap.id,
                source_node_id: oltNode.id,
                target_node_id: node.id,
                source_interface: gponPortVal,
                target_interface: '',
                cable_type: 'fiber',
                status: 'ok',
                extra_data: {
                  direction: 'source_to_target',
                  is_gpon_branch: true,
                  cable_type: 'fiber',
                  gpon_port: gponPortVal,
                  gpon_index: gponIdxVal,
                  olt_ip: oltIpVal,
                  olt_name: oltNameVal
                }
              });
              if (!currentMap.links) currentMap.links = [];
              currentMap.links.push(newLink);
              const dict = new Map();
              currentMap.nodes.forEach(n => dict.set(n.id, n));
              renderLink(newLink, dict);
            }
          }
        }

        updateAllLinks();
        if (linksLayer) linksLayer.batchDraw();
        if (nodesLayer) nodesLayer.batchDraw();

        selectNode(node);
        closeModal();
      } catch (err) {
        alert('Error guardando brazo FTTH: ' + (err.message || err));
      }
    };
  }

  if (btnDelete) {
    btnDelete.onclick = async () => {
      if (!confirm('¿Eliminar este brazo FTTH del lienzo?')) return;
      try {
        await API.deleteNode(node.id);
        const grp = nodeGroups.get(node.id);
        if (grp) grp.destroy();
        nodeGroups.delete(node.id);
        if (currentMap && currentMap.nodes) {
          currentMap.nodes = currentMap.nodes.filter(n => n.id !== node.id);
        }
        nodesLayer.batchDraw();
        deselectNode();
        closeModal();
      } catch (err) {
        alert('Error eliminando brazo FTTH: ' + (err.message || err));
      }
    };
  }
}

function selectNode(node) {
  deselectNode();
  selectedNode = node;

  const grp = nodeGroups.get(node.id);
  if (grp) {
    const box = grp.findOne('.box');
    box.stroke('#0ea5e9');
    box.strokeWidth(2.5);
    box.isHighlighted = true;
    nodesLayer.batchDraw();
  }

  document.getElementById('no-selection-msg').style.display = 'none';
  document.getElementById('node-properties-panel').style.display = 'block';

  // ── Panel especial para Nodo Nota ──
  if (node.device_type === 'note') {
    _selectNoteNode(node);
    switchTab('tab-properties');
    return;
  }

  // ── Panel especial para Brazo FTTH ──
  if (node.device_type === 'ftth_branch') {
    _selectFtthBranchNode(node);
    switchTab('tab-properties');
    return;
  }

  let extra = node.extra_data || {};
  const isParentShortcut = node.device_type === 'parent_map' || !!extra.is_parent_shortcut;
  const isSubmap = node.device_type === 'submap' || isParentShortcut;

  document.getElementById('prop-node-title').textContent = node.name || 'Sin Nombre';
  const badgeEl = document.getElementById('prop-node-type-badge');
  if (badgeEl) {
    const roleHex = getNodeRoleColor(node);
    badgeEl.textContent = isParentShortcut ? 'Subir Nivel ⬆' : (isSubmap ? 'Submapa' : (extra.role || node.device_type || 'Dispositivo'));
    if (roleHex && !isParentShortcut && !isSubmap) {
      badgeEl.style.backgroundColor = hexToRgba(roleHex, 0.22) || '';
      badgeEl.style.borderColor = roleHex;
      badgeEl.style.color = roleHex;
    } else {
      badgeEl.style.backgroundColor = '';
      badgeEl.style.borderColor = '';
      badgeEl.style.color = '';
    }
  }

  const subtitleEl = document.getElementById('prop-node-subtitle');
  if (isParentShortcut) {
    subtitleEl.textContent = 'Portal de Navegación a Nivel Superior';
  } else if (isSubmap) {
    subtitleEl.textContent = 'Contenedor de Topología Hija';
  } else if (extra.manufacturer || extra.model) {
    subtitleEl.textContent = `${extra.manufacturer || ''} ${extra.model || ''}`.trim();
  } else {
    subtitleEl.textContent = 'Ficha técnica NetBox';
  }

  const ipLinkEl = document.getElementById('prop-node-ip-link');
  const ipTextEl = document.getElementById('prop-node-ip');
  const webAdminBtn = document.getElementById('btn-open-device-web');

  const rawIp = (node.ip || '').trim();
  if (rawIp && !isSubmap) {
    const cleanIp = rawIp.split('/')[0].trim();
    if (ipTextEl) ipTextEl.textContent = rawIp;

    const deviceWebUrl = `http://${cleanIp}`;
    if (ipLinkEl) {
      ipLinkEl.href = deviceWebUrl;
      ipLinkEl.target = '_blank';
      ipLinkEl.rel = 'noopener noreferrer';
      ipLinkEl.title = `Abrir administración web de ${cleanIp} en nueva pestaña`;
      ipLinkEl.classList.remove('disabled');
      ipLinkEl.style.display = 'inline-flex';
    }
    if (webAdminBtn) {
      webAdminBtn.href = deviceWebUrl;
      webAdminBtn.target = '_blank';
      webAdminBtn.rel = 'noopener noreferrer';
      webAdminBtn.title = `Abrir administración web de ${cleanIp} en nueva pestaña`;
      webAdminBtn.style.display = 'flex';
    }
  } else {
    if (ipTextEl) ipTextEl.textContent = isParentShortcut ? 'Navegación Canvas' : (isSubmap ? 'Contenedor Virtual' : 'Sin IP configurada');
    if (ipLinkEl) {
      ipLinkEl.removeAttribute('href');
      ipLinkEl.removeAttribute('target');
      ipLinkEl.title = isParentShortcut ? 'Portal de Navegación' : 'Dispositivo sin dirección IP';
      ipLinkEl.classList.add('disabled');
    }
    if (webAdminBtn) {
      webAdminBtn.style.display = 'none';
    }
  }
  document.getElementById('prop-node-site').textContent = node.site_name || (isParentShortcut ? (node.extra_data?.parent_map_name || 'Mapa Superior') : 'No asignado');
  document.getElementById('prop-node-role').textContent = isParentShortcut ? 'Acceso Directo a Mapa Padre' : (isSubmap ? 'Contenedor Submapa' : (extra.role || node.device_type || 'N/A'));
  document.getElementById('prop-node-mfr').textContent = extra.manufacturer || (isParentShortcut ? 'NexusDude System' : (isSubmap ? 'Sistema' : 'Genérico'));
  document.getElementById('prop-node-model').textContent = extra.model || (isParentShortcut ? 'Portal Jerárquico' : (isSubmap ? 'Submapa Virtual' : 'N/A'));
  document.getElementById('prop-node-serial').textContent = extra.serial || (isParentShortcut ? 'N/A' : 'No registrado');

  const statusEl = document.getElementById('prop-node-status');
  const statusVal = extra.status || node.status || 'active';
  statusEl.textContent = (statusVal === 'active' || statusVal === 'ok') ? 'Activo' : statusVal;
  statusEl.style.color = (statusVal === 'active' || statusVal === 'ok') ? 'var(--success)' : 'var(--danger)';

  document.getElementById('prop-node-coords').textContent = `X: ${Math.round(node.x)}, Y: ${Math.round(node.y)}`;

  const netboxBtn = document.getElementById('btn-open-netbox');
  if (node.device_id && !isParentShortcut) {
    const host = window.location.hostname || '10.9.1.6';
    netboxBtn.href = `https://${host}:8443/dcim/devices/${node.device_id}/`;
    netboxBtn.style.display = 'inline-flex';

    if (!extra.serial || !extra.model) {
      API.getDeviceById(node.device_id).then(dev => {
        if (dev && selectedNode && selectedNode.id === node.id) {
          extra.manufacturer = dev.manufacturer || extra.manufacturer;
          extra.model = dev.model || extra.model;
          extra.serial = dev.serial || extra.serial;
          extra.status = dev.status || extra.status;
          extra.role = dev.role || extra.role;
          node.extra_data = extra;

          document.getElementById('prop-node-subtitle').textContent = `${dev.manufacturer || ''} ${dev.model || ''}`.trim();
          document.getElementById('prop-node-mfr').textContent = dev.manufacturer || 'Genérico';
          document.getElementById('prop-node-model').textContent = dev.model || 'N/A';
          document.getElementById('prop-node-serial').textContent = dev.serial || 'No registrado';
          if (dev.role) document.getElementById('prop-node-role').textContent = dev.role;
        }
      }).catch(() => {});
    }
  } else {
    netboxBtn.style.display = 'none';
  }

  const zabbixBtn = document.getElementById('btn-open-zabbix');
  if (zabbixBtn) {
    if (!isSubmap && !isParentShortcut && node.name) {
      const zabbixBase = window.zabbixBaseUrl || 'https://10.9.1.7:8082';
      const hostName = encodeURIComponent(node.name);
      zabbixBtn.href = `${zabbixBase}/zabbix.php?action=latest.view&filter_name=${hostName}&filter_groupids[]=0`;
      zabbixBtn.style.display = 'inline-flex';
    } else {
      zabbixBtn.style.display = 'none';
    }
  }

  const convertSubmapBox = document.getElementById('convert-submap-action-box');
  const submapBox = document.getElementById('submap-action-box');
  const btnEnterSubmap = document.getElementById('btn-enter-submap');

  if (isSubmap) {
    if (convertSubmapBox) convertSubmapBox.style.display = 'none';
    if (submapBox) {
      submapBox.style.display = node.extra_data?.target_map_id ? 'block' : 'none';
      if (btnEnterSubmap) {
        if (isParentShortcut) {
          const pName = node.extra_data?.parent_map_name || 'Mapa Padre';
          btnEnterSubmap.innerHTML = `<i class="fas fa-level-up-alt"></i> Subir a ${pName}`;
          btnEnterSubmap.style.background = 'linear-gradient(135deg, #0284c7 0%, #0369a1 100%)';
          btnEnterSubmap.style.borderColor = '#38bdf8';
        } else {
          btnEnterSubmap.innerHTML = `<i class="fas fa-folder-open"></i> Entrar al Submapa`;
          btnEnterSubmap.style.background = '';
          btnEnterSubmap.style.borderColor = '';
        }
      }
    }
  } else {
    if (convertSubmapBox) convertSubmapBox.style.display = 'block';
    if (submapBox) submapBox.style.display = 'none';
  }

  // ─── TELEMETRÍA EN TIEMPO REAL (Zabbix) ─────────────────────────────────
  const telemetryPanel = document.getElementById('telemetry-panel');
  if (telemetryPanel) {
    if (isParentShortcut) {
      telemetryPanel.style.display = 'none';
    } else if (!isSubmap && node.ip) {
      telemetryPanel.style.display = 'block';
      setTelemetryLoading();
      loadNodeTelemetry(node.id);
    } else if (isSubmap && !isParentShortcut) {
      telemetryPanel.style.display = 'block';
      setTelemetryLoading();
      loadNodeTelemetry(node.id);
    } else {
      telemetryPanel.style.display = 'none';
    }
  }

  // ─── PUERTOS & INTERFACES FÍSICAS (NetBox) ──────────────────────────────
  const portsCard = document.getElementById('node-ports-card');
  const portsGrid = document.getElementById('node-ports-grid');
  const portsBadge = document.getElementById('node-ports-count-badge');
  const portsLoading = document.getElementById('node-ports-loading');

  if (portsCard && portsGrid) {
    const devId = node ? (node.device_id || node.extra_data?.device_id || node.extra_data?.netbox_id) : null;
    if (devId && !isParentShortcut && !isSubmap) {
      portsCard.style.display = 'block';
      if (portsLoading) portsLoading.style.display = 'block';
      portsGrid.innerHTML = '';
      if (portsBadge) portsBadge.textContent = '...';

      API.getDeviceInterfaces(devId).then(ifaces => {
        if (!selectedNode || selectedNode.id !== node.id) return;
        if (portsLoading) portsLoading.style.display = 'none';
        if (portsBadge) portsBadge.textContent = `${ifaces.length} Puertos`;

        if (!ifaces || ifaces.length === 0) {
          portsGrid.innerHTML = '<div style="grid-column: span 3; font-size: 0.72rem; color: var(--text-muted); text-align: center;">Sin puertos registrados</div>';
          return;
        }

        portsGrid.innerHTML = '';
        ifaces.forEach(iface => {
          const isConn = iface.is_connected;
          const isFiber = (iface.type || '').includes('sfp');
          const chip = document.createElement('div');
          chip.className = 'port-slot-chip';
          chip.style.cssText = `
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            background: ${isConn ? 'rgba(56, 189, 248, 0.12)' : 'rgba(15, 23, 42, 0.7)'};
            border: 1px solid ${isConn ? '#38bdf8' : (isFiber ? 'rgba(168, 85, 247, 0.4)' : 'rgba(148, 163, 184, 0.25)')};
            border-radius: 5px; padding: 4px 3px; cursor: pointer; transition: all 0.15s ease;
          `;
          
          const icon = isFiber ? '<i class="fas fa-bolt" style="font-size: 0.65rem; color: #c084fc;"></i>' : '<i class="fas fa-ethernet" style="font-size: 0.65rem; color: #38bdf8;"></i>';
          const statusDot = `<span style="display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: ${isConn ? '#10b981' : '#64748b'}; margin-left: 2px;"></span>`;
          
          chip.innerHTML = `
            <div style="font-size: 0.68rem; font-weight: 700; color: #f8fafc; display: flex; align-items: center; gap: 3px;">
              ${icon} <span>${iface.name}</span> ${statusDot}
            </div>
            <div style="font-size: 0.58rem; color: var(--text-muted); margin-top: 1px;">
              ${iface.type ? (iface.type.includes('sfp') ? 'SFP+' : '1G') : 'Port'}
            </div>
          `;

          const peerInfo = iface.connected_device ? `Conectado a ${iface.connected_device} (${iface.connected_interface})` : (isConn ? 'Conectado' : 'Disponible');
          chip.title = `${iface.name} (${iface.type || 'Port'}) — ${peerInfo}. Clic para iniciar trazado de enlace desde este puerto.`;

          chip.onmouseenter = () => {
            chip.style.transform = 'translateY(-1px)';
            chip.style.borderColor = '#38bdf8';
            chip.style.boxShadow = '0 2px 6px rgba(56, 189, 248, 0.25)';
          };
          chip.onmouseleave = () => {
            chip.style.transform = '';
            chip.style.borderColor = isConn ? '#38bdf8' : (isFiber ? 'rgba(168, 85, 247, 0.4)' : 'rgba(148, 163, 184, 0.25)');
            chip.style.boxShadow = '';
          };

          chip.onclick = () => {
            startLinkMode();
            handleLinkNodeClick(node);
          };

          portsGrid.appendChild(chip);
        });
      }).catch(err => {
        if (portsLoading) portsLoading.style.display = 'none';
        portsGrid.innerHTML = `<div style="grid-column: span 3; font-size: 0.7rem; color: #f87171;">Error cargando puertos: ${err.message}</div>`;
      });
    } else {
      portsCard.style.display = 'none';
    }
  }

  // Cambiar a la pestaña de propiedades
  switchTab('tab-properties');
}

function deselectNode() {
  if (selectedNode) {
    const grp = nodeGroups.get(selectedNode.id);
    if (grp) {
      const box = grp.findOne('.box');
      if (box) {
        if (selectedNode.device_type === 'note') {
          // Restaurar borde de la nota al color del tema
          const colorKey = selectedNode.extra_data?.note_color || 'yellow';
          const theme = NOTE_COLORS[colorKey] || NOTE_COLORS['yellow'];
          box.stroke(theme.border);
          box.strokeWidth(1.5);
        } else if (selectedNode.device_type === 'ftth_branch') {
          // Restaurar borde del brazo FTTH
          const isDown = selectedNode.status === 'down' || selectedNode.ping_status === 'down';
          const hasAtyp = (selectedNode.extra_data?.atypical_count || 0) > 0;
          const col = isDown ? '#ef4444' : (hasAtyp ? '#f59e0b' : '#10b981');
          box.stroke(col);
          box.strokeWidth(isDown ? 2 : 1.5);
          const arrow = grp.findOne('.branchArrow');
          if (arrow) arrow.stroke(col);
        } else {
          const isParentShortcut = grp.isParentShortcut || false;
          const isSubmap = grp.isSubmap || (selectedNode.device_type === 'submap');
          const curPingStatus = selectedNode.ping_status || selectedNode.status;
          box.stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
          box.strokeWidth(isParentShortcut ? 2 : 1.5);
        }
        box.isHighlighted = false;
      }
    }
    selectedNode = null;
    nodesLayer.batchDraw();
  }
  document.getElementById('no-selection-msg').style.display = 'block';
  document.getElementById('node-properties-panel').style.display = 'none';
  const gponPanel = document.getElementById('gpon-properties-panel');
  if (gponPanel) gponPanel.style.display = 'none';
  const notePanel = document.getElementById('note-properties-panel');
  if (notePanel) notePanel.style.display = 'none';
}

// ─── SELECCIÓN MÚLTIPLE (Marquee / Área de Selección con Clic Derecho y Shift-Click) ──────
function addNodeToMultiSelection(node) {
  if (!node) return;
  selectedNodes.add(node);

  const grp = nodeGroups.get(node.id);
  if (grp) {
    const box = grp.findOne('.box');
    if (box) {
      box.stroke('#38bdf8');
      box.strokeWidth(2.5);
      box.isHighlighted = true;
    }
  }
  nodesLayer.batchDraw();
}

function removeNodeFromMultiSelection(node) {
  if (!node) return;
  selectedNodes.delete(node);

  const grp = nodeGroups.get(node.id);
  if (grp) {
    const box = grp.findOne('.box');
    if (box) {
      const isParentShortcut = grp.isParentShortcut || false;
      const isSubmap = grp.isSubmap || (node.device_type === 'submap');
      const curPingStatus = node.ping_status || node.status;
      box.stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
      box.strokeWidth(isParentShortcut ? 2 : 1.5);
      box.isHighlighted = false;
    }
  }
  nodesLayer.batchDraw();

  if (selectedNodes.size > 0) {
    showMultiSelectionNotice(selectedNodes.size);
  } else {
    clearMultiSelection();
  }
}

function clearMultiSelection() {
  selectedNodes.forEach(node => {
    const grp = nodeGroups.get(node.id);
    if (grp) {
      const box = grp.findOne('.box');
      if (box && node !== selectedNode) {
        const isParentShortcut = grp.isParentShortcut || false;
        const isSubmap = grp.isSubmap || (node.device_type === 'submap');
        const curPingStatus = node.ping_status || node.status;
        box.stroke(isParentShortcut ? '#38bdf8' : getNodeStatusColor(curPingStatus, isSubmap));
        box.strokeWidth(isParentShortcut ? 2 : 1.5);
        box.isHighlighted = false;
      }
    }
  });
  selectedNodes.clear();
  hideMultiSelectionNotice();
  nodesLayer.batchDraw();
}

function showMultiSelectionNotice(count) {
  document.getElementById('node-properties-panel').style.display = 'none';
  const msgEl = document.getElementById('no-selection-msg');
  if (msgEl) {
    msgEl.style.display = 'block';

    const selectedArray = Array.from(selectedNodes);
    
    // Contabilizar enlaces conectados a los nodos seleccionados
    let totalConnectedLinks = 0;
    const selectedIds = new Set(selectedArray.map(n => n.id));
    linkLines.forEach(({ sourceId, targetId }) => {
      if (selectedIds.has(sourceId) || selectedIds.has(targetId)) {
        totalConnectedLinks++;
      }
    });

    // Construir filas de previsualización de cada nodo seleccionado
    const itemsHtml = selectedArray.map(node => {
      const isSubmap = node.device_type === 'submap';
      const icon = getRoleIcon(node.device_type);
      const subInfo = isSubmap ? 'Submapa' : (node.ip || node.site_name || 'Sin IP');
      const safeName = (node.name || 'Sin nombre').replace(/"/g, '&quot;');
      return `
        <div class="multi-select-node-item">
          <div style="display: flex; align-items: center; gap: 7px; overflow: hidden; min-width: 0; flex: 1;">
            <span style="font-size: 0.85rem; flex-shrink: 0;">${icon}</span>
            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left;">
              <div style="font-size: 0.78rem; font-weight: 600; color: #f1f5f9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${safeName}">${safeName}</div>
              <div style="font-size: 0.68rem; color: #94a3b8; font-family: monospace;">${subInfo}</div>
            </div>
          </div>
          <button type="button" class="btn-remove-from-sel" data-node-id="${node.id}" title="Quitar de la selección">
            <i class="fas fa-times"></i>
          </button>
        </div>
      `;
    }).join('');

    msgEl.innerHTML = `
      <div class="multi-select-card">
        
        <!-- Cabecera de Selección Múltiple -->
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(56, 189, 248, 0.2);">
          <div style="display: flex; align-items: center; gap: 8px;">
            <div style="width: 28px; height: 28px; border-radius: 6px; background: rgba(56, 189, 248, 0.15); display: flex; align-items: center; justify-content: center; color: #38bdf8; font-size: 0.9rem;">
              <i class="fas fa-object-group"></i>
            </div>
            <div>
              <div style="font-weight: 700; color: #f8fafc; font-size: 0.88rem;">Selección Múltiple</div>
              <div style="font-size: 0.68rem; color: #94a3b8;">${count} nodos marcados en el lienzo</div>
            </div>
          </div>
          <span style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); font-size: 0.7rem; font-weight: 700; padding: 2px 7px; border-radius: 12px;">
            ${count}
          </span>
        </div>

        <!-- Indicador de reacomodo -->
        <p style="font-size: 0.73rem; color: #94a3b8; margin: 0 0 10px 0; line-height: 1.4;">
          <i class="fas fa-arrows-alt" style="color: #38bdf8; margin-right: 4px;"></i> Arrastra cualquier nodo marcado en el lienzo para mover el grupo en bloque.
        </p>

        <!-- Lista de nodos seleccionados -->
        <div style="margin-bottom: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.7rem; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px;">
            <span>Nodos en el grupo</span>
            <span style="font-size: 0.65rem; color: #64748b;">${count} elementos</span>
          </div>
          <div class="multi-select-scroll-list" style="max-height: 180px; overflow-y: auto; padding-right: 2px;">
            ${itemsHtml}
          </div>
        </div>

        <!-- Acciones Masivas -->
        <div style="display: flex; flex-direction: column; gap: 6px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.06);">
          <!-- Botón Eliminar Nodos del Mapa -->
          <button type="button" class="btn btn-danger" id="btn-bulk-delete-nodes" style="width: 100%; justify-content: center; padding: 8px; font-size: 0.78rem; font-weight: 700; box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25);" title="Quitar todos los nodos seleccionados del mapa">
            <i class="fas fa-trash-alt"></i> Quitar (${count}) del Mapa
          </button>

          <!-- Botón Eliminar Enlaces de los Nodos -->
          ${totalConnectedLinks > 0 ? `
          <button type="button" class="btn" id="btn-bulk-delete-links" style="width: 100%; justify-content: center; padding: 6px; font-size: 0.74rem; background: rgba(239, 68, 68, 0.1); border: 1px dashed #ef4444; color: #f87171;" title="Eliminar las conexiones vinculadas a estos nodos">
            <i class="fas fa-project-diagram"></i> Eliminar Enlaces (${totalConnectedLinks})
          </button>` : ''}

          <!-- Botón Deseleccionar -->
          <button type="button" class="btn" style="width: 100%; justify-content: center; font-size: 0.72rem; padding: 6px 10px; background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.12); color: #cbd5e1; margin-top: 2px;" id="btn-clear-selection-notice">
            <i class="fas fa-times"></i> Deseleccionar Todo
          </button>
        </div>

      </div>
    `;

    // Event listener: Deseleccionar
    const btnClear = document.getElementById('btn-clear-selection-notice');
    if (btnClear) btnClear.addEventListener('click', clearMultiSelection);

    // Event listener: Quitar individual de la selección
    msgEl.querySelectorAll('.btn-remove-from-sel').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const nodeId = btn.getAttribute('data-node-id');
        const nodeObj = selectedArray.find(n => n.id === nodeId);
        if (nodeObj) {
          removeNodeFromMultiSelection(nodeObj);
        }
      });
    });

    // Event listener: Eliminar nodos masivamente del mapa
    const btnBulkDelete = document.getElementById('btn-bulk-delete-nodes');
    if (btnBulkDelete) {
      btnBulkDelete.addEventListener('click', async () => {
        if (selectedNodes.size === 0) return;
        const countToDelete = selectedNodes.size;
        const msg = `¿Estás seguro de quitar los ${countToDelete} nodos seleccionados de la topología?\n\nNota: Solo se retiran del mapa visual de NexusDude. Los dispositivos y su inventario permanecen intactos en NetBox.`;
        if (!confirm(msg)) return;

        btnBulkDelete.disabled = true;
        btnBulkDelete.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Quitando...';

        try {
          const idsToDelete = Array.from(selectedNodes).map(n => n.id);
          const idsSet = new Set(idsToDelete);

          // Intentar eliminación masiva atómica
          try {
            await API.bulkDeleteNodes(idsToDelete);
          } catch (e) {
            // Fallback por nodo si endpoint masivo tuviera error
            for (const id of idsToDelete) {
              await API.deleteNode(id);
            }
          }

          // Destruir grupos visuales de Konva
          idsToDelete.forEach(id => {
            const grp = nodeGroups.get(id);
            if (grp) grp.destroy();
            nodeGroups.delete(id);
          });

          // Destruir enlaces conectados a cualquiera de los nodos eliminados
          linkLines.forEach(({ line, sourceId, targetId }, linkId) => {
            if (idsSet.has(sourceId) || idsSet.has(targetId)) {
              line.destroy();
              linkLines.delete(linkId);
            }
          });

          // Actualizar datos del mapa actual
          if (currentMap && currentMap.nodes) {
            currentMap.nodes = currentMap.nodes.filter(n => !idsSet.has(n.id));
          }

          // Limpiar selección
          selectedNodes.clear();
          hideMultiSelectionNotice();

          // Redibujar capas y actualizar enlaces restantes
          nodesLayer.batchDraw();
          updateAllLinks();

          console.log(`[NexusDude] ${countToDelete} nodos eliminados del mapa correctamente.`);
        } catch (err) {
          alert('Error al quitar los nodos seleccionados: ' + err.message);
          btnBulkDelete.disabled = false;
          btnBulkDelete.innerHTML = `<i class="fas fa-trash-alt"></i> Quitar (${countToDelete}) del Mapa`;
        }
      });
    }

    // Event listener: Eliminar enlaces de los nodos seleccionados
    const btnBulkDeleteLinks = document.getElementById('btn-bulk-delete-links');
    if (btnBulkDeleteLinks) {
      btnBulkDeleteLinks.addEventListener('click', async () => {
        if (selectedNodes.size === 0) return;
        const selectedIds = new Set(Array.from(selectedNodes).map(n => n.id));
        const linksToDelete = [];
        linkLines.forEach(({ sourceId, targetId }, linkId) => {
          if (selectedIds.has(sourceId) || selectedIds.has(targetId)) {
            linksToDelete.push(linkId);
          }
        });

        if (linksToDelete.length === 0) {
          alert('No hay conexiones vinculadas a los nodos seleccionados.');
          return;
        }

        if (!confirm(`¿Estás seguro de eliminar los ${linksToDelete.length} enlaces vinculados a los nodos seleccionados?`)) return;

        btnBulkDeleteLinks.disabled = true;
        btnBulkDeleteLinks.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Eliminando enlaces...';

        try {
          for (const linkId of linksToDelete) {
            try {
              await API.deleteLink(linkId);
            } catch (e) {
              console.warn('Error eliminando enlace ' + linkId, e);
            }
            const linkObj = linkLines.get(linkId);
            if (linkObj && linkObj.line) {
              linkObj.line.destroy();
            }
            linkLines.delete(linkId);
          }

          if (currentMap && currentMap.links) {
            const linkIdsSet = new Set(linksToDelete);
            currentMap.links = currentMap.links.filter(l => !linkIdsSet.has(l.id));
          }

          linksLayer.batchDraw();
          showMultiSelectionNotice(selectedNodes.size);
        } catch (err) {
          alert('Error eliminando enlaces: ' + err.message);
        }
      });
    }
  }
  switchTab('tab-properties');
}

function hideMultiSelectionNotice() {
  const msgEl = document.getElementById('no-selection-msg');
  if (msgEl && selectedNodes.size === 0 && !selectedNode) {
    msgEl.innerHTML = `
      <i class="fas fa-mouse-pointer" style="font-size: 2rem; margin-bottom: 10px; color: var(--accent); opacity: 0.6;"></i>
      <p style="margin-bottom: 6px; font-weight: 600; color: #cbd5e1;">Ningún elemento seleccionado</p>
      <p style="font-size: 0.78rem;">Haz clic sobre cualquier equipo o submapa en el lienzo para consultar su ficha técnica y telemetría.</p>
    `;
    msgEl.style.display = 'block';
  }
}


// ─── 7. Carga de Mapas y Breadcrumb Jerárquico ──────────────────────────────

// ─── TELEMETRÍA: helpers para el panel de propiedades ────────────────────────
// ─── TELEMETRÍA: helpers para el panel de propiedades ────────────────────────
function setTelemetryLoading() {
  const safe = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  safe('telemetry-rtt', '…');
  safe('telemetry-loss', '…');
  safe('telemetry-problems-count', '…');
  safe('telemetry-last-update', 'cargando…');
  safe('telemetry-channel-bw', '…');
  safe('telemetry-freq', '…');
  safe('telemetry-rssi', '…');
  safe('telemetry-snr', '…');

  const wBox = document.getElementById('telemetry-wireless-box');
  if (wBox) wBox.style.display = 'none';

  const dot = document.getElementById('telemetry-status-dot');
  const lbl = document.getElementById('telemetry-status-label');
  if (dot) { dot.style.background = '#64748b'; dot.style.boxShadow = 'none'; }
  if (lbl) lbl.textContent = 'Consultando…';
  const list = document.getElementById('telemetry-problems-list');
  if (list) { list.innerHTML = ''; list.style.display = 'none'; }
}

function applyTelemetryToPanel(data) {
  if (!data) {
    const lbl = document.getElementById('telemetry-status-label');
    if (lbl) lbl.textContent = 'No disponible en Zabbix';
    return;
  }

  const status = data.status || 'unknown';
  const colors = { ok: '#10b981', warning: '#f59e0b', down: '#ef4444', unknown: '#64748b' };
  const labels = { ok: 'En línea', warning: 'Degradado', down: 'Fuera de línea', unknown: 'Sin datos' };
  const glows = { ok: '0 0 8px #10b981', warning: '0 0 8px #f59e0b', down: '0 0 8px #ef4444', unknown: 'none' };

  const dot = document.getElementById('telemetry-status-dot');
  const lbl = document.getElementById('telemetry-status-label');
  if (dot) { dot.style.background = colors[status] || '#64748b'; dot.style.boxShadow = glows[status] || 'none'; }
  if (lbl) { lbl.textContent = labels[status] || status; lbl.style.color = colors[status] || '#94a3b8'; }

  const safe = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  const rtt = data.rtt_ms;
  safe('telemetry-rtt', rtt != null ? (rtt > 0 ? `${rtt.toFixed(1)} ms` : '∞') : '—');
  const loss = data.packet_loss;
  safe('telemetry-loss', loss != null ? `${loss.toFixed(0)}%` : '—');

  // Actualizar Diagnóstico Dual (Ping ICMP vs Telemetría SNMP)
  const pingStatus = data.ping_status || (data.icmp_ping === 1 ? 'ok' : (data.icmp_ping === 0 ? 'down' : 'unknown'));
  const snmpStatus = data.snmp_status || (data.snmp_available === 1 ? 'ok' : (data.snmp_available === 2 ? 'down' : 'unknown'));
  const hasSnmpIssue = !!data.has_snmp_issue || (pingStatus === 'ok' && (snmpStatus === 'down' || snmpStatus === 'unknown' || data.snmp_available === 2));

  const pingDot = document.getElementById('telemetry-ping-dot');
  const pingText = document.getElementById('telemetry-ping-text');
  const pingPill = document.getElementById('telemetry-ping-pill');
  if (pingDot && pingText) {
    const pColor = pingStatus === 'ok' ? '#10b981' : (pingStatus === 'warning' ? '#f59e0b' : (pingStatus === 'down' ? '#ef4444' : '#64748b'));
    pingDot.style.background = pColor;
    pingText.style.color = pColor;
    pingText.textContent = pingStatus === 'ok' ? 'En línea (Ping OK)' : (pingStatus === 'warning' ? 'Ping Degradado' : (pingStatus === 'down' ? 'Sin Respuesta' : 'Sin datos'));
    if (pingPill) pingPill.style.borderColor = pColor + '55';
  }

  const snmpDot = document.getElementById('telemetry-snmp-dot');
  const snmpText = document.getElementById('telemetry-snmp-text');
  const snmpPill = document.getElementById('telemetry-snmp-pill');
  if (snmpDot && snmpText) {
    const sColor = snmpStatus === 'ok' ? '#10b981' : (snmpStatus === 'warning' ? '#f59e0b' : (snmpStatus === 'down' ? '#ef4444' : '#64748b'));
    snmpDot.style.background = sColor;
    snmpText.style.color = sColor;
    snmpText.textContent = snmpStatus === 'ok' ? 'Activo (v2c)' : (snmpStatus === 'down' || data.snmp_available === 2 ? 'Timeout / Falló' : (snmpStatus === 'warning' ? 'Alertas SNMP' : 'Sin datos'));
    if (snmpPill) snmpPill.style.borderColor = sColor + '55';
  }

  const snmpWarnBox = document.getElementById('telemetry-snmp-warning-box');
  const snmpWarnDesc = document.getElementById('telemetry-snmp-warning-desc');
  if (snmpWarnBox) {
    if (hasSnmpIssue) {
      snmpWarnBox.style.display = 'block';
      if (snmpWarnDesc) {
        snmpWarnDesc.textContent = data.snmp_warning_message || (data.snmp_error ? `Fallo SNMP: ${data.snmp_error}` : 'El equipo responde a Ping ICMP por IP pero el agente SNMP no entrega datos (Timeout en puerto 161 o comunidad no coincide).');
      }
    } else {
      snmpWarnBox.style.display = 'none';
    }
  }

  // 1. Parámetros Inalámbricos (Cambium / Altai / Ubiquiti / Mimosa)
  const wBox = document.getElementById('telemetry-wireless-box');
  const w = data.wireless;
  if (wBox) {
    if (w && (w.channel_width_text || w.frequency_mhz || w.rssi_dbm || w.snr_db || w.mode || w.channel_width_id)) {
      wBox.style.display = 'block';
      safe('telemetry-channel-bw', w.channel_width_text || (w.channel_width_id ? `ID ${w.channel_width_id}` : '—'));
      safe('telemetry-freq', w.frequency_mhz ? `${w.frequency_mhz} MHz` : '—');
      safe('telemetry-rssi', w.rssi_dbm ? `${w.rssi_dbm} dBm` : '—');
      safe('telemetry-snr', w.snr_db ? `${w.snr_db} dB` : '—');
      safe('telemetry-radio-mode', w.mode ? (w.connected_sta_count ? `${w.mode} (${w.connected_sta_count} STAs)` : w.mode) : (w.connected_sta_count ? `AP (${w.connected_sta_count} STAs)` : 'Wireless'));

      const boxMcs = document.getElementById('box-telemetry-mcs');
      if (boxMcs) {
        if (w.rx_mcs || w.tx_mcs) {
          boxMcs.style.display = 'block';
          safe('telemetry-mcs', `Rx: ${w.rx_mcs || '—'} / Tx: ${w.tx_mcs || '—'}`);
        } else {
          boxMcs.style.display = 'none';
        }
      }

      const boxDist = document.getElementById('box-telemetry-distance');
      if (boxDist) {
        if (w.distance_km) {
          boxDist.style.display = 'block';
          safe('telemetry-distance', `${w.distance_km} km`);
        } else {
          boxDist.style.display = 'none';
        }
      }

      const boxSsid = document.getElementById('box-telemetry-ssid');
      if (boxSsid) {
        if (w.ssid || w.connected_ap_mac) {
          boxSsid.style.display = 'block';
          safe('telemetry-ssid', w.ssid || w.connected_ap_mac || '—');
        } else {
          boxSsid.style.display = 'none';
        }
      }
    } else {
      wBox.style.display = 'none';
    }
  }

  // 2. Recursos de Hardware & Sensores (MikroTik / Routers / Switches)
  const hwBox = document.getElementById('telemetry-hardware-box');
  const hw = data.hardware;
  if (hwBox) {
    if (hw && (hw.cpu_util_pct != null || hw.memory_util_pct != null || hw.temp_cpu_c != null || hw.voltage_v != null)) {
      hwBox.style.display = 'block';
      safe('telemetry-cpu-util', hw.cpu_util_pct != null ? `${parseFloat(hw.cpu_util_pct).toFixed(1)}%` : '—');
      safe('telemetry-mem-util', hw.memory_util_pct != null ? `${parseFloat(hw.memory_util_pct).toFixed(1)}%` : '—');
      safe('telemetry-temp-cpu', hw.temp_cpu_c != null ? `${hw.temp_cpu_c} °C` : (hw.temp_board_c != null ? `${hw.temp_board_c} °C (Board)` : '—'));
      safe('telemetry-voltage', hw.voltage_v != null ? `${hw.voltage_v} V` : '—');
    } else {
      hwBox.style.display = 'none';
    }
  }

  // 3. Sistema & Inventario
  const sysBox = document.getElementById('telemetry-system-box');
  const sys = data.system;
  if (sysBox) {
    if (sys && (sys.model || sys.serial || sys.firmware || sys.mac || (sys.uptime_text && sys.uptime_text !== '—'))) {
      sysBox.style.display = 'block';
      const modelFw = [sys.model, sys.firmware ? `v${sys.firmware}` : ''].filter(Boolean).join(' · ');
      safe('telemetry-model-fw', modelFw || sys.sys_name || 'Dispositivo');
      safe('telemetry-serial', sys.serial || '—');
      safe('telemetry-uptime', sys.uptime_text || '—');

      const boxMac = document.getElementById('box-telemetry-mac');
      if (boxMac) {
        if (sys.mac) {
          boxMac.style.display = 'block';
          safe('telemetry-mac', sys.mac);
        } else {
          boxMac.style.display = 'none';
        }
      }
    } else {
      sysBox.style.display = 'none';
    }
  }

  // 4. Interfaz LAN
  const lanBox = document.getElementById('telemetry-lan-box');
  const lan = data.lan;
  if (lanBox) {
    if (lan && (lan.in_text || lan.out_text || lan.status)) {
      lanBox.style.display = 'block';
      const lanStatusEl = document.getElementById('telemetry-lan-status');
      if (lanStatusEl) {
        lanStatusEl.textContent = lan.status || 'Up';
        lanStatusEl.style.color = lan.status === 'Up' ? '#10b981' : '#ef4444';
        lanStatusEl.style.background = lan.status === 'Up' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
      }
      safe('telemetry-lan-in', lan.in_text || '—');
      safe('telemetry-lan-out', lan.out_text || '—');
    } else {
      lanBox.style.display = 'none';
    }
  }

  const problems = data.problems || [];
  safe('telemetry-problems-count', problems.length > 0 ? String(problems.length) : '0');

  const priorityLabel = { 0: 'Info', 1: 'Info', 2: '⚠ Warning', 3: '🔶 Average', 4: '🔴 High', 5: '🚨 Disaster' };
  const priorityColors = { 0: '#64748b', 1: '#64748b', 2: '#f59e0b', 3: '#f97316', 4: '#ef4444', 5: '#dc2626' };

  const list = document.getElementById('telemetry-problems-list');
  if (list) {
    if (problems.length > 0) {
      list.innerHTML = problems.map(p => `
        <div style="background: rgba(239,68,68,0.08); border-left: 3px solid ${priorityColors[p.priority] || '#ef4444'}; border-radius: 4px; padding: 4px 8px; font-size: 0.7rem;">
          <span style="color: ${priorityColors[p.priority] || '#ef4444'}; font-weight: 700;">${priorityLabel[p.priority] || 'Alerta'}</span>
          <span style="color: #cbd5e1; margin-left: 4px;">${p.description || 'Sin descripción'}</span>
        </div>
      `).join('');
      list.style.display = 'flex';
    } else {
      list.innerHTML = `<div style="font-size: 0.7rem; color: #10b981; padding: 2px 0;"><i class="fas fa-check-circle"></i> Sin alertas activas</div>`;
      list.style.display = 'flex';
    }
  }

  const now = new Date();
  safe('telemetry-last-update', `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`);

  // Actualizar también el dot y contorno del canvas
  if (data.node_id) applyNodeStatusToCanvas(data.node_id, status);

  // Actualizar prop-node-status bar
  const statusEl = document.getElementById('prop-node-status');
  if (statusEl) {
    const texts = { ok: 'En línea', warning: 'Degradado', down: 'Fuera de línea', unknown: 'Sin datos Zabbix' };
    statusEl.textContent = texts[status] || status;
    statusEl.style.color = colors[status] || '#94a3b8';
  }
}

async function loadNodeTelemetry(nodeId) {
  try {
    const data = await API.getNodeTelemetry(nodeId);
    // Only apply if this node is still selected
    if (selectedNode && selectedNode.id === nodeId) {
      applyTelemetryToPanel(data);
    }
  } catch (e) {
    console.warn('Error cargando telemetría del nodo:', e);
  }
}

function refreshSelectedNodeTelemetry() {
  if (!selectedNode) return;
  setTelemetryLoading();
  loadNodeTelemetry(selectedNode.id);
}

// Actualiza el dot de color (SNMP) y contorno (PING) en el canvas Konva para un nodo específico
function applyNodeStatusToCanvas(nodeId, statusData) {
  const grp = nodeGroups.get(nodeId);
  if (!grp) return;
  const dotShape = grp.findOne('.statusDot') || grp.getChildren(c => c.getClassName() === 'Circle' && !c.listening())[0];
  const boxShape = grp.findOne('.box');
  const isParentShortcut = grp.isParentShortcut || false;
  const isSubmap = grp.isSubmap || false;

  let pingStatus = 'ok';
  let snmpStatus = 'ok';
  let hasSnmpIssue = false;

  if (typeof statusData === 'object' && statusData !== null) {
    pingStatus = statusData.ping_status || statusData.status || 'ok';
    snmpStatus = statusData.snmp_status || statusData.status || 'ok';
    hasSnmpIssue = !!statusData.has_snmp_issue || (pingStatus === 'ok' && (snmpStatus === 'down' || snmpStatus === 'unknown' || statusData.snmp_available === 2));
  } else if (typeof statusData === 'string') {
    pingStatus = statusData;
    snmpStatus = statusData;
  }

  const pingColor = isParentShortcut ? '#38bdf8' : getNodeStatusColor(pingStatus, isSubmap);
  const snmpColor = isParentShortcut ? '#38bdf8' : getNodeStatusColor(snmpStatus, isSubmap);

  if (dotShape) {
    dotShape.fill(snmpColor);
  }
  if (boxShape && !boxShape.isHighlighted) {
    boxShape.stroke(pingColor);
  }

  // Indicador visual de alerta SNMP en esquina si hay Ping pero fallo de SNMP
  let snmpAlertBadge = grp.findOne('.snmpAlertBadge');
  if (hasSnmpIssue && !isSubmap && !isParentShortcut) {
    if (!snmpAlertBadge) {
      const boxW = boxShape ? boxShape.width() : 130;
      snmpAlertBadge = new Konva.Text({
        x: boxW - 20,
        y: 3,
        text: '⚠️',
        fontSize: 10,
        listening: false,
        name: 'snmpAlertBadge'
      });
      grp.add(snmpAlertBadge);
    } else {
      snmpAlertBadge.show();
    }
  } else if (snmpAlertBadge) {
    snmpAlertBadge.hide();
  }

  nodesLayer.batchDraw();
}

// ─── POLLING EN TIEMPO REAL: actualiza el mapa completo cada 45s ─────────────
function startRealtimePolling(mapId) {
  // Limpiar polling anterior
  if (_realtimePollInterval) {
    clearInterval(_realtimePollInterval);
    _realtimePollInterval = null;
  }
  if (!mapId) return;

  const poll = async () => {
    if (!currentMap || currentMap.id !== mapId) return;
    try {
      const data = await API.getMapRealtimeStatus(mapId);
      if (!data || !data.nodes) return;

      // Actualizar indicador de conectividad con Zabbix
      const sourceDot = document.getElementById('telemetry-source-dot');
      if (sourceDot) {
        if (data.zabbix_connected === false) {
          sourceDot.style.color = '#ef4444';
          sourceDot.title = 'Zabbix Desconectado / Fuera de Línea';
        } else {
          sourceDot.style.color = '#22c55e';
          sourceDot.title = 'Zabbix Conectado (7.0)';
        }
      }

      // Actualizar dot (SNMP) y contorno (PING) de cada nodo en el canvas
      for (const [nodeId, nodeStatus] of Object.entries(data.nodes)) {
        applyNodeStatusToCanvas(nodeId, nodeStatus);
        // Si este nodo está seleccionado, refrescar panel también
        if (selectedNode && selectedNode.id === nodeId) {
          applyTelemetryToPanel(nodeStatus);
        }
      }

      // Actualizar status local en currentMap
      if (currentMap) {
        currentMap.nodes.forEach(n => {
          if (data.nodes[n.id]) {
            n.status = data.nodes[n.id].status;
            n.ping_status = data.nodes[n.id].ping_status;
            n.snmp_status = data.nodes[n.id].snmp_status;
          }
        });
        updateAllLinkColors();
      }
    } catch (e) {
      console.warn('[Realtime] Error en polling de estado:', e);
      const sourceDot = document.getElementById('telemetry-source-dot');
      if (sourceDot) {
        sourceDot.style.color = '#ef4444';
        sourceDot.title = 'Error de conexión con servicio de telemetría';
      }
    }
  };

  // Primera ejecución inmediata + polling cada 45s
  poll();
  _realtimePollInterval = setInterval(poll, 45000);
}

function updateUrlHashState() {
  const currentTab = localStorage.getItem('nexusdude_active_tab') || 'tab-maps';
  const mapId = currentMap ? currentMap.id : (localStorage.getItem('nexusdude_last_map_id') || 'default-map');
  const params = new URLSearchParams();
  if (mapId) params.set('map', mapId);
  if (currentTab) params.set('tab', currentTab);
  if (selectedNode) params.set('node', selectedNode.id);
  const newHash = `#${params.toString()}`;
  if (window.location.hash !== newHash) {
    history.replaceState(null, null, newHash);
  }
}

async function loadMap(mapId) {
  linksLayer.destroyChildren();
  nodesLayer.destroyChildren();
  nodeGroups.clear();
  linkLines.clear();
  deselectNode();
  clearMultiSelection();

  // Detener polling del mapa anterior
  if (_realtimePollInterval) {
    clearInterval(_realtimePollInterval);
    _realtimePollInterval = null;
  }

  const mapData = await API.getMapDetail(mapId);
  if (!mapData) return;

  currentMap = mapData;

  // Actualizar visibilidad del botón de insertar acceso a padre
  const btnEnsureParent = document.getElementById('btn-ensure-parent-node');
  if (btnEnsureParent) {
    btnEnsureParent.style.display = (currentMap && currentMap.parent_map_id) ? 'inline-flex' : 'none';
  }

  // Actualizar Breadcrumb
  const crumbs = await API.getMapBreadcrumb(mapId);
  renderBreadcrumbs(crumbs);

  // Renderizar enlaces primero (capa inferior)
  const nodesDict = new Map();
  mapData.nodes.forEach(n => nodesDict.set(n.id, n));
  mapData.links.forEach(l => renderLink(l, nodesDict));

  // Renderizar nodos (capa superior)
  mapData.nodes.forEach(n => renderNode(n));

  // Recalcular posiciones y etiquetas de enlaces con los nodos y cajas ya creados
  updateAllLinks();

  linksLayer.batchDraw();
  nodesLayer.batchDraw();
  refreshMapsTabList();
  checkMapCanPopulateFromSite();

  // Arrancar polling de estado en tiempo real para este mapa
  startRealtimePolling(mapId);

  // Persistir mapa actual y sincronizar estado en URL
  localStorage.setItem('nexusdude_last_map_id', mapId);
  updateUrlHashState();
}

function renderBreadcrumbs(crumbs) {
  const bar = document.getElementById('breadcrumb-bar');
  bar.innerHTML = '';

  if (!crumbs || crumbs.length === 0) {
    bar.innerHTML = `<div class="breadcrumb-item active"><i class="fas fa-sitemap"></i> <span>${currentMap.name}</span></div>`;
    return;
  }

  crumbs.forEach((crumb, idx) => {
    const isLast = idx === crumbs.length - 1;
    const item = document.createElement('div');
    item.className = `breadcrumb-item ${isLast ? 'active' : ''}`;
    item.innerHTML = `<i class="fas ${idx === 0 ? 'fa-home' : 'fa-folder'}"></i> <span>${crumb.name}</span>`;

    if (!isLast) {
      item.addEventListener('click', () => loadMap(crumb.id));
    }
    bar.appendChild(item);

    if (!isLast) {
      const sep = document.createElement('span');
      sep.className = 'breadcrumb-sep';
      sep.textContent = '>';
      bar.appendChild(sep);
    }
  });
}

// ─── 8. Carga y Filtro de Inventario NetBox ──────────────────────────────────
let searchTimeout = null;

async function loadInventoryFilters() {
  const [sites, roles] = await Promise.all([
    API.getInventorySites(),
    API.getInventoryRoles()
  ]);

  // Cargar mapa dinámico de colores de roles oficiales de NetBox
  if (Array.isArray(roles)) {
    roles.forEach(r => {
      if (r.name && r.color) {
        netboxRoleColorsMap.set(r.name.toLowerCase().trim(), r.color.startsWith('#') ? r.color : `#${r.color}`);
      }
      if (r.slug && r.color) {
        netboxRoleColorsMap.set(r.slug.toLowerCase().trim(), r.color.startsWith('#') ? r.color : `#${r.color}`);
      }
    });
  }

  const selectSite = document.getElementById('select-site');
  selectSite.innerHTML = '<option value="">🏢 Todos los Sitios</option>';
  sites.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.name;
    opt.textContent = s.name;
    selectSite.appendChild(opt);
  });

  const selectRole = document.getElementById('select-role');
  selectRole.innerHTML = '<option value="">🏷️ Todos los Roles</option>';
  roles.forEach(r => {
    const opt = document.createElement('option');
    opt.value = r.name;
    opt.textContent = r.name;
    selectRole.appendChild(opt);
  });
}

async function triggerSearch() {
  const query = document.getElementById('input-search-devices').value;
  const site = document.getElementById('select-site').value;
  const role = document.getElementById('select-role').value;

  document.getElementById('results-count').textContent = 'Buscando en NetBox...';

  const data = await API.searchInventory({
    query,
    site,
    role,
    limit: 60
  });

  renderDeviceList(data.results || [], data.total || 0);
}

function renderDeviceList(devices, total) {
  const list = document.getElementById('device-list');
  list.innerHTML = '';
  document.getElementById('results-count').textContent = `${total} equipo${total === 1 ? '' : 's'} encontrado${total === 1 ? '' : 's'}`;

  if (devices.length === 0) {
    list.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 20px;">No se encontraron equipos con los filtros seleccionados.</div>';
    return;
  }

  devices.forEach(dev => {
    const item = document.createElement('div');
    item.className = 'device-item';
    item.draggable = true;

    const roleNameLower = (dev.role || '').toLowerCase().trim();
    const rColor = dev.role_color || netboxRoleColorsMap.get(roleNameLower) || DEFAULT_NETBOX_ROLE_COLORS[roleNameLower] || '';
    const rHex = rColor ? (rColor.startsWith('#') ? rColor : `#${rColor}`) : '';
    const rStyle = rHex ? `style="background: ${hexToRgba(rHex, 0.22)}; border: 1px solid ${rHex}; color: ${rHex};"` : '';

    item.innerHTML = `
      <div class="device-head">
        <span class="device-title" title="${dev.name}">${dev.name}</span>
        <button class="device-btn-add" title="Agregar al centro del lienzo">
          <i class="fas fa-plus-circle"></i>
        </button>
      </div>
      <div class="device-ip">${dev.ip || 'Sin IP'}</div>
      <div class="device-meta">
        <span class="meta-pill role" ${rStyle}>${dev.role}</span>
        <span class="meta-pill site">${dev.site}</span>
        ${dev.model ? `<span class="meta-pill">${dev.model}</span>` : ''}
      </div>
    `;

    // Soporte Drag & Drop
    item.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('application/json', JSON.stringify({ ...dev, role_color: rHex }));
      e.dataTransfer.effectAllowed = 'copy';
    });

    // Clic en el botón "+" para agregar al centro de la vista
    item.querySelector('.device-btn-add').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!currentMap) return;

      const centerX = (-stage.x() + stage.width() / 2) / stage.scaleX();
      const centerY = (-stage.y() + stage.height() / 2) / stage.scaleY();
      const snapX = snapToGrid ? Math.round(centerX / GRID_SIZE) * GRID_SIZE : centerX;
      const snapY = snapToGrid ? Math.round(centerY / GRID_SIZE) * GRID_SIZE : centerY;

      try {
        const newNode = await API.createNode({
          map_id: currentMap.id,
          device_id: dev.id,
          name: dev.name,
          ip: dev.ip,
          device_type: dev.role,
          site_name: dev.site,
          x: snapX,
          y: snapY,
          status: 'ok',
          extra_data: {
            manufacturer: dev.manufacturer || 'Genérico',
            model: dev.model || '',
            serial: dev.serial || '',
            role: dev.role || dev.device_type || 'Dispositivo',
            role_color: rHex,
            status: dev.status || 'active'
          }
        });
        currentMap.nodes.push(newNode);
        renderNode(newNode);
        nodesLayer.batchDraw();
        selectNode(newNode);
      } catch (err) {
        alert('Error agregando nodo: ' + err.message);
      }
    });

    list.appendChild(item);
  });
}

// ─── 9. Pestaña de Mapas y Jerarquía Estilo Gestor de Archivos ───────────────
let cachedMaps = [];
const expandedMapIds = new Set();

// Inicializar mapas expandidos desde localStorage
try {
  const savedExpanded = localStorage.getItem('nexusdude_expanded_map_ids');
  if (savedExpanded) {
    JSON.parse(savedExpanded).forEach(id => expandedMapIds.add(id));
  } else {
    expandedMapIds.add('default-map');
  }
} catch (e) {
  expandedMapIds.add('default-map');
}

function saveExpandedMapIds() {
  try {
    localStorage.setItem('nexusdude_expanded_map_ids', JSON.stringify(Array.from(expandedMapIds)));
  } catch (e) {}
}

function autoExpandAncestors(mapId) {
  if (!mapId || !Array.isArray(cachedMaps)) return;
  const mapDict = new Map(cachedMaps.map(m => [m.id, m]));
  let cur = mapDict.get(mapId);
  while (cur && cur.parent_map_id) {
    expandedMapIds.add(cur.parent_map_id);
    cur = mapDict.get(cur.parent_map_id);
  }
  saveExpandedMapIds();
}

async function refreshMapsTabList(filterText = '') {
  window.loadMapsTree = refreshMapsTabList;
  cachedMaps = await API.getMaps();
  const treeContainer = document.getElementById('maps-tree');
  if (!treeContainer) return;
  treeContainer.innerHTML = '';

  updateParentMapSelectOptions();

  // Si hay un mapa activo, expandir sus ancestros para que sea inmediatamente visible
  if (currentMap) {
    autoExpandAncestors(currentMap.id);
  }

  // Filtrar mapas si hay texto de búsqueda
  const isSearching = Boolean(filterText && filterText.trim());
  let filtered = cachedMaps;
  if (isSearching) {
    const q = filterText.trim().toLowerCase();
    filtered = cachedMaps.filter(m => 
      (m.name || '').toLowerCase().includes(q) || 
      (m.description && m.description.toLowerCase().includes(q))
    );
  }

  // Actualizar visibilidad del botón de limpiar búsqueda
  const btnClearSearch = document.getElementById('btn-clear-search-maps');
  if (btnClearSearch) {
    btnClearSearch.style.display = isSearching ? 'block' : 'none';
  }

  if (filtered.length === 0) {
    treeContainer.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 20px;"><i class="fas fa-search" style="margin-bottom: 6px; display: block; opacity: 0.5;"></i>No se encontraron mapas con ese nombre.</div>';
    return;
  }

  // Agrupar mapas por parent_map_id
  const childrenMap = new Map();
  const allIds = new Set(cachedMaps.map(m => m.id));

  cachedMaps.forEach(m => {
    const pId = m.parent_map_id;
    if (pId && allIds.has(pId)) {
      if (!childrenMap.has(pId)) childrenMap.set(pId, []);
      childrenMap.get(pId).push(m);
    }
  });

  const rootMaps = cachedMaps.filter(m => !m.parent_map_id || !allIds.has(m.parent_map_id));

  // Función recursiva para renderizar los mapas como tarjetas jerárquicas desplegables
  function renderMapNode(mapObj, level = 0, includeChildren = true, visited = new Set()) {
    if (visited.has(mapObj.id)) return document.createDocumentFragment();
    visited.add(mapObj.id);

    const wrapper = document.createElement('div');
    wrapper.className = 'map-node-wrapper';
    wrapper.dataset.mapId = mapObj.id;

    const children = childrenMap.get(mapObj.id) || [];
    const hasChildren = children.length > 0;
    const isExpanded = isSearching || expandedMapIds.has(mapObj.id);
    const isSubmap = level > 0 || Boolean(mapObj.parent_map_id);
    const isActive = currentMap && currentMap.id === mapObj.id;
    const isDefault = mapObj.id === 'default-map';

    const card = document.createElement('div');
    card.className = `map-item-card ${isSubmap ? 'is-submap' : ''} ${isActive ? 'active' : ''}`;

    let iconClass = 'fa-sitemap';
    let iconExtraClass = '';
    if (isSubmap) {
      iconClass = isExpanded ? 'fa-folder-open' : 'fa-folder';
      iconExtraClass = isExpanded ? 'submap open' : 'submap';
    }

    card.innerHTML = `
      <div class="map-card-head">
        <div class="map-card-title-group" title="Hacer clic para abrir este mapa en el lienzo">
          ${hasChildren ? `
          <button type="button" class="map-toggle-caret" title="${isExpanded ? 'Contraer submapas' : 'Desplegar submapas'}">
            <i class="fas ${isExpanded ? 'fa-chevron-down' : 'fa-chevron-right'}"></i>
          </button>` : ''}
          <i class="fas ${iconClass} map-card-icon ${iconExtraClass}"></i>
          <span class="map-card-name">${mapObj.name}</span>
          ${hasChildren ? `<span class="map-child-count-pill">${children.length} submapas</span>` : ''}
        </div>
        ${isActive ? '<span class="map-active-badge"><i class="fas fa-check"></i> Activo</span>' : ''}
      </div>

      <div class="map-card-meta">
        <div class="map-card-stats">
          <span><i class="fas fa-server"></i> ${mapObj.nodes_count || 0} nodos</span>
          <span><i class="fas fa-project-diagram"></i> ${mapObj.links_count || 0} enlaces</span>
        </div>
        <div class="map-actions">
          <button type="button" class="map-action-btn open-btn" title="Cargar este mapa en el lienzo">
            <i class="fas fa-eye"></i>
          </button>
          <button type="button" class="map-action-btn add-sub-btn" title="Crear submapa hijo de este mapa">
            <i class="fas fa-folder-plus"></i>
          </button>
          <button type="button" class="map-action-btn populate-btn" title="Poblar o sincronizar equipos desde NetBox">
            <i class="fas fa-magic"></i>
          </button>
          <button type="button" class="map-action-btn edit-btn" title="Editar propiedades del mapa">
            <i class="fas fa-pen"></i>
          </button>
          ${!isDefault ? `
          <button type="button" class="map-action-btn delete-btn" title="Eliminar mapa">
            <i class="fas fa-trash-alt"></i>
          </button>` : ''}
        </div>
      </div>
    `;

    wrapper.appendChild(card);

    // Contenedor de submapas hijos con animación y borde púrpura
    let childContainer = null;
    if (hasChildren && includeChildren) {
      childContainer = document.createElement('div');
      childContainer.className = 'map-children-container';
      childContainer.style.display = isExpanded ? 'flex' : 'none';

      children.forEach(child => {
        childContainer.appendChild(renderMapNode(child, level + 1, true, new Set(visited)));
      });
      wrapper.appendChild(childContainer);
    }

    // Toggle expand/collapse function
    const toggleExpand = (e) => {
      if (e) e.stopPropagation();
      if (!hasChildren) return;

      const currentlyOpen = expandedMapIds.has(mapObj.id);
      if (currentlyOpen) {
        expandedMapIds.delete(mapObj.id);
      } else {
        expandedMapIds.add(mapObj.id);
      }
      saveExpandedMapIds();

      const newOpen = expandedMapIds.has(mapObj.id);
      if (childContainer) {
        childContainer.style.display = newOpen ? 'flex' : 'none';
      }

      const caretBtn = card.querySelector('.map-toggle-caret');
      if (caretBtn) {
        caretBtn.innerHTML = `<i class="fas ${newOpen ? 'fa-chevron-down' : 'fa-chevron-right'}"></i>`;
        caretBtn.title = newOpen ? 'Contraer submapas' : 'Desplegar submapas';
      }

      const iconEl = card.querySelector('.map-card-icon');
      if (iconEl && isSubmap) {
        iconEl.className = `fas ${newOpen ? 'fa-folder-open' : 'fa-folder'} map-card-icon ${newOpen ? 'submap open' : 'submap'}`;
      }
    };

    // Caret click handler
    const caretBtn = card.querySelector('.map-toggle-caret');
    if (caretBtn) {
      caretBtn.addEventListener('click', toggleExpand);
    }

    // Clic en el título para abrir mapa en el lienzo
    card.querySelector('.map-card-title-group').addEventListener('click', (e) => {
      if (e.target.closest('.map-toggle-caret')) return;
      loadMap(mapObj.id);
    });

    // Doble clic en el título para desplegar/contraer
    card.querySelector('.map-card-title-group').addEventListener('dblclick', (e) => {
      if (e.target.closest('.map-toggle-caret')) return;
      if (hasChildren) toggleExpand(e);
    });

    // Botón abrir
    card.querySelector('.open-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      loadMap(mapObj.id);
    });

    // Botón crear submapa
    card.querySelector('.add-sub-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      openCreateMapModal(true, mapObj.id);
    });

    // Botón poblar desde NetBox
    const popBtn = card.querySelector('.populate-btn');
    if (popBtn) {
      popBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`¿Deseas poblar o sincronizar los equipos de NetBox para el mapa "${mapObj.name}"?`)) return;
        try {
          const res = await API.populateMapFromSite(mapObj.id, mapObj.name);
          alert(res.message);
          if (currentMap && currentMap.id === mapObj.id) {
            await loadMap(mapObj.id);
          } else {
            await refreshMapsTabList();
          }
        } catch (err) {
          alert('Error sincronizando: ' + err.message);
        }
      });
    }

    // Botón editar mapa
    card.querySelector('.edit-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      openEditMapModal(mapObj);
    });

    // Botón eliminar mapa
    const delBtn = card.querySelector('.delete-btn');
    if (delBtn) {
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        handleDeleteMap(mapObj.id, mapObj.name);
      });
    }

    return wrapper;
  }

  if (isSearching) {
    // Modo búsqueda: mostrar directamente todos los mapas coincidentes
    filtered.forEach(m => {
      treeContainer.appendChild(renderMapNode(m, 0, false));
    });
  } else {
    // Modo jerárquico: mostrar desde las raíces
    rootMaps.forEach(root => {
      treeContainer.appendChild(renderMapNode(root, 0, true));
    });
  }
}

function updateParentMapSelectOptions(excludeMapId = null) {
  const selectParent = document.getElementById('select-map-parent');
  if (!selectParent) return;

  const currentVal = selectParent.value;
  selectParent.innerHTML = '<option value="">🌐 Ninguno (Mapa Raíz Principal)</option>';

  cachedMaps.forEach(m => {
    if (excludeMapId && m.id === excludeMapId) return;
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.parent_map_id ? '  ↳ 📁 ' : '🌐 '}${m.name}`;
    selectParent.appendChild(opt);
  });

  if (currentVal) selectParent.value = currentVal;
}

// ─── 10. Modales de Creación y Edición de Mapas (CRUD) ───────────────────────
let currentModalMode = 'site';
let cachedSitesSummary = [];

function setModalMode(mode) {
  currentModalMode = mode;
  const btnSite = document.getElementById('btn-mode-site');
  const btnManual = document.getElementById('btn-mode-manual');
  const rowSitePicker = document.getElementById('row-site-picker');
  const rowAutoPopulate = document.getElementById('row-auto-populate');

  if (btnSite) btnSite.classList.toggle('active', mode === 'site');
  if (btnManual) btnManual.classList.toggle('active', mode === 'manual');

  if (mode === 'site') {
    if (rowSitePicker) rowSitePicker.style.display = 'block';
    if (rowAutoPopulate) rowAutoPopulate.style.display = 'block';
  } else {
    if (rowSitePicker) rowSitePicker.style.display = 'none';
    if (rowAutoPopulate) rowAutoPopulate.style.display = 'none';
  }
}

async function checkMapCanPopulateFromSite() {
  const btnPopulate = document.getElementById('btn-populate-current-map');
  if (!btnPopulate || !currentMap) return;

  if (cachedSitesSummary.length === 0) {
    cachedSitesSummary = await API.getSitesSummary();
  }

  const mapNameLower = currentMap.name.trim().toLowerCase();
  const matchedSite = cachedSitesSummary.find(s =>
    s.name.toLowerCase() === mapNameLower ||
    mapNameLower.includes(s.name.toLowerCase()) ||
    s.name.toLowerCase().includes(mapNameLower)
  );

  if (matchedSite && matchedSite.device_count > 0) {
    btnPopulate.style.display = 'inline-flex';
    document.getElementById('btn-populate-text').textContent = `Poblar "${matchedSite.name}" (${matchedSite.device_count} eq)`;
    btnPopulate.title = `Importar automáticamente los ${matchedSite.device_count} equipos del sitio NetBox "${matchedSite.name}"`;
    btnPopulate.dataset.siteName = matchedSite.name;
  } else {
    btnPopulate.style.display = 'none';
  }
}

function renderSiteOptionsForSubmap(filterText = '') {
  const selectSiteSubmap = document.getElementById('select-site-for-submap');
  const badge = document.getElementById('site-submap-count-badge');
  if (!selectSiteSubmap) return;

  const query = (filterText || '').trim().toLowerCase();
  const filtered = cachedSitesSummary.filter(s => {
    if (!query) return true;
    return s.name.toLowerCase().includes(query);
  });

  selectSiteSubmap.innerHTML = '';
  if (filtered.length === 0) {
    selectSiteSubmap.innerHTML = '<option value="">No se encontraron sitios con ese filtro</option>';
  } else {
    filtered.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.name;
      opt.textContent = `${s.name} (${s.device_count} equipos)`;
      opt.dataset.count = s.device_count;
      selectSiteSubmap.appendChild(opt);
    });
  }

  if (badge) {
    badge.textContent = `${filtered.length} de ${cachedSitesSummary.length} sitios`;
  }
}

async function openCreateMapModal(isSubmap = false, parentId = null, preferSiteMode = false) {
  updateParentMapSelectOptions();
  const modal = document.getElementById('modal-map');
  const title = document.getElementById('modal-map-title');
  const inputId = document.getElementById('input-edit-map-id');
  const inputName = document.getElementById('input-new-map-name');
  const inputDesc = document.getElementById('input-new-map-desc');
  const selectGrid = document.getElementById('select-new-map-grid');
  const selectParent = document.getElementById('select-map-parent');
  const rowInsert = document.getElementById('row-insert-submap-node');
  const rowModeTabs = document.getElementById('row-modal-mode-tabs');
  const selectSiteSubmap = document.getElementById('select-site-for-submap');
  const btnConfirm = document.getElementById('btn-confirm-save-map');

  inputId.value = '';
  inputName.value = '';
  inputDesc.value = '';
  selectGrid.value = '20';

  // Cargar sitios NetBox en caché
  if (cachedSitesSummary.length === 0) {
    cachedSitesSummary = await API.getSitesSummary();
  }

  const inputFilterSite = document.getElementById('input-filter-site-submap');
  if (inputFilterSite) inputFilterSite.value = '';
  renderSiteOptionsForSubmap('');

  const parentTargetId = parentId || (isSubmap ? currentMap?.id : '') || '';
  selectParent.value = parentTargetId;

  if (isSubmap) {
    title.textContent = 'Nuevo Submapa';
    rowInsert.style.display = 'block';
    document.getElementById('check-insert-submap-node').checked = true;
    if (rowModeTabs) rowModeTabs.style.display = 'flex';
    setModalMode('site');
  } else {
    title.textContent = 'Nuevo Mapa Raíz';
    rowInsert.style.display = 'none';
    if (rowModeTabs) rowModeTabs.style.display = 'flex';
    setModalMode(preferSiteMode ? 'site' : 'manual');
  }

  btnConfirm.innerHTML = '<i class="fas fa-plus"></i> Crear Mapa';
  modal.style.display = 'flex';
}

function openEditMapModal(mapObj) {
  updateParentMapSelectOptions(mapObj.id);
  const modal = document.getElementById('modal-map');
  const title = document.getElementById('modal-map-title');
  const inputId = document.getElementById('input-edit-map-id');
  const inputName = document.getElementById('input-new-map-name');
  const inputDesc = document.getElementById('input-new-map-desc');
  const selectGrid = document.getElementById('select-new-map-grid');
  const selectParent = document.getElementById('select-map-parent');
  const rowInsert = document.getElementById('row-insert-submap-node');
  const rowModeTabs = document.getElementById('row-modal-mode-tabs');
  const rowSitePicker = document.getElementById('row-site-picker');
  const rowAutoPopulate = document.getElementById('row-auto-populate');
  const btnConfirm = document.getElementById('btn-confirm-save-map');

  inputId.value = mapObj.id;
  inputName.value = mapObj.name;
  inputDesc.value = mapObj.description || '';
  selectGrid.value = String(mapObj.grid_size || 20);
  selectParent.value = mapObj.parent_map_id || '';
  rowInsert.style.display = 'none';
  if (rowModeTabs) rowModeTabs.style.display = 'none';
  if (rowSitePicker) rowSitePicker.style.display = 'none';
  if (rowAutoPopulate) rowAutoPopulate.style.display = 'none';

  title.textContent = `Editar Mapa: ${mapObj.name}`;
  btnConfirm.innerHTML = '<i class="fas fa-save"></i> Guardar Cambios';
  modal.style.display = 'flex';
  inputName.focus();
}

async function handleSaveMap() {
  const editId = document.getElementById('input-edit-map-id').value;
  const name = document.getElementById('input-new-map-name').value.trim();
  const desc = document.getElementById('input-new-map-desc').value.trim();
  const grid = parseInt(document.getElementById('select-new-map-grid').value, 10) || 20;
  const parentId = document.getElementById('select-map-parent').value || null;
  const insertSubmapNode = document.getElementById('check-insert-submap-node').checked;
  const checkAutoPopulate = document.getElementById('check-auto-populate-devices');
  const autoPopulate = checkAutoPopulate ? checkAutoPopulate.checked : true;
  const selectSiteSubmap = document.getElementById('select-site-for-submap');
  const selectedSite = selectSiteSubmap ? selectSiteSubmap.value : '';

  if (currentModalMode === 'site' && !editId) {
    if (!selectedSite) {
      alert('Por favor selecciona un Sitio de NetBox de la lista.');
      return;
    }
  } else {
    if (!name) {
      alert('Por favor introduce un nombre para el mapa.');
      return;
    }
  }

  try {
    if (editId) {
      // MODO EDICIÓN
      const updated = await API.updateMap(editId, {
        name: name,
        description: desc,
        grid_size: grid,
        parent_map_id: parentId
      });

      if (currentMap) {
        await loadMap(currentMap.id);
      }
    } else if (currentModalMode === 'site') {
      // MODO CREACIÓN DESDE SITIO NETBOX
      const centerX = (-stage.x() + stage.width() / 2) / stage.scaleX();
      const centerY = (-stage.y() + stage.height() / 2) / stage.scaleY();
      const snapX = snapToGrid ? Math.round(centerX / GRID_SIZE) * GRID_SIZE : centerX;
      const snapY = snapToGrid ? Math.round(centerY / GRID_SIZE) * GRID_SIZE : centerY;

      const res = await API.createMapFromSite({
        site_name: selectedSite,
        parent_map_id: parentId,
        insert_submap_node: insertSubmapNode,
        x: snapX,
        y: snapY,
        auto_populate: autoPopulate
      });

      // Si insertó un nodo en el mapa activo actual, recargar el mapa para renderizarlo con toda la sincronización
      if (parentId && currentMap && currentMap.id === parentId) {
        await loadMap(currentMap.id);
      }

      if (!parentId) {
        await loadMap(res.map.id);
      } else {
        const goNow = confirm(`Submapa "${selectedSite}" creado con éxito con ${res.devices_count} equipos.\n\n¿Deseas abrir el submapa ahora?`);
        if (goNow) {
          await loadMap(res.map.id);
        }
      }
    } else {
      // MODO CREACIÓN MANUAL EN BLANCO
      const newMap = await API.createMap({
        name: name,
        description: desc,
        parent_map_id: parentId,
        grid_size: grid
      });

      if (parentId && currentMap && currentMap.id === parentId) {
        await loadMap(currentMap.id);
      } else if (!parentId) {
        await loadMap(newMap.id);
      }
    }

    document.getElementById('modal-map').style.display = 'none';
    await refreshMapsTabList();

  } catch (err) {
    alert('Error procesando el mapa: ' + err.message);
  }
}

async function handleDeleteMap(mapId, mapName) {
  if (mapId === 'default-map') {
    alert('El mapa principal del sistema no puede eliminarse.');
    return;
  }

  const ok = confirm(`¿Estás seguro de eliminar el mapa "${mapName}"?\n\nNota: Los submapas que dependían de este mapa no serán eliminados; quedarán preservados como mapas independientes y solo se desvinculará la relación.`);
  if (!ok) return;

  try {
    const res = await API.deleteMap(mapId);
    if (res) {
      if (currentMap && (currentMap.id === mapId || currentMap.parent_map_id === mapId)) {
        await loadMap('default-map');
      } else if (currentMap) {
        await loadMap(currentMap.id);
      }
      await refreshMapsTabList();
    }
  } catch (err) {
    alert('Error eliminando mapa: ' + err.message);
  }
}

// ─── 11. Gestión de Pestañas (Sidebar Tabs) ──────────────────────────────────
function switchTab(tabId) {
  if (!tabId) tabId = 'tab-maps';
  localStorage.setItem('nexusdude_active_tab', tabId);

  document.querySelectorAll('.sidebar-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });
  document.querySelectorAll('.sidebar-body').forEach(b => {
    b.style.display = b.id === tabId ? 'flex' : 'none';
  });
  if (tabId === 'tab-spectrum') {
    handleSpectrumTabActivated();
  }
  updateUrlHashState();
}

// ─── 11.2. ANALIZADOR DE ESPECTRO RF & REGLA DE FRECUENCIAS (4850 - 7250 MHz) ─
let spectrumData = null;
const spectrumSelectedDeviceIds = new Set();
let spectrumSidebarZoom = 1.0;
let spectrumModalZoom = 1.0;
let spectrumFilterText = '';
let spectrumRoleFilter = 'all';

const MIN_SPEC_FREQ = 4850;
const MAX_SPEC_FREQ = 7250;
const SPAN_SPEC_FREQ = MAX_SPEC_FREQ - MIN_SPEC_FREQ; // 2400 MHz

const STANDARD_RF_BANDS = [
  { name: '5.1 GHz (UNII-1)', start: 5150, end: 5250, cls: 'unii1_3', label: 'UNII-1 (5.15-5.25 GHz)' },
  { name: '5.3 GHz (UNII-2A)', start: 5250, end: 5350, cls: 'unii1_3', label: 'UNII-2A (5.25-5.35 GHz)' },
  { name: '5.5 GHz (UNII-2C DFS)', start: 5470, end: 5725, cls: 'unii1_3', label: 'UNII-2C DFS (5.47-5.72 GHz)' },
  { name: '5.8 GHz (UNII-3)', start: 5725, end: 5850, cls: 'unii1_3', label: 'UNII-3 (5.72-5.85 GHz)' },
  { name: '5.9 GHz (UNII-4)', start: 5850, end: 5925, cls: 'unii4', label: 'UNII-4 (5.85-5.92 GHz)' },
  { name: '6.0 GHz (UNII-5)', start: 5925, end: 6425, cls: 'unii5_8', label: 'UNII-5 (5.92-6.42 GHz)' },
  { name: '6.5 GHz (UNII-6)', start: 6425, end: 6525, cls: 'unii5_8', label: 'UNII-6 (6.42-6.52 GHz)' },
  { name: '6.7 GHz (UNII-7)', start: 6525, end: 6875, cls: 'unii5_8', label: 'UNII-7 (6.52-6.87 GHz)' },
  { name: '7.0 GHz (UNII-8)', start: 6875, end: 7125, cls: 'unii5_8', label: 'UNII-8 (6.87-7.12 GHz)' },
];

function handleSpectrumTabActivated() {
  populateSpectrumMapSelector();
  const selectMap = document.getElementById('select-spectrum-map');
  const targetMap = (selectMap && selectMap.value && selectMap.value !== 'current') ? selectMap.value : (currentMap ? currentMap.id : 'default-map');
  loadSpectrumData(targetMap);
}

function populateSpectrumMapSelector() {
  const selectMap = document.getElementById('select-spectrum-map');
  if (!selectMap) return;

  const currentVal = selectMap.value || 'current';
  const mapsOptions = allMaps.map(m => `<option value="${m.id}">${m.name} (${m.nodes_count || 0} nodos)</option>`).join('');

  selectMap.innerHTML = `
    <option value="current">📍 Mapa Actual (${currentMap ? currentMap.name : 'Lienzo'})</option>
    <option value="all">🌐 Todos los Mapas (Global)</option>
    ${mapsOptions}
  `;
  selectMap.value = currentVal;
}

async function loadSpectrumData(mapId = null) {
  const listEl = document.getElementById('spectrum-devices-list');
  if (listEl) {
    listEl.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); font-size: 0.75rem; padding: 20px 0;">
        <i class="fas fa-spinner fa-spin"></i> Consultando telemetría de radio en Zabbix...
      </div>
    `;
  }

  const actualMapId = (!mapId || mapId === 'current') ? (currentMap ? currentMap.id : 'default-map') : mapId;

  try {
    const res = await API.getMapSpectrum(actualMapId);
    spectrumData = res;

    // Inicializar selección con todos los dispositivos activos
    spectrumSelectedDeviceIds.clear();
    (spectrumData.devices || []).forEach(d => spectrumSelectedDeviceIds.add(d.node_id));

    // Actualizar badges de resumen
    const totalEl = document.getElementById('spec-stat-total');
    if (totalEl) totalEl.textContent = (spectrumData.devices || []).length;

    const bws = new Set((spectrumData.devices || []).map(d => `${d.bandwidth_mhz}M`));
    const bwsEl = document.getElementById('spec-stat-active-bws');
    if (bwsEl) bwsEl.textContent = bws.size > 0 ? Array.from(bws).join(', ') : '0';

    const countLbl = document.getElementById('spectrum-device-count-lbl');
    if (countLbl) countLbl.textContent = (spectrumData.devices || []).length;

    // Renderizar lista y regla
    renderSpectrumDeviceList();
    renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);

  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `
        <div style="text-align: center; color: #ef4444; font-size: 0.75rem; padding: 15px 0;">
          <i class="fas fa-exclamation-triangle"></i> Error: ${err.message}
        </div>
      `;
    }
  }
}

function renderSpectrumDeviceList() {
  const listEl = document.getElementById('spectrum-devices-list');
  if (!listEl || !spectrumData) return;

  const q = spectrumFilterText.toLowerCase().trim();
  const roleF = spectrumRoleFilter;

  const filtered = (spectrumData.devices || []).filter(d => {
    if (q) {
      const matchName = (d.name || '').toLowerCase().includes(q);
      const matchIp = (d.ip || '').toLowerCase().includes(q);
      const matchFreq = String(d.frequency_mhz || '').includes(q);
      const matchBw = (d.bandwidth_text || '').toLowerCase().includes(q);
      if (!matchName && !matchIp && !matchFreq && !matchBw) return false;
    }
    if (roleF === 'ap') {
      const isAp = (d.name || '').toLowerCase().startsWith('ap_') || (d.name || '').toLowerCase().startsWith('sec_') || d.device_type === 'ap' || d.device_type === 'radio';
      if (!isAp) return false;
    } else if (roleF === 'cpe') {
      const isCpe = (d.name || '').toLowerCase().startsWith('cpe_') || d.device_type === 'cpe';
      if (!isCpe) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    listEl.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); font-size: 0.74rem; padding: 15px 0;">
        No se encontraron equipos inalámbricos que coincidan con los filtros.
      </div>
    `;
    return;
  }

  listEl.innerHTML = filtered.map(d => {
    const isSelected = spectrumSelectedDeviceIds.has(d.node_id);
    const isAp = (d.name || '').toLowerCase().startsWith('ap_') || (d.name || '').toLowerCase().startsWith('sec_') || d.device_type === 'ap';
    const roleIcon = isAp ? '📡' : '📶';
    const bwColor = d.bandwidth_mhz >= 80 ? '#c084fc' : (d.bandwidth_mhz >= 40 ? '#38bdf8' : '#94a3b8');

    return `
      <div class="spectrum-device-item ${isSelected ? 'active' : ''}" data-node-id="${d.node_id}">
        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; flex: 1; overflow: hidden; min-width: 0;">
          <input type="checkbox" class="chk-spec-device" data-node-id="${d.node_id}" ${isSelected ? 'checked' : ''} style="cursor: pointer; flex-shrink: 0; width: 14px; height: 14px;">
          <span style="font-size: 0.92rem;">${roleIcon}</span>
          <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">
            <div style="font-size: 0.86rem; font-weight: 700; color: #f1f5f9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${d.name}">
              ${d.name}
            </div>
            <div style="font-size: 0.76rem; color: #94a3b8; font-family: monospace;">
              ${d.ip || 'Sin IP'} · <span style="color: ${bwColor}; font-weight: 600;">${d.bandwidth_text}</span>
            </div>
          </div>
        </label>
        <div style="text-align: right; flex-shrink: 0; padding-left: 6px;">
          <span style="font-size: 0.86rem; font-weight: 800; color: #38bdf8; font-family: monospace; display: block;">
            ${d.frequency_mhz} MHz
          </span>
          <span style="font-size: 0.74rem; color: #64748b; font-family: monospace;">
            ${d.freq_start_mhz} - ${d.freq_end_mhz}
          </span>
        </div>
      </div>
    `;
  }).join('');

  listEl.querySelectorAll('.chk-spec-device').forEach(chk => {
    chk.addEventListener('change', () => {
      const nid = chk.dataset.nodeId;
      if (chk.checked) {
        spectrumSelectedDeviceIds.add(nid);
      } else {
        spectrumSelectedDeviceIds.delete(nid);
      }
      const itemEl = chk.closest('.spectrum-device-item');
      if (itemEl) itemEl.classList.toggle('active', chk.checked);

      renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
      const modalCanvas = document.getElementById('spectrum-modal-ruler-canvas');
      if (modalCanvas && document.getElementById('modal-spectrum-fullscreen').style.display === 'flex') {
        renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
      }
    });
  });
}

function renderSpectrumRuler(canvasContainerId, isModal = false) {
  const container = document.getElementById(canvasContainerId);
  if (!container || !spectrumData) return;

  const zoom = isModal ? spectrumModalZoom : spectrumSidebarZoom;
  const baseMinWidth = isModal ? 2400 : 1250;
  const totalWidthPx = Math.round(baseMinWidth * zoom);
  container.style.width = `${totalWidthPx}px`;

  const activeDevices = (spectrumData.devices || []).filter(d => spectrumSelectedDeviceIds.has(d.node_id));

  // Generar marcas de la regla (Ticks cada 20, 50, 100 MHz)
  let ticksHtml = '';
  for (let f = MIN_SPEC_FREQ; f <= MAX_SPEC_FREQ; f += 20) {
    const isMajor = (f % 100 === 0);
    const isMedium = (!isMajor && f % 50 === 0);
    const tickClass = isMajor ? 'major' : (isMedium ? 'medium' : 'minor');
    const leftPercent = ((f - MIN_SPEC_FREQ) / SPAN_SPEC_FREQ) * 100;

    let labelHtml = '';
    if (isMajor) {
      labelHtml = `<span class="spectrum-tick-label" style="left: ${leftPercent}%;">${f}</span>`;
    }
    ticksHtml += `
      <div class="spectrum-tick ${tickClass}" style="left: ${leftPercent}%;"></div>
      ${labelHtml}
    `;
  }

  // Generar zonas de bandas estándar (UNII-1 a UNII-8)
  const bandsHtml = STANDARD_RF_BANDS.map(b => {
    if (b.end < MIN_SPEC_FREQ || b.start > MAX_SPEC_FREQ) return '';
    const startClamped = Math.max(b.start, MIN_SPEC_FREQ);
    const endClamped = Math.min(b.end, MAX_SPEC_FREQ);
    const leftPct = ((startClamped - MIN_SPEC_FREQ) / SPAN_SPEC_FREQ) * 100;
    const widthPct = ((endClamped - startClamped) / SPAN_SPEC_FREQ) * 100;
    const bandFontSize = isModal ? '0.70rem' : '0.58rem';
    return `
      <div class="spectrum-band-zone ${b.cls}" style="left: ${leftPct}%; width: ${widthPct}%;" title="${b.label}">
        <span style="position: absolute; top: 2px; left: 4px; font-size: ${bandFontSize}; font-weight: 700; color: rgba(255,255,255,0.3); text-transform: uppercase; pointer-events: none;">
          ${b.name}
        </span>
      </div>
    `;
  }).join('');

  // Algoritmo de asignación de pistas/carriles para canales solapados
  const lanes = [];
  const deviceLanes = [];

  const sorted = [...activeDevices].sort((a, b) => a.freq_start_mhz - b.freq_start_mhz);

  sorted.forEach(dev => {
    let assignedLane = -1;
    for (let l = 0; l < lanes.length; l++) {
      if (lanes[l] <= dev.freq_start_mhz) {
        assignedLane = l;
        lanes[l] = dev.freq_end_mhz + 2;
        break;
      }
    }
    if (assignedLane === -1) {
      assignedLane = lanes.length;
      lanes.push(dev.freq_end_mhz + 2);
    }
    deviceLanes.push({ dev, lane: assignedLane });
  });

  const laneHeight = isModal ? 40 : 18;
  const laneGap = isModal ? 9 : 4;
  const rulerHeaderHeight = isModal ? 44 : 26;
  const minCanvasHeight = isModal ? 520 : 115;
  const totalCanvasHeight = Math.max(minCanvasHeight, rulerHeaderHeight + (lanes.length * (laneHeight + laneGap)) + 10);
  container.style.height = `${totalCanvasHeight}px`;

  const channelColors = [
    { bg: 'linear-gradient(135deg, rgba(16, 185, 129, 0.45), rgba(6, 182, 212, 0.45))', border: '#10b981', text: '#ffffff' },
    { bg: 'linear-gradient(135deg, rgba(56, 189, 248, 0.45), rgba(99, 102, 241, 0.45))', border: '#38bdf8', text: '#ffffff' },
    { bg: 'linear-gradient(135deg, rgba(245, 158, 11, 0.45), rgba(249, 115, 22, 0.45))', border: '#f59e0b', text: '#ffffff' },
    { bg: 'linear-gradient(135deg, rgba(168, 85, 247, 0.45), rgba(236, 72, 153, 0.45))', border: '#c084fc', text: '#ffffff' },
    { bg: 'linear-gradient(135deg, rgba(239, 68, 68, 0.45), rgba(244, 63, 94, 0.45))', border: '#f87171', text: '#ffffff' },
    { bg: 'linear-gradient(135deg, rgba(20, 184, 166, 0.45), rgba(16, 185, 129, 0.45))', border: '#2dd4bf', text: '#ffffff' },
  ];

  const channelsHtml = deviceLanes.map(({ dev, lane }, idx) => {
    const leftPct = ((dev.freq_start_mhz - MIN_SPEC_FREQ) / SPAN_SPEC_FREQ) * 100;
    const widthPct = (dev.bandwidth_mhz / SPAN_SPEC_FREQ) * 100;
    const topPx = rulerHeaderHeight + (lane * (laneHeight + laneGap));

    const colorStyle = channelColors[idx % channelColors.length];
    const isAp = (dev.name || '').toLowerCase().startsWith('ap_') || (dev.name || '').toLowerCase().startsWith('sec_') || dev.device_type === 'ap';
    const roleIcon = isAp ? '📡' : '📶';

    const safeName = (dev.name || 'Dispositivo').replace(/"/g, '&quot;');
    const tooltipText = `
${roleIcon} ${dev.name}
📍 Frecuencia Central: ${dev.frequency_mhz} MHz
📏 Ancho de Canal: ${dev.bandwidth_text} (${dev.freq_start_mhz} - ${dev.freq_end_mhz} MHz)
🌐 IP: ${dev.ip || 'N/A'}
⚡ Potencia Tx: ${dev.tx_power_dbm ? dev.tx_power_dbm + ' dBm' : 'N/A'}
📶 Señal RSSI: ${dev.rssi_dbm ? dev.rssi_dbm + ' dBm' : 'N/A'}
📊 Ruido SNR: ${dev.snr_db ? dev.snr_db + ' dB' : 'N/A'}
💾 Firmware: ${dev.firmware || 'N/A'}
👥 Clientes STA: ${dev.connected_sta_count || 'N/A'}
    `.trim();

    return `
      <div class="spectrum-channel-block" 
           style="left: ${leftPct}%; width: ${widthPct}%; top: ${topPx}px; height: ${laneHeight}px; background: ${colorStyle.bg}; border: 1.5px solid ${colorStyle.border}; color: ${colorStyle.text};"
           data-node-id="${dev.node_id}"
           title="${tooltipText}">
        <div class="spectrum-center-line"></div>
        <div style="position: relative; z-index: 2; display: flex; align-items: center; gap: ${isModal ? '5px' : '3px'}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: ${isModal ? '0.86rem' : '0.66rem'};">
          <span style="font-size: ${isModal ? '0.96rem' : '0.72rem'};">${roleIcon}</span>
          <strong style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${safeName}</strong>
          <span style="opacity: 0.88; font-family: monospace; font-size: ${isModal ? '0.78rem' : '0.60rem'};">(${dev.bandwidth_mhz}M)</span>
        </div>
      </div>
    `;
  }).join('');

  container.innerHTML = `
    <div class="spectrum-ruler-track">
      ${ticksHtml}
    </div>
    ${bandsHtml}
    ${channelsHtml}
  `;

  container.querySelectorAll('.spectrum-channel-block').forEach(blk => {
    blk.addEventListener('click', () => {
      const nid = blk.dataset.nodeId;
      if (nid) {
        focusNodeInMap(nid);
        if (isModal) {
          document.getElementById('modal-spectrum-fullscreen').style.display = 'none';
        }
      }
    });
  });
}

function focusNodeInMap(nodeId) {
  if (!nodeId) return;
  const grp = nodeGroups.get(nodeId);
  if (grp) {
    const nodeObj = (currentMap.nodes || []).find(n => n.id === nodeId);
    if (nodeObj) {
      clearMultiSelection();
      selectNode(nodeObj);
    }
    const scale = stage.scaleX();
    stage.position({
      x: (stage.width() / 2) - (grp.x() * scale),
      y: (stage.height() / 2) - (grp.y() * scale)
    });
    stage.batchDraw();
  }
}

function openSpectrumModal() {
  const modal = document.getElementById('modal-spectrum-fullscreen');
  if (!modal) return;

  modal.style.display = 'flex';

  const selectMap = document.getElementById('select-spectrum-map');
  const mapName = selectMap ? selectMap.options[selectMap.selectedIndex].text : 'Mapa';
  const titleEl = document.getElementById('modal-spectrum-map-title');
  if (titleEl) titleEl.textContent = `📍 Mapa: ${mapName}`;

  const count = spectrumSelectedDeviceIds.size;
  const totalBadge = document.getElementById('modal-spec-total-badge');
  if (totalBadge) totalBadge.textContent = `${count} Equipos Visibles`;

  renderSpectrumRuler('spectrum-modal-ruler-canvas', true);

  setTimeout(() => {
    const scrollContainer = document.getElementById('spectrum-modal-ruler-scroll');
    const firstBlock = document.querySelector('#spectrum-modal-ruler-canvas .spectrum-channel-block');
    if (scrollContainer && firstBlock) {
      const offset = firstBlock.offsetLeft - 150;
      scrollContainer.scrollTo({ left: Math.max(0, offset), behavior: 'smooth' });
    }
  }, 100);
}



// ─── 11.5. Sincronización con Zabbix Services (BSM) ─────────────────────────
async function openZabbixSyncModal() {
  const modal = document.getElementById('modal-zabbix-sync');
  if (!modal) return;

  modal.style.display = 'flex';
  const consoleBox = document.getElementById('zbx-sync-console');
  if (consoleBox) {
    consoleBox.style.display = 'none';
    consoleBox.innerHTML = '';
  }

  // Actualizar nombre del mapa activo en selector de alcance
  const scopeMapNameEl = document.getElementById('zbx-scope-map-name');
  if (scopeMapNameEl) {
    scopeMapNameEl.textContent = currentMap ? currentMap.name : 'Mapa Actual';
  }

  // Configurar listeners de cambio de radio de alcance
  const radioBranch = document.getElementById('radio-zbx-scope-branch');
  const radioGlobal = document.getElementById('radio-zbx-scope-global');
  const btnExec = document.getElementById('btn-exec-zbx-sync');

  const updateSyncButtonText = () => {
    if (!btnExec) return;
    if (radioBranch && radioBranch.checked) {
      btnExec.innerHTML = `<i class="fas fa-sync-alt"></i> Sincronizar Rama a Zabbix`;
    } else {
      btnExec.innerHTML = `<i class="fas fa-sync-alt"></i> Sincronizar Todo a Zabbix`;
    }
  };

  if (radioBranch) radioBranch.onchange = updateSyncButtonText;
  if (radioGlobal) radioGlobal.onchange = updateSyncButtonText;
  updateSyncButtonText();

  // 1. Obtener estado de conexión Zabbix
  try {
    const status = await API.getZabbixStatus();
    const verEl = document.getElementById('zbx-status-version');
    const urlEl = document.getElementById('zbx-status-url');
    const badgeEl = document.getElementById('zbx-status-badge');
    const textEl = document.getElementById('zbx-status-text');

    if (status && status.connected) {
      verEl.textContent = `Zabbix ${status.version} (${status.total_zabbix_hosts || 0} hosts indexados)`;
      urlEl.textContent = status.zabbix_url;
      badgeEl.className = 'user-badge';
      textEl.textContent = 'En Línea';
    } else {
      verEl.textContent = 'Zabbix Desconectado';
      urlEl.textContent = status.zabbix_url || 'N/A';
      badgeEl.className = 'user-badge unauthenticated';
      textEl.textContent = 'Desconectado';
    }
  } catch (err) {
    console.error('Error cargando estado Zabbix:', err);
  }

  // 2. Obtener análisis topológico del mapa actual
  if (currentMap) {
    document.getElementById('zbx-analysis-map-name').textContent = currentMap.name;
    try {
      const analysis = await API.getTopologyAnalysis(currentMap.id);
      if (analysis) {
        const connectedCount = analysis.connected_nodes_count !== undefined ? analysis.connected_nodes_count : (analysis.nodes.filter(n => n.relation_type !== 'isolated').length);
        const isolatedCount = analysis.isolated_nodes_count !== undefined ? analysis.isolated_nodes_count : (analysis.total_nodes - connectedCount);
        const matchedConnected = analysis.nodes.filter(n => n.zabbix_matched && n.relation_type !== 'isolated').length;

        document.getElementById('zbx-stat-nodes').textContent = `${connectedCount} / ${analysis.total_nodes}`;
        document.getElementById('zbx-stat-matched').textContent = `${matchedConnected} / ${connectedCount}`;
        document.getElementById('zbx-stat-links').textContent = analysis.total_links;
        document.getElementById('zbx-stat-direct').textContent = analysis.direct_relations_count;
        document.getElementById('zbx-stat-root').textContent = analysis.root_nodes_count;
        const isolatedEl = document.getElementById('zbx-stat-isolated');
        if (isolatedEl) isolatedEl.textContent = isolatedCount;
      }
    } catch (err) {
      console.error('Error obteniendo análisis de topología:', err);
    }
  }
}

async function handleExecuteZabbixSync() {
  const btn = document.getElementById('btn-exec-zbx-sync');
  const consoleBox = document.getElementById('zbx-sync-console');
  if (!btn || !consoleBox) return;

  const scope = document.querySelector('input[name="zbx-sync-scope"]:checked')?.value || 'branch';
  const clearFirst = document.getElementById('chk-zbx-clear-first')?.checked ?? true;
  const mapId = currentMap ? currentMap.id : null;
  const scopeLabel = scope === 'branch' ? `Rama actual (${currentMap ? currentMap.name : 'Mapa'})` : 'Todo el Sistema';

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sincronizando...';
  consoleBox.style.display = 'block';
  consoleBox.innerHTML = `<div style="color: #38bdf8;">[1/3] Iniciando sincronización BSM con Zabbix 7.0 (${scopeLabel})...</div>`;

  try {
    consoleBox.innerHTML += '<div style="color: #94a3b8;">[2/3] Filtrando nodos con relaciones y mapeando jerarquía Host-a-Host (Padre ➔ Hijo)...</div>';
    const res = await API.syncZabbix({ scope, map_id: mapId, clear_first: clearFirst });
    const rep = res.report || {};

    consoleBox.innerHTML += `
      <div style="color: #10b981; margin-top: 6px; font-weight: bold;">✔ ¡Sincronización BSM Host-a-Host exitosa!</div>
      <div style="color: #cbd5e1; margin-top: 4px;">• Alcance: <strong>${scope === 'branch' ? 'Rama Actual' : 'Todo el Sistema'}</strong> (${rep.maps_processed || 1} mapas)</div>
      <div style="color: #cbd5e1;">• Dispositivos con relaciones sincronizados: <strong>${rep.nodes_synced || 0}</strong></div>
      <div style="color: #94a3b8;">• Dispositivos aislados sin aristas (omitidos): <strong>${rep.isolated_devices_skipped || 0}</strong></div>
      <div style="color: #38bdf8;">• Nodos Raíz Proveedores (Gateways/Core): <strong>${rep.root_devices_count || 0}</strong></div>
      <div style="color: #10b981;">• Hosts vinculados a triggers en Zabbix: <strong>${rep.nodes_matched_zabbix || 0}</strong></div>
      <div style="color: #38bdf8;">• Dependencias directas (Aristas Padre ➔ Hijo): <strong>${rep.direct_dependencies_created || 0}</strong></div>
      <div style="color: #64748b; font-size: 0.7rem; margin-top: 6px;">Solo se enviaron equipos con contexto y conexiones reales. Causa raíz BSM activa.</div>
    `;
    consoleBox.scrollTop = consoleBox.scrollHeight;
  } catch (err) {
    consoleBox.innerHTML += `<div style="color: #ef4444; margin-top: 6px;">❌ Error en sincronización: ${err.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

async function handleClearZabbixServices() {
  const scope = document.querySelector('input[name="zbx-sync-scope"]:checked')?.value || 'branch';
  const mapId = currentMap ? currentMap.id : null;

  let confirmMsg = '';
  if (scope === 'branch' && currentMap) {
    confirmMsg = `¿Estás seguro de eliminar de Zabbix únicamente los servicios de la rama '${currentMap.name}' y sus submapas descendientes?`;
  } else {
    confirmMsg = '¿Estás seguro de eliminar TODOS los servicios de NexusDude en Zabbix?\n\nEsto limpiará la estructura BSM global de NexusDude para permitir una re-sincronización limpia.';
  }

  if (!confirm(confirmMsg)) {
    return;
  }

  const consoleBox = document.getElementById('zbx-sync-console');
  if (consoleBox) {
    consoleBox.style.display = 'block';
    consoleBox.innerHTML = '<div style="color: #f59e0b;">Eliminando servicios en Zabbix...</div>';
  }

  try {
    const res = await API.clearZabbixServices(scope === 'branch' ? mapId : null, scope);
    if (consoleBox) {
      consoleBox.innerHTML = `<div style="color: #10b981;">✔ ${res.message || `Se eliminaron ${res.deleted_services || 0} servicios en Zabbix.`}</div>`;
    }
  } catch (err) {
    if (consoleBox) {
      consoleBox.innerHTML = `<div style="color: #ef4444;">❌ Error: ${err.message}</div>`;
    }
  }
}

// ─── 11.6. Generación Automática de Mapas desde Sitios NetBox ─────────────
async function openBulkSitesModal() {
  const modal = document.getElementById('modal-bulk-sites');
  if (!modal) return;

  modal.style.display = 'flex';
  const consoleBox = document.getElementById('bulk-sites-console');
  if (consoleBox) {
    consoleBox.style.display = 'none';
    consoleBox.innerHTML = '';
  }

  // 1. Poblar el selector de mapa padre con los mapas disponibles
  const selectParent = document.getElementById('bulk-select-parent-map');
  if (selectParent) {
    selectParent.innerHTML = '<option value="">🌐 Ninguno (Crear todos como mapas raíz)</option>';
    try {
      const maps = await API.getMaps();
      maps.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = `${m.parent_map_id ? '  ↳ ' : '🗺️ '}${m.name} (${m.nodes_count || 0} nodos)`;
        if (m.id === 'default-map' || (currentMap && m.id === currentMap.id)) {
          opt.selected = true;
        }
        selectParent.appendChild(opt);
      });
      if (!selectParent.value && maps.some(m => m.id === 'default-map')) {
        selectParent.value = 'default-map';
      }
    } catch (err) {
      console.error('Error cargando mapas para selector:', err);
    }
  }

  // 2. Cargar diagnóstico de mapeo y sitios desde NetBox
  await refreshBulkSitesStatus();

  // 3. Cargar estado del Webhook en tiempo real con NetBox
  await refreshWebhookStatusUI();
}

// ─── 11.7. Convertir Nodo Individual a Submapa (Selector de Mapa) ───────────
async function openConvertSubmapModal() {
  if (!selectedNode) return;
  const modal = document.getElementById('modal-convert-submap');
  if (!modal) return;

  const nodeName = selectedNode.name || 'Dispositivo';
  const siteName = selectedNode.site_name || nodeName;

  document.getElementById('convert-submap-node-name').textContent = nodeName;
  document.getElementById('input-convert-new-map-name').value = siteName;
  document.getElementById('select-convert-mode').value = 'create_new';

  const inputSearch = document.getElementById('input-search-convert-existing-map');
  if (inputSearch) inputSearch.value = '';

  document.getElementById('convert-section-create-new').style.display = 'block';
  document.getElementById('convert-section-link-existing').style.display = 'none';

  // Cargar lista de todos los mapas existentes para el selector
  const selectExisting = document.getElementById('select-convert-existing-map');
  if (selectExisting) {
    selectExisting.innerHTML = '<option value="">-- Elige un mapa de la lista --</option>';
    try {
      const maps = await API.getMaps();
      if (maps && maps.length > 0) {
        maps.forEach(m => {
          // No permitir seleccionar el mapa actual en el que ya estamos parados para evitar auto-referencia
          if (currentMap && m.id === currentMap.id) return;
          const opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = `${m.parent_map_id ? '  ↳ ' : '🗺️ '}${m.name} (${m.nodes_count || 0} nodos)`;
          selectExisting.appendChild(opt);
        });
      } else {
        selectExisting.innerHTML = '<option value="">No hay otros mapas creados</option>';
      }
    } catch (e) {
      console.error('Error cargando lista de mapas para vincular:', e);
      selectExisting.innerHTML = '<option value="">Error al cargar mapas</option>';
    }
  }

  modal.style.display = 'flex';
}

async function handleExecuteConvertSubmap() {
  if (!selectedNode) return;
  const modal = document.getElementById('modal-convert-submap');
  const mode = document.getElementById('select-convert-mode')?.value;
  const nodeName = selectedNode.name;
  const siteName = selectedNode.site_name || nodeName;

  let targetMapId = null;
  let targetMapName = '';

  if (mode === 'create_new') {
    const newMapName = document.getElementById('input-convert-new-map-name')?.value?.trim() || siteName;
    const autoPopulate = document.getElementById('check-convert-auto-populate')?.checked ?? true;

    try {
      const newMap = await API.createMap({
        name: newMapName,
        description: `Submapa para ${newMapName} (Convertido desde nodo ${nodeName})`,
        parent_map_id: currentMap ? currentMap.id : null
      });

      targetMapId = newMap.id;
      targetMapName = newMap.name;

      if (autoPopulate) {
        try {
          await API.populateMapFromSite(newMap.id, siteName);
        } catch (e) {
          console.log('Poblado automático omitido:', e);
        }
      }
    } catch (err) {
      alert('Error creando nuevo submapa: ' + err.message);
      return;
    }

  } else {
    // Vincular a un mapa existente
    targetMapId = document.getElementById('select-convert-existing-map')?.value;
    if (!targetMapId) {
      alert('Por favor selecciona un mapa existente de la lista.');
      return;
    }
    const selectElem = document.getElementById('select-convert-existing-map');
    targetMapName = selectElem.options[selectElem.selectedIndex].text;
  }

  try {
    // Actualizar el nodo actual a tipo 'submap' vinculándolo al targetMapId elegido
    const updatedExtra = {
      ...(selectedNode.extra_data || {}),
      target_map_id: targetMapId,
      site_name: siteName
    };

    await API.updateNode(selectedNode.id, {
      device_type: 'submap',
      extra_data: updatedExtra
    });

    selectedNode.device_type = 'submap';
    selectedNode.extra_data = updatedExtra;

    // Re-renderizar nodo en el lienzo
    const oldGroup = nodeGroups.get(selectedNode.id);
    if (oldGroup) oldGroup.destroy();
    nodeGroups.delete(selectedNode.id);

    renderNode(selectedNode);
    nodesLayer.batchDraw();

    // Actualizar paneles de la interfaz
    selectNode(selectedNode);
    refreshMapsTabList();

    if (modal) modal.style.display = 'none';
    alert(`✔ El nodo "${nodeName}" fue convertido exitosamente en Submapa relacionado con "${targetMapName}".`);
  } catch (err) {
    alert('Error al vincular nodo a submapa: ' + err.message);
  }
}

async function refreshBulkSitesStatus() {
  try {
    const statusData = await API.getSitesStatus();
    const totalSites = statusData.total_sites || 0;
    const mappedCount = statusData.mapped_count || 0;
    const unmappedCount = statusData.unmapped_count || 0;
    const sites = statusData.sites || [];
    const totalDevices = sites.reduce((sum, s) => sum + (s.device_count || 0), 0);

    const elTotal = document.getElementById('bulk-stat-sites-total');
    const elMapped = document.getElementById('bulk-stat-mapped-total');
    const elUnmapped = document.getElementById('bulk-stat-unmapped-total');
    const elDevsTotal = document.getElementById('bulk-stat-devices-total');

    if (elTotal) elTotal.textContent = totalSites;
    if (elMapped) elMapped.textContent = mappedCount;
    if (elUnmapped) elUnmapped.textContent = unmappedCount;
    if (elDevsTotal) elDevsTotal.textContent = totalDevices;
  } catch (err) {
    console.error('Error cargando estado de sitios NetBox:', err);
  }
}

async function refreshWebhookStatusUI() {
  const statusText = document.getElementById('webhook-status-text');
  const dot = document.getElementById('webhook-dot-indicator');
  const btn = document.getElementById('btn-reconfigure-webhook');
  if (!statusText || !dot) return;

  try {
    const wh = await API.getNetBoxWebhookStatus();
    if (wh.active) {
      dot.style.background = '#10b981';
      dot.style.boxShadow = '0 0 8px #10b981';
      statusText.innerHTML = `<strong>Webhook NetBox Activo:</strong> Conectado en tiempo real (ID #${wh.webhook_id}). Al registrar un sitio nuevo en NetBox, su submapa se crea al instante sin tocar mapas existentes.`;
      if (btn) {
        btn.innerHTML = '<i class="fas fa-check-circle" style="color: #10b981;"></i> Activo';
        btn.title = 'Webhook y regla de eventos activos en NetBox';
      }
    } else {
      dot.style.background = '#f59e0b';
      dot.style.boxShadow = '0 0 6px #f59e0b';
      statusText.innerHTML = `<strong>Webhook NetBox:</strong> No detectado en NetBox. Haz clic en "Conectar Webhook" para sincronización instantánea.`;
      if (btn) {
        btn.innerHTML = '<i class="fas fa-plug"></i> Conectar Webhook';
        btn.title = 'Configurar webhook automáticamente en NetBox';
      }
    }
  } catch (err) {
    console.warn('Error comprobando webhook:', err);
  }
}

async function handleSetupWebhook() {
  const btn = document.getElementById('btn-reconfigure-webhook');
  if (!btn) return;
  const origHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Conectando...';

  try {
    const res = await API.setupNetBoxWebhook();
    alert(`✔ Webhook configurado exitosamente en NetBox:\n${res.message}`);
    await refreshWebhookStatusUI();
  } catch (err) {
    alert(`❌ Error configurando webhook en NetBox: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = origHtml;
  }
}

// ─── 11.8. Gestión de Permisos y Roles RBAC ───────────────────────────────
async function openRBACModal() {
  const modal = document.getElementById('modal-rbac');
  if (!modal) return;
  modal.style.display = 'flex';
  await loadRBACRoles();
}

async function loadRBACRoles() {
  const tbody = document.getElementById('rbac-roles-tbody');
  if (!tbody) return;

  try {
    const roles = await API.getRoles();
    if (!roles || roles.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="padding: 10px; text-align: center; color: #94a3b8;">No hay roles registrados.</td></tr>';
      return;
    }

    tbody.innerHTML = roles.map(r => {
      const p = r.permissions || {};
      const chk = (val) => val ? '<i class="fas fa-check-circle" style="color: #10b981;"></i>' : '<i class="fas fa-times-circle" style="color: #64748b; opacity: 0.5;"></i>';
      return `
        <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
          <td style="padding: 6px;">
            <strong style="color: #38bdf8;">${r.name}</strong>
            ${r.is_system ? '<span style="font-size:0.6rem; background:rgba(168,85,247,0.2); color:#c084fc; padding:1px 4px; border-radius:3px; margin-left:4px;">Sistema</span>' : ''}
            <div style="font-size: 0.68rem; color: #94a3b8;">${r.description || ''}</div>
          </td>
          <td style="padding: 6px; text-align: center;">${chk(p.can_access_servers)}</td>
          <td style="padding: 6px; text-align: center;">${chk(p.can_edit_topology)}</td>
          <td style="padding: 6px; text-align: center;">${chk(p.can_manage_submaps)}</td>
          <td style="padding: 6px; text-align: center;">${chk(p.can_view_zabbix)}</td>
          <td style="padding: 6px; text-align: center;">${chk(p.can_manage_users)}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Error cargando roles RBAC:', err);
    tbody.innerHTML = `<tr><td colspan="6" style="padding: 10px; text-align: center; color: #ef4444;">Error: ${err.message}</td></tr>`;
  }
}

async function handleSaveNewRole() {
  const nameInput = document.getElementById('input-rbac-role-name');
  const descInput = document.getElementById('input-rbac-role-desc');
  if (!nameInput || !nameInput.value.trim()) {
    alert('Por favor especifica un nombre para el nuevo rol.');
    return;
  }

  const roleName = nameInput.value.trim();
  const roleDesc = descInput?.value.trim() || '';

  const permissions = {
    can_access_servers: document.getElementById('chk-perm-servers')?.checked ?? false,
    can_edit_topology: document.getElementById('chk-perm-topology')?.checked ?? true,
    can_manage_submaps: document.getElementById('chk-perm-submaps')?.checked ?? true,
    can_view_zabbix: document.getElementById('chk-perm-zabbix')?.checked ?? true,
    can_view_inventory: document.getElementById('chk-perm-inventory')?.checked ?? true,
    can_manage_users: document.getElementById('chk-perm-users')?.checked ?? false
  };

  try {
    await API.createRole({
      name: roleName,
      description: roleDesc,
      permissions: permissions
    });

    nameInput.value = '';
    if (descInput) descInput.value = '';
    alert(`✔ Rol '${roleName}' creado exitosamente.`);
    await loadRBACRoles();
  } catch (err) {
    alert('Error creando nuevo rol: ' + err.message);
  }
}

async function handleExecuteBulkSites() {
  const btn = document.getElementById('btn-exec-bulk-sites');
  const consoleBox = document.getElementById('bulk-sites-console');
  if (!btn || !consoleBox) return;

  const parentMapId = document.getElementById('bulk-select-parent-map')?.value || null;
  const onlyWithDevices = document.getElementById('bulk-check-only-with-devices')?.checked ?? true;
  const autoPopulate = document.getElementById('bulk-check-auto-populate')?.checked ?? true;
  const insertShortcuts = document.getElementById('bulk-check-insert-shortcuts')?.checked ?? true;
  // Política estricta: los submapas ya generados NO se tocan
  const skipExisting = true;

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generando mapas...';
  consoleBox.style.display = 'block';
  consoleBox.innerHTML = '<div style="color: #38bdf8;">[1/3] Consultando inventario y clasificando sitios de NetBox...</div>';

  try {
    consoleBox.innerHTML += '<div style="color: #94a3b8;">[2/3] Creando mapas nuevos (preservando los existentes), ordenando cuadrículas jerárquicas y generando accesos directos...</div>';

    const payload = {
      parent_map_id: parentMapId,
      only_with_devices: onlyWithDevices,
      auto_populate_devices: autoPopulate,
      insert_submap_nodes: insertShortcuts,
      skip_existing: skipExisting
    };

    const res = await API.bulkCreateMapsFromSites(payload);

    consoleBox.innerHTML += `
      <div style="color: #10b981; margin-top: 6px; font-weight: bold;">✔ ¡Generación automática completada exitosamente!</div>
      <div style="color: #cbd5e1; margin-top: 4px;">• Sitios evaluados: <strong>${res.total_evaluated || 0}</strong></div>
      <div style="color: #10b981;">• Mapas nuevos creados: <strong>${res.created_count || 0}</strong></div>
      <div style="color: #38bdf8;">• Equipos ordenados en mapas: <strong>${res.devices_populated || 0}</strong></div>
      <div style="color: #a855f7;">• Accesos directos (📁) en mapa padre: <strong>${res.submap_nodes_created || 0}</strong></div>
      ${res.skipped_count > 0 ? `<div style="color: #94a3b8;">• 🔒 Mapas existentes preservados (no tocados): <strong>${res.skipped_count}</strong></div>` : ''}
      <div style="color: #64748b; font-size: 0.7rem; margin-top: 6px;">El árbol de navegación ha sido actualizado. Puedes explorar cada sitio o sincronizarlos con Zabbix BSM.</div>
    `;
    consoleBox.scrollTop = consoleBox.scrollHeight;

    // Actualizar métricas del diagnóstico
    await refreshBulkSitesStatus();

    // Refrescar el árbol de mapas lateral
    await refreshMapsTabList();

    // Si el mapa actual es el padre, recargar su vista para mostrar los nuevos accesos directos
    if (parentMapId && currentMap && currentMap.id === parentMapId) {
      await loadMap(currentMap.id);
    }
  } catch (err) {
    consoleBox.innerHTML += `<div style="color: #ef4444; margin-top: 6px;">❌ Error en generación: ${err.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

// ─── Auto-Diseño PCB: Terminales y Buses de Conexión ──────────────────────────
async function autoLayoutMapAsPCB() {
  if (!currentMap || !currentMap.nodes || currentMap.nodes.length === 0) return;

  const nodes = currentMap.nodes;
  const links = currentMap.links || [];
  const nodeMap = new Map();
  nodes.forEach(n => nodeMap.set(n.id, n));

  // 1. Construir jerarquía de buses (Controlador Maestro -> Distribución -> Periféricos)
  const childrenMap = new Map();
  const parentsMap = new Map();
  nodes.forEach(n => {
    childrenMap.set(n.id, []);
    parentsMap.set(n.id, []);
  });

  links.forEach(l => {
    if (nodeMap.has(l.source_node_id) && nodeMap.has(l.target_node_id)) {
      const dir = l.extra_data?.direction || 'source_to_target';
      const parentId = dir === 'target_to_source' ? l.target_node_id : l.source_node_id;
      const childId = dir === 'target_to_source' ? l.source_node_id : l.target_node_id;

      if (!childrenMap.get(parentId).includes(childId)) {
        childrenMap.get(parentId).push(childId);
      }
      if (!parentsMap.get(childId).includes(parentId)) {
        parentsMap.get(childId).push(parentId);
      }
    }
  });

  // 2. Identificar terminales maestras / cabeceras de bus
  let rootIds = nodes
    .filter(n => (parentsMap.get(n.id) || []).length === 0)
    .map(n => n.id);

  if (rootIds.length === 0) {
    rootIds = [nodes[0].id];
  }

  // 3. Cálculo de estratos de bus PCB (Nivel de componente en el esquema)
  const levelMap = new Map();
  rootIds.forEach(rid => levelMap.set(rid, 0));

  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    childrenMap.forEach((kids, parentId) => {
      const pLvl = levelMap.get(parentId) ?? 0;
      kids.forEach(kidId => {
        const needed = pLvl + 1;
        const cur = levelMap.get(kidId) ?? -1;
        if (cur < needed) {
          levelMap.set(kidId, needed);
          changed = true;
        }
      });
    });
    if (!changed) break;
  }

  nodes.forEach(n => {
    if (!levelMap.has(n.id)) levelMap.set(n.id, 0);
  });

  // 4. Ubicación de Bloques de Componentes en Cuadrícula PCB (Bancos y Corredores de Bus)
  const compW = 190.0;
  const colGap = 80.0;
  const rowSpacing = 260.0; // Corredor amplio para paso de pistas de bus paralelas
  const moduleGap = 120.0;  // Separación entre subsistemas/módulos independientes
  const startX = 100.0;
  const startY = 100.0;

  const visited = new Set();
  const nodePositions = new Map();

  function layoutPCBModule(nodeId) {
    visited.add(nodeId);
    const rawKids = childrenMap.get(nodeId) || [];
    const kids = rawKids.filter(kid => !visited.has(kid));

    if (kids.length === 0) {
      const singleWidth = compW + colGap;
      const nodePos = new Map();
      nodePos.set(nodeId, { relX: 0.0, depth: levelMap.get(nodeId) || 0 });
      return {
        width: singleWidth,
        relX: 0.0,
        nodes: nodePos
      };
    }

    const childLayouts = [];
    let currentOffset = 0.0;
    const moduleNodes = new Map();
    moduleNodes.set(nodeId, { relX: 0.0, depth: levelMap.get(nodeId) || 0 });

    for (let i = 0; i < kids.length; i++) {
      const kLayout = layoutPCBModule(kids[i]);
      childLayouts.push({ layout: kLayout, offset: currentOffset });

      kLayout.nodes.forEach((kPos, kId) => {
        moduleNodes.set(kId, {
          relX: kPos.relX + currentOffset,
          depth: kPos.depth
        });
      });

      currentOffset += kLayout.width;
    }

    const totalChildrenWidth = currentOffset;
    const firstKidRelX = childLayouts[0].layout.relX + childLayouts[0].offset;
    const lastKidRelX = childLayouts[childLayouts.length - 1].layout.relX + childLayouts[childLayouts.length - 1].offset;
    const parentRelX = (firstKidRelX + lastKidRelX) / 2.0;

    moduleNodes.get(nodeId).relX = parentRelX;
    const totalWidth = Math.max(compW + colGap, totalChildrenWidth);

    return {
      width: totalWidth,
      relX: parentRelX,
      nodes: moduleNodes
    };
  }

  let currentModuleX = startX;

  // Procesar cada módulo/subsistema de componentes
  rootIds.forEach(rootId => {
    if (visited.has(rootId)) return;
    const pcbLayout = layoutPCBModule(rootId);

    pcbLayout.nodes.forEach((pos, nid) => {
      // Ajuste exacto a la cuadrícula PCB de 20px
      const rawX = currentModuleX + pos.relX;
      const rawY = startY + pos.depth * rowSpacing;
      const snapX = Math.round(rawX / 20.0) * 20;
      const snapY = Math.round(rawY / 20.0) * 20;
      nodePositions.set(nid, { x: snapX, y: snapY, level: pos.depth });
    });

    currentModuleX += pcbLayout.width + moduleGap;
  });

  // Ubicar terminales periféricas o aisladas
  nodes.forEach(n => {
    if (!nodePositions.has(n.id)) {
      const rawX = currentModuleX;
      const rawY = startY + (levelMap.get(n.id) || 0) * rowSpacing;
      const snapX = Math.round(rawX / 20.0) * 20;
      const snapY = Math.round(rawY / 20.0) * 20;
      nodePositions.set(n.id, { x: snapX, y: snapY, level: levelMap.get(n.id) || 0 });
      currentModuleX += compW + colGap;
    }
  });

  // 5. Aplicar animación de posicionamiento de componentes y actualización de pistas de bus
  const updatePromises = [];
  nodes.forEach(n => {
    const pos = nodePositions.get(n.id);
    if (pos) {
      n.x = pos.x;
      n.y = pos.y;

      const grp = nodeGroups.get(n.id);
      if (grp) {
        new Konva.Tween({
          node: grp,
          duration: 0.35,
          x: pos.x,
          y: pos.y,
          easing: Konva.Easings.EaseInOut,
          onUpdate: () => {
            updateAllLinks();
          },
          onFinish: () => {
            updateAllLinks();
            if (nodesLayer) nodesLayer.batchDraw();
            if (linksLayer) linksLayer.batchDraw();
          }
        }).play();
      }

      updatePromises.push(API.updateNode(n.id, { x: pos.x, y: pos.y }).catch(e => console.warn(e)));
    }
  });

  setTimeout(() => {
    updateAllLinks();
    if (nodesLayer) nodesLayer.batchDraw();
    if (linksLayer) linksLayer.batchDraw();
  }, 380);

  await Promise.all(updatePromises);
}

const autoLayoutMapAsTree = autoLayoutMapAsPCB; // Alias para compatibilidad inversa

// ─── 12. Inicialización General ─────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  setupSidebarResizer();
  initSSOAuth();
  initCanvas();

  // Cambio de pestañas
  document.querySelectorAll('.sidebar-tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  // Verificación de autenticación y carga de datos en segundo plano sin bloquear listeners
  initAppAsync();

  async function initAppAsync() {
    try {
      currentUser = await API.verifyAuth();
      const userBadge = document.getElementById('user-badge');
      const authOverlay = document.getElementById('auth-overlay');

      if (currentUser && currentUser.authenticated) {
        userBadge.innerHTML = `<span class="status-dot"></span><span>${currentUser.username} (${currentUser.role})</span>`;
        userBadge.classList.remove('unauthenticated');
        authOverlay.style.display = 'none';

        // Cargar roles y filtros de inventario antes de renderizar mapas para tener la paleta oficial
        await loadInventoryFilters();

        // Cargar mapas y restaurar estado de navegación persistente
        const maps = await API.getMaps();
        if (maps && maps.length > 0) {
          const hashParams = new URLSearchParams((window.location.hash || '').replace(/^#/, ''));
          const hashTab = hashParams.get('tab');
          const hashMapId = hashParams.get('map');
          const hashNodeId = hashParams.get('node');

          const savedTab = hashTab || localStorage.getItem('nexusdude_active_tab') || 'tab-maps';
          const savedMapId = hashMapId || localStorage.getItem('nexusdude_last_map_id');

          let targetMapId = maps[0].id;
          if (savedMapId && maps.some(m => m.id === savedMapId)) {
            targetMapId = savedMapId;
          }

          await loadMap(targetMapId);

          // Restaurar pestaña activa (por defecto: Mapas)
          switchTab(savedTab);

          // Si venía un nodo específico seleccionado en el hash
          if (hashNodeId && currentMap && currentMap.nodes) {
            const matchedNode = currentMap.nodes.find(n => n.id === hashNodeId || String(n.device_id) === hashNodeId);
            if (matchedNode) {
              selectNode(matchedNode);
            }
          }
        }
        await triggerSearch();

      } else {
        userBadge.innerHTML = `<span class="status-dot"></span><span>No autenticado</span>`;
        userBadge.classList.add('unauthenticated');
        authOverlay.style.display = 'flex';
      }
    } catch (e) {
      console.error('Error de autenticación o inicio:', e);
      const authOverlay = document.getElementById('auth-overlay');
      if (authOverlay) authOverlay.style.display = 'flex';
    }
  }

  // Buscador de inventario con debounce
  const searchInput = document.getElementById('input-search-devices');
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(triggerSearch, 250);
  });

  document.getElementById('select-site').addEventListener('change', triggerSearch);
  document.getElementById('select-role').addEventListener('change', triggerSearch);
  document.getElementById('btn-refresh-inventory').addEventListener('click', async () => {
    const btn = document.getElementById('btn-refresh-inventory');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    try {
      await API.refreshInventory();
      await loadInventoryFilters();
      if (currentMap && currentMap.id) {
        await API.syncMapNetboxNodes(currentMap.id);
        await loadMap(currentMap.id);
      }
      await triggerSearch();
    } catch (e) {
      console.error('Error refrescando inventario:', e);
    } finally {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  });

  // Botón Sincronizar Nodos y Enlaces del mapa actual con NetBox en Toolbar
  const btnSyncNetbox = document.getElementById('btn-sync-netbox-nodes');
  if (btnSyncNetbox) {
    btnSyncNetbox.addEventListener('click', async () => {
      if (!currentMap) return;
      const origHtml = btnSyncNetbox.innerHTML;
      btnSyncNetbox.disabled = true;
      btnSyncNetbox.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>Sincronizando NetBox...</span>';

      try {
        await API.refreshInventory();
        await loadInventoryFilters();
        const resNodes = await API.syncMapNetboxNodes(currentMap.id);
        const resCables = await API.syncMapNetboxCables(currentMap.id);
        await loadMap(currentMap.id);
        alert(`Sincronización NetBox completada:\n• Nodos: ${resNodes.message || 'Actualizados'}\n• Enlaces Físicos / Cables: ${resCables.message || 'Actualizados'}`);
      } catch (err) {
        alert('Error sincronizando con NetBox: ' + err.message);
      } finally {
        btnSyncNetbox.disabled = false;
        btnSyncNetbox.innerHTML = origHtml;
      }
    });
  }

  // Buscador de mapas en la pestaña de mapas
  let mapSearchTimeout = null;
  const inputSearchMaps = document.getElementById('input-search-maps');
  if (inputSearchMaps) {
    inputSearchMaps.addEventListener('input', () => {
      clearTimeout(mapSearchTimeout);
      mapSearchTimeout = setTimeout(() => {
        refreshMapsTabList(inputSearchMaps.value);
      }, 200);
    });
  }

  // Botón limpiar búsqueda de mapas
  const btnClearSearchMaps = document.getElementById('btn-clear-search-maps');
  if (btnClearSearchMaps) {
    btnClearSearchMaps.addEventListener('click', () => {
      if (inputSearchMaps) inputSearchMaps.value = '';
      refreshMapsTabList('');
    });
  }

  // Botón contraer todas las carpetas del árbol
  const btnCollapseAllMaps = document.getElementById('btn-collapse-all-maps');
  if (btnCollapseAllMaps) {
    btnCollapseAllMaps.addEventListener('click', () => {
      expandedMapIds.clear();
      saveExpandedMapIds();
      refreshMapsTabList(inputSearchMaps ? inputSearchMaps.value : '');
    });
  }

  // Botón expandir todas las carpetas del árbol
  const btnExpandAllMaps = document.getElementById('btn-expand-all-maps');
  if (btnExpandAllMaps) {
    btnExpandAllMaps.addEventListener('click', () => {
      (cachedMaps || []).forEach(m => expandedMapIds.add(m.id));
      saveExpandedMapIds();
      refreshMapsTabList(inputSearchMaps ? inputSearchMaps.value : '');
    });
  }

  // Botón refrescar mapas en la pestaña
  const btnRefreshMaps = document.getElementById('btn-refresh-maps-tab');
  if (btnRefreshMaps) {
    btnRefreshMaps.addEventListener('click', () => refreshMapsTabList());
  }

  // Botón Nuevo Submapa en la pestaña
  const btnNewSubmapTab = document.getElementById('btn-new-submap-tab');
  if (btnNewSubmapTab) {
    btnNewSubmapTab.addEventListener('click', () => openCreateMapModal(true, currentMap?.id));
  }

  // Botón Importar Sitio NetBox en la pestaña de mapas
  const btnImportSiteTab = document.getElementById('btn-import-site-tab');
  if (btnImportSiteTab) {
    btnImportSiteTab.addEventListener('click', () => openCreateMapModal(true, currentMap?.id, true));
  }

  // Botón Poblar mapa actual desde NetBox en Toolbar
  const btnPopulateCurrent = document.getElementById('btn-populate-current-map');
  if (btnPopulateCurrent) {
    btnPopulateCurrent.addEventListener('click', async () => {
      if (!currentMap) return;
      const siteName = btnPopulateCurrent.dataset.siteName || currentMap.name;
      if (!confirm(`¿Deseas poblar el mapa "${currentMap.name}" con los equipos de NetBox para el sitio "${siteName}"?\n\nLos equipos se posicionarán automáticamente ordenados por jerarquía de red.`)) return;

      try {
        const res = await API.populateMapFromSite(currentMap.id, siteName);
        alert(res.message);
        await loadMap(currentMap.id);
      } catch (err) {
        alert('Error poblando mapa: ' + err.message);
      }
    });
  }

  // Pestañas de modo dentro del modal de mapas
  const btnModeSite = document.getElementById('btn-mode-site');
  if (btnModeSite) {
    btnModeSite.addEventListener('click', () => setModalMode('site'));
  }
  const btnModeManual = document.getElementById('btn-mode-manual');
  if (btnModeManual) {
    btnModeManual.addEventListener('click', () => setModalMode('manual'));
  }

  // Buscador de sitios en el modal de nuevo submapa
  const inputFilterSiteSubmap = document.getElementById('input-filter-site-submap');
  if (inputFilterSiteSubmap) {
    inputFilterSiteSubmap.addEventListener('input', (e) => {
      renderSiteOptionsForSubmap(e.target.value);
    });
  }

  // Cambio de selección de sitio en el modal
  const selectSiteSubmap = document.getElementById('select-site-for-submap');
  if (selectSiteSubmap) {
    selectSiteSubmap.addEventListener('change', () => {
      const selectedSite = selectSiteSubmap.value;
      if (!selectedSite) return;
      const opt = selectSiteSubmap.selectedOptions[0];
      const count = opt ? (opt.dataset.count || 0) : 0;
      document.getElementById('input-new-map-name').value = selectedSite;
      document.getElementById('input-new-map-desc').value = `Sitio NetBox: ${selectedSite} (${count} equipos)`;
    });
  }

  // Botones de crear mapa en topbar
  document.getElementById('btn-new-submap').addEventListener('click', () => openCreateMapModal(true, currentMap?.id));
  document.getElementById('btn-new-root-map').addEventListener('click', () => openCreateMapModal(false));
  document.getElementById('btn-close-map-modal').addEventListener('click', () => {
    document.getElementById('modal-map').style.display = 'none';
  });
  document.getElementById('btn-cancel-map-modal').addEventListener('click', () => {
    document.getElementById('modal-map').style.display = 'none';
  });
  document.getElementById('btn-confirm-save-map').addEventListener('click', handleSaveMap);

  // Botón Añadir Nota en lienzo (Sticky Note)
  const btnAddNote = document.getElementById('btn-add-note');
  if (btnAddNote) {
    btnAddNote.addEventListener('click', async () => {
      if (!currentMap) { alert('Abre un mapa primero.'); return; }
      const stageW = stage ? stage.width() : 600;
      const stageH = stage ? stage.height() : 400;
      const rawX = (stageW / 2 - (stage ? stage.x() : 0)) / (stage ? stage.scaleX() : 1);
      const rawY = (stageH / 2 - (stage ? stage.y() : 0)) / (stage ? stage.scaleY() : 1);
      const noteX = snapToGrid ? Math.round(rawX / GRID_SIZE) * GRID_SIZE : rawX;
      const noteY = snapToGrid ? Math.round(rawY / GRID_SIZE) * GRID_SIZE : rawY;
      try {
        const newNote = await API.createNode({
          map_id: currentMap.id,
          name: 'Nueva Nota',
          device_type: 'note',
          x: noteX,
          y: noteY,
          status: 'ok',
          extra_data: { note_text: 'Nueva Nota', note_color: 'yellow' }
        });
        currentMap.nodes.push(newNote);
        _renderNoteNode(newNote);
        nodesLayer.batchDraw();
        selectNode(newNote);
        openNoteEditorModal(newNote);
      } catch(err) {
        alert('Error creando nota: ' + err.message);
      }
    });
  }

  // Botón Añadir Brazo FTTH / Ramal GPON en lienzo
  const btnAddFtthBranch = document.getElementById('btn-add-ftth-branch');
  if (btnAddFtthBranch) {
    btnAddFtthBranch.addEventListener('click', async () => {
      if (!currentMap) { alert('Abre un mapa primero.'); return; }
      const stageW = stage ? stage.width() : 600;
      const stageH = stage ? stage.height() : 400;
      const rawX = (stageW / 2 - (stage ? stage.x() : 0)) / (stage ? stage.scaleX() : 1);
      const rawY = (stageH / 2 - (stage ? stage.y() : 0)) / (stage ? stage.scaleY() : 1);
      const nodeX = snapToGrid ? Math.round(rawX / GRID_SIZE) * GRID_SIZE : rawX;
      const nodeY = snapToGrid ? Math.round(rawY / GRID_SIZE) * GRID_SIZE : rawY;

      // Buscar si hay una OLT en el mapa actual para pre-asociar
      let defaultOltName = 'OLT_HUAWEI';
      let defaultOltIp = '10.20.0.2';
      let defaultOltId = '';
      if (currentMap && currentMap.nodes) {
        const matchedOlt = currentMap.nodes.find(n => (n.extra_data?.role || n.device_type || n.name || '').toLowerCase().includes('olt'));
        if (matchedOlt) {
          defaultOltName = matchedOlt.name;
          defaultOltIp = matchedOlt.ip || defaultOltIp;
          defaultOltId = matchedOlt.id;
        }
      }

      try {
        const newBranch = await API.createNode({
          map_id: currentMap.id,
          name: 'Brazo Huizache GPON',
          device_type: 'ftth_branch',
          x: nodeX,
          y: nodeY,
          status: 'ok',
          extra_data: {
            branch_name: 'Brazo Huizache GPON',
            gpon_port: 'GPON 0/1/0',
            gpon_index: '4194312192',
            olt_name: defaultOltName,
            olt_ip: defaultOltIp,
            olt_node_id: defaultOltId,
            typical_threshold_dbm: -27.0,
            arrow_direction: 'left',
            arrow_length: 38
          }
        });
        currentMap.nodes.push(newBranch);
        _renderFtthBranchNode(newBranch);
        nodesLayer.batchDraw();

        // ── Auto-vincular arista interactiva con la OLT ──
        if (defaultOltId || defaultOltIp) {
          const oltNode = currentMap.nodes.find(n => (defaultOltId && n.id === defaultOltId) || (defaultOltIp && n.ip === defaultOltIp) || n.name === defaultOltName);
          if (oltNode && oltNode.id !== newBranch.id) {
            try {
              const newLink = await API.createLink({
                map_id: currentMap.id,
                source_node_id: oltNode.id,
                target_node_id: newBranch.id,
                source_interface: 'GPON 0/1/0',
                target_interface: '',
                cable_type: 'fiber',
                status: 'ok',
                extra_data: {
                  direction: 'source_to_target',
                  is_gpon_branch: true,
                  cable_type: 'fiber',
                  gpon_port: 'GPON 0/1/0',
                  gpon_index: '4194312192',
                  olt_ip: defaultOltIp,
                  olt_name: defaultOltName
                }
              });
              if (!currentMap.links) currentMap.links = [];
              currentMap.links.push(newLink);
              const dict = new Map();
              currentMap.nodes.forEach(n => dict.set(n.id, n));
              renderLink(newLink, dict);
              updateAllLinks();
              if (linksLayer) linksLayer.batchDraw();
            } catch (linkErr) {
              console.warn('Error auto-vinculando arista al crear:', linkErr);
            }
          }
        }

        selectNode(newBranch);
        openFtthBranchEditorModal(newBranch);
      } catch(err) {
        alert('Error creando brazo FTTH: ' + err.message);
      }
    });
  }

  // Botón Insertar Acceso a Mapa Padre en lienzo
  const btnEnsureParent = document.getElementById('btn-ensure-parent-node');
  if (btnEnsureParent) {
    btnEnsureParent.addEventListener('click', async () => {
      if (!currentMap || !currentMap.parent_map_id) {
        alert('Este mapa no tiene un mapa padre asociado.');
        return;
      }
      try {
        const res = await API.ensureParentNode(currentMap.id);
        alert(res.message);
        await loadMap(currentMap.id);
      } catch (err) {
        alert('Error insertando acceso a mapa padre: ' + err.message);
      }
    });
  }

  // Botón Conectar Enlace
  document.getElementById('btn-toggle-link-mode').addEventListener('click', () => {
    if (linkMode) {
      cancelLinkMode();
    } else {
      startLinkMode();
    }
  });
  document.getElementById('btn-cancel-link').addEventListener('click', cancelLinkMode);

  const btnConfirmMultiLink = document.getElementById('btn-confirm-multi-link');
  if (btnConfirmMultiLink) {
    btnConfirmMultiLink.addEventListener('click', async () => {
      if (linkSourceNode && linkTargetNodes.size > 0) {
        await connectMultipleTargetNodes(linkSourceNode, Array.from(linkTargetNodes));
      }
    });
  }

  // Botón Imantar Cuadrícula
  const btnGrid = document.getElementById('btn-toggle-grid');
  btnGrid.addEventListener('click', () => {
    snapToGrid = !snapToGrid;
    btnGrid.classList.toggle('btn-active', snapToGrid);
    btnGrid.querySelector('span').textContent = snapToGrid ? 'Imantar (20px)' : 'Libre';
  });

  // Botón Subir Nivel (Level Up)
  document.getElementById('btn-level-up').addEventListener('click', () => {
    if (currentMap && currentMap.parent_map_id) {
      loadMap(currentMap.parent_map_id);
    }
  });

  // Botones de Zoom flotantes
  document.getElementById('btn-zoom-in').addEventListener('click', () => {
    stage.scale({ x: stage.scaleX() * 1.2, y: stage.scaleY() * 1.2 });
    stage.batchDraw();
  });
  document.getElementById('btn-zoom-out').addEventListener('click', () => {
    stage.scale({ x: stage.scaleX() / 1.2, y: stage.scaleY() / 1.2 });
    stage.batchDraw();
  });
  document.getElementById('btn-zoom-reset').addEventListener('click', () => {
    stage.scale({ x: 1, y: 1 });
    stage.position({ x: 0, y: 0 });
    stage.batchDraw();
  });

  // Inspector de Propiedades: Quitar nodo del mapa (Solo retira del lienzo visual)
  const btnDeleteNode = document.getElementById('btn-delete-node');
  if (btnDeleteNode) {
    btnDeleteNode.addEventListener('click', async () => {
      if (!selectedNode) return;
      if (!confirm(`¿Estás seguro de quitar "${selectedNode.name}" de la topología?\n\nNota: Solo se retira del mapa visual de NexusDude, el dispositivo y su configuración permanecen intactos en NetBox.`)) return;

      try {
        await API.deleteNode(selectedNode.id);
        const grp = nodeGroups.get(selectedNode.id);
        if (grp) grp.destroy();
        nodeGroups.delete(selectedNode.id);

        // Eliminar enlaces conectados al nodo
        linkLines.forEach(({ line, sourceId, targetId }, linkId) => {
          if (sourceId === selectedNode.id || targetId === selectedNode.id) {
            line.destroy();
            linkLines.delete(linkId);
          }
        });

        currentMap.nodes = currentMap.nodes.filter(n => n.id !== selectedNode.id);
        deselectNode();
        nodesLayer.batchDraw();
        updateAllLinks();

      } catch (err) {
        alert('Error quitando nodo: ' + err.message);
      }
    });
  }

  // Inspector de Propiedades: Eliminar todas las conexiones del nodo seleccionado
  const btnDeleteNodeLinks = document.getElementById('btn-delete-node-links');
  if (btnDeleteNodeLinks) {
    btnDeleteNodeLinks.addEventListener('click', async () => {
      if (!selectedNode) return;
      const targetLinks = [];
      linkLines.forEach(({ line, sourceId, targetId }, linkId) => {
        if (sourceId === selectedNode.id || targetId === selectedNode.id) {
          targetLinks.push({ linkId, line });
        }
      });

      if (targetLinks.length === 0) {
        alert(`El nodo "${selectedNode.name}" no tiene conexiones activas.`);
        return;
      }

      if (!confirm(`¿Eliminar las ${targetLinks.length} conexión(es) asociadas a "${selectedNode.name}"?`)) return;

      try {
        let deletedCount = 0;
        for (const { linkId, line } of targetLinks) {
          await API.deleteLink(linkId);
          line.destroy();
          linkLines.delete(linkId);
          deletedCount++;
        }

        if (currentMap && currentMap.links) {
          const deletedIds = new Set(targetLinks.map(t => t.linkId));
          currentMap.links = currentMap.links.filter(l => !deletedIds.has(l.id));
        }

        linksLayer.batchDraw();
        alert(`Se eliminaron ${deletedCount} conexión(es) correctamente.`);
      } catch (err) {
        alert('Error eliminando conexiones: ' + err.message);
      }
    });
  }

  // Inspector de Propiedades: Convertir nodo regular a Submapa
  const btnConvertSubmap = document.getElementById('btn-convert-node-to-submap');
  if (btnConvertSubmap) {
    btnConvertSubmap.addEventListener('click', openConvertSubmapModal);
  }

  // Handlers para el Modal de Convertir a Submapa
  const inputSearchConvert = document.getElementById('input-search-convert-existing-map');
  if (inputSearchConvert) {
    inputSearchConvert.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      const selectElem = document.getElementById('select-convert-existing-map');
      if (!selectElem) return;

      Array.from(selectElem.options).forEach(opt => {
        if (!opt.value) return; // Mantener opción por defecto si aplica
        const text = opt.textContent.toLowerCase();
        opt.style.display = text.includes(q) ? '' : 'none';
      });
    });
  }

  const selectConvertMode = document.getElementById('select-convert-mode');
  if (selectConvertMode) {
    selectConvertMode.addEventListener('change', (e) => {
      const isNew = e.target.value === 'create_new';
      document.getElementById('convert-section-create-new').style.display = isNew ? 'block' : 'none';
      document.getElementById('convert-section-link-existing').style.display = isNew ? 'none' : 'block';
    });
  }

  const btnCloseConvertX = document.getElementById('btn-close-convert-submap-x');
  if (btnCloseConvertX) {
    btnCloseConvertX.addEventListener('click', () => {
      document.getElementById('modal-convert-submap').style.display = 'none';
    });
  }

  const btnCloseConvert = document.getElementById('btn-close-convert-submap-modal');
  if (btnCloseConvert) {
    btnCloseConvert.addEventListener('click', () => {
      document.getElementById('modal-convert-submap').style.display = 'none';
    });
  }

  const btnExecConvert = document.getElementById('btn-exec-convert-submap');
  if (btnExecConvert) {
    btnExecConvert.addEventListener('click', handleExecuteConvertSubmap);
  }

  // Botón Entrar al Submapa desde Inspector
  const btnEnterSubmap = document.getElementById('btn-enter-submap');
  if (btnEnterSubmap) {
    btnEnterSubmap.addEventListener('click', () => {
      if (selectedNode && selectedNode.extra_data?.target_map_id) {
        loadMap(selectedNode.extra_data.target_map_id);
      }
    });
  }

  // Modal Sincronización Zabbix (BSM)
  const btnOpenZbx = document.getElementById('btn-open-zabbix-sync');
  if (btnOpenZbx) btnOpenZbx.addEventListener('click', openZabbixSyncModal);

  const btnCloseZbx1 = document.getElementById('btn-close-zabbix-modal');
  if (btnCloseZbx1) btnCloseZbx1.addEventListener('click', () => {
    document.getElementById('modal-zabbix-sync').style.display = 'none';
  });

  const btnCloseZbx2 = document.getElementById('btn-close-zbx-modal');
  if (btnCloseZbx2) btnCloseZbx2.addEventListener('click', () => {
    document.getElementById('modal-zabbix-sync').style.display = 'none';
  });

  const btnExecZbx = document.getElementById('btn-exec-zbx-sync');
  if (btnExecZbx) btnExecZbx.addEventListener('click', handleExecuteZabbixSync);

  const btnClearZbx = document.getElementById('btn-clear-zbx-services');
  if (btnClearZbx) btnClearZbx.addEventListener('click', handleClearZabbixServices);

  // Modal Generación Automática de Mapas desde Sitios NetBox
  const btnOpenBulk = document.getElementById('btn-open-bulk-sites-modal');
  if (btnOpenBulk) btnOpenBulk.addEventListener('click', openBulkSitesModal);

  const btnCloseBulkX = document.getElementById('btn-close-bulk-sites-modal');
  if (btnCloseBulkX) btnCloseBulkX.addEventListener('click', () => {
    document.getElementById('modal-bulk-sites').style.display = 'none';
  });

  const btnCloseBulk = document.getElementById('btn-close-bulk-modal');
  if (btnCloseBulk) btnCloseBulk.addEventListener('click', () => {
    document.getElementById('modal-bulk-sites').style.display = 'none';
  });

  const btnExecBulk = document.getElementById('btn-exec-bulk-sites');
  if (btnExecBulk) btnExecBulk.addEventListener('click', handleExecuteBulkSites);

  const btnReconfigWh = document.getElementById('btn-reconfigure-webhook');
  if (btnReconfigWh) btnReconfigWh.addEventListener('click', handleSetupWebhook);

  // Modal RBAC & Permisos
  const btnOpenRbac = document.getElementById('btn-open-rbac-modal');
  if (btnOpenRbac) btnOpenRbac.addEventListener('click', openRBACModal);

  const btnCloseRbacX = document.getElementById('btn-close-rbac-x');
  if (btnCloseRbacX) btnCloseRbacX.addEventListener('click', () => {
    document.getElementById('modal-rbac').style.display = 'none';
  });

  const btnCloseRbacModal = document.getElementById('btn-close-rbac-modal');
  if (btnCloseRbacModal) btnCloseRbacModal.addEventListener('click', () => {
    document.getElementById('modal-rbac').style.display = 'none';
  });

  const btnSaveRole = document.getElementById('btn-save-new-role');
  if (btnSaveRole) btnSaveRole.addEventListener('click', handleSaveNewRole);

  // ═══ CONTROLES DEL ANALIZADOR DE ESPECTRO RF (4850 - 7250 MHz) ═══
  const selectSpecMap = document.getElementById('select-spectrum-map');
  if (selectSpecMap) {
    selectSpecMap.addEventListener('change', (e) => {
      loadSpectrumData(e.target.value);
    });
  }

  const btnRefreshSpec = document.getElementById('btn-refresh-spectrum');
  if (btnRefreshSpec) {
    btnRefreshSpec.addEventListener('click', () => {
      const val = selectSpecMap ? selectSpecMap.value : 'current';
      loadSpectrumData(val);
    });
  }

  const inputSearchSpec = document.getElementById('input-search-spectrum-devices');
  if (inputSearchSpec) {
    inputSearchSpec.addEventListener('input', (e) => {
      spectrumFilterText = e.target.value;
      renderSpectrumDeviceList();
      renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
      const modalCanvas = document.getElementById('spectrum-modal-ruler-canvas');
      if (modalCanvas && document.getElementById('modal-spectrum-fullscreen').style.display === 'flex') {
        renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
      }
    });
  }

  const selectRoleFilter = document.getElementById('select-spectrum-role-filter');
  if (selectRoleFilter) {
    selectRoleFilter.addEventListener('change', (e) => {
      spectrumRoleFilter = e.target.value;
      renderSpectrumDeviceList();
      renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
      const modalCanvas = document.getElementById('spectrum-modal-ruler-canvas');
      if (modalCanvas && document.getElementById('modal-spectrum-fullscreen').style.display === 'flex') {
        renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
      }
    });
  }

  const btnSpecSelectAll = document.getElementById('btn-spectrum-select-all');
  if (btnSpecSelectAll) {
    btnSpecSelectAll.addEventListener('click', () => {
      if (spectrumData && spectrumData.devices) {
        spectrumData.devices.forEach(d => spectrumSelectedDeviceIds.add(d.node_id));
        renderSpectrumDeviceList();
        renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
        const modalCanvas = document.getElementById('spectrum-modal-ruler-canvas');
        if (modalCanvas && document.getElementById('modal-spectrum-fullscreen').style.display === 'flex') {
          renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
        }
      }
    });
  }

  const btnSpecDeselectAll = document.getElementById('btn-spectrum-deselect-all');
  if (btnSpecDeselectAll) {
    btnSpecDeselectAll.addEventListener('click', () => {
      spectrumSelectedDeviceIds.clear();
      renderSpectrumDeviceList();
      renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
      const modalCanvas = document.getElementById('spectrum-modal-ruler-canvas');
      if (modalCanvas && document.getElementById('modal-spectrum-fullscreen').style.display === 'flex') {
        renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
      }
    });
  }

  // Zoom de la regla en el Sidebar
  const btnSpecZoomIn = document.getElementById('btn-spec-zoom-in');
  if (btnSpecZoomIn) {
    btnSpecZoomIn.addEventListener('click', () => {
      if (spectrumSidebarZoom < 3.0) {
        spectrumSidebarZoom += 0.25;
        const lbl = document.getElementById('spec-zoom-label');
        if (lbl) lbl.textContent = `${Math.round(spectrumSidebarZoom * 100)}%`;
        renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
      }
    });
  }

  const btnSpecZoomOut = document.getElementById('btn-spec-zoom-out');
  if (btnSpecZoomOut) {
    btnSpecZoomOut.addEventListener('click', () => {
      if (spectrumSidebarZoom > 0.6) {
        spectrumSidebarZoom -= 0.25;
        const lbl = document.getElementById('spec-zoom-label');
        if (lbl) lbl.textContent = `${Math.round(spectrumSidebarZoom * 100)}%`;
        renderSpectrumRuler('spectrum-sidebar-ruler-canvas', false);
      }
    });
  }

  // Modal de Regla de Espectro en Pantalla Completa
  const btnOpenSpecModal = document.getElementById('btn-open-spectrum-modal');
  if (btnOpenSpecModal) {
    btnOpenSpecModal.addEventListener('click', openSpectrumModal);
  }

  const btnCloseSpecModalX = document.getElementById('btn-close-spectrum-modal-x');
  if (btnCloseSpecModalX) {
    btnCloseSpecModalX.addEventListener('click', () => {
      document.getElementById('modal-spectrum-fullscreen').style.display = 'none';
    });
  }

  const btnCloseSpecModal = document.getElementById('btn-close-spectrum-modal');
  if (btnCloseSpecModal) {
    btnCloseSpecModal.addEventListener('click', () => {
      document.getElementById('modal-spectrum-fullscreen').style.display = 'none';
    });
  }

  const btnModalSpecZoomIn = document.getElementById('btn-modal-spec-zoom-in');
  if (btnModalSpecZoomIn) {
    btnModalSpecZoomIn.addEventListener('click', () => {
      if (spectrumModalZoom < 4.0) {
        spectrumModalZoom += 0.25;
        const lbl = document.getElementById('modal-spec-zoom-label');
        if (lbl) lbl.textContent = `${Math.round(spectrumModalZoom * 100)}%`;
        renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
      }
    });
  }

  const btnModalSpecZoomOut = document.getElementById('btn-modal-spec-zoom-out');
  if (btnModalSpecZoomOut) {
    btnModalSpecZoomOut.addEventListener('click', () => {
      if (spectrumModalZoom > 0.5) {
        spectrumModalZoom -= 0.25;
        const lbl = document.getElementById('modal-spec-zoom-label');
        if (lbl) lbl.textContent = `${Math.round(spectrumModalZoom * 100)}%`;
        renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
      }
    });
  }

  const btnModalSpecZoomReset = document.getElementById('btn-modal-spec-zoom-reset');
  if (btnModalSpecZoomReset) {
    btnModalSpecZoomReset.addEventListener('click', () => {
      spectrumModalZoom = 1.0;
      const lbl = document.getElementById('modal-spec-zoom-label');
      if (lbl) lbl.textContent = '100%';
      renderSpectrumRuler('spectrum-modal-ruler-canvas', true);
    });
  }


  // Atajos de Teclado Globales (Delete/Backspace para eliminar nodos, Escape para cancelar selección)
  window.addEventListener('keydown', (e) => {
    // Ignorar si el usuario está escribiendo en un input, textarea o select
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target && e.target.isContentEditable)) {
      return;
    }

    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (selectedNodes.size > 0) {
        e.preventDefault();
        const btnBulkDel = document.getElementById('btn-bulk-delete-nodes');
        if (btnBulkDel && !btnBulkDel.disabled) {
          btnBulkDel.click();
        }
      } else if (selectedNode) {
        e.preventDefault();
        const btnSingleDel = document.getElementById('btn-delete-node');
        if (btnSingleDel) {
          btnSingleDel.click();
        }
      }
    } else if (e.key === 'Escape') {
      if (linkMode) {
        cancelLinkMode();
      } else if (selectedNodes.size > 0) {
        clearMultiSelection();
      } else if (selectedNode) {
        deselectNode();
      }
    } else if (e.key === 'Enter') {
      if (linkMode && linkSourceNode && linkTargetNodes.size > 0) {
        e.preventDefault();
        const btnMulti = document.getElementById('btn-confirm-multi-link');
        if (btnMulti) btnMulti.click();
      }
    }
  });

  // ─── 22. Menú Desplegable de Sincronización en Toolbar ─────────────────────
  const btnToggleSyncMenu = document.getElementById('btn-toggle-sync-menu');
  const dropdownSyncContainer = document.getElementById('dropdown-sync-container');

  if (btnToggleSyncMenu && dropdownSyncContainer) {
    btnToggleSyncMenu.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdownSyncContainer.classList.toggle('open');
    });

    // Cerrar el menú desplegable al hacer clic en cualquier opción interna
    const dropdownItems = dropdownSyncContainer.querySelectorAll('.dropdown-item');
    dropdownItems.forEach(item => {
      item.addEventListener('click', () => {
        dropdownSyncContainer.classList.remove('open');
      });
    });

    // Cerrar si se hace clic fuera del menú
    document.addEventListener('click', (e) => {
      if (!dropdownSyncContainer.contains(e.target)) {
        dropdownSyncContainer.classList.remove('open');
      }
    });
  }

  // ─── 23. Modal de Configuración e Integración i-WISP ───────────────────────
  // ─── Modal de Configuración General (NetBox y Zabbix) ─────────────────────
  const modalSettings = document.getElementById('modal-settings');
  const btnOpenSettings = document.getElementById('btn-open-settings');
  const btnCloseSettingsModal = document.getElementById('btn-close-settings-modal');
  const btnCancelSettings = document.getElementById('btn-cancel-settings');
  const btnSaveSettings = document.getElementById('btn-save-settings');

  // Pestañas
  const tabBtnNetbox = document.getElementById('tab-btn-netbox');
  const tabBtnZabbix = document.getElementById('tab-btn-zabbix');
  const panelNetbox = document.getElementById('panel-settings-netbox');
  const panelZabbix = document.getElementById('panel-settings-zabbix');

  // Campos NetBox
  const inputNetboxUrl = document.getElementById('input-netbox-url');
  const inputNetboxToken = document.getElementById('input-netbox-token');
  const btnToggleNetboxTokenVis = document.getElementById('btn-toggle-netbox-token-vis');
  const btnTestNetboxConn = document.getElementById('btn-test-netbox-conn');
  const alertNetboxMsg = document.getElementById('netbox-msg-alert');
  const badgeNetboxStatus = document.getElementById('badge-netbox-status');

  // Campos Zabbix
  const inputZabbixUrl = document.getElementById('input-zabbix-url');
  const inputZabbixUser = document.getElementById('input-zabbix-user');
  const inputZabbixPass = document.getElementById('input-zabbix-pass');
  const btnToggleZabbixPassVis = document.getElementById('btn-toggle-zabbix-pass-vis');
  const btnTestZabbixConn = document.getElementById('btn-test-zabbix-conn');
  const alertZabbixMsg = document.getElementById('zabbix-msg-alert');
  const badgeZabbixStatus = document.getElementById('badge-zabbix-status');

  // Alerta global
  const alertGlobalSettings = document.getElementById('settings-global-alert');

  function showPanelAlert(element, msg, type = 'info') {
    if (!element) return;
    element.style.display = 'block';
    element.textContent = msg;
    if (type === 'success') {
      element.style.background = 'rgba(16, 185, 129, 0.15)';
      element.style.color = '#10b981';
      element.style.border = '1px solid rgba(16, 185, 129, 0.3)';
    } else if (type === 'error') {
      element.style.background = 'rgba(239, 68, 68, 0.15)';
      element.style.color = '#ef4444';
      element.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    } else {
      element.style.background = 'rgba(14, 165, 233, 0.15)';
      element.style.color = '#38bdf8';
      element.style.border = '1px solid rgba(14, 165, 233, 0.3)';
    }
  }

  // Cambio de pestañas en el Modal de Configuración
  function switchSettingsTab(tabName) {
    if (tabName === 'netbox') {
      if (tabBtnNetbox) {
        tabBtnNetbox.classList.add('active');
        tabBtnNetbox.style.borderBottom = '2px solid var(--accent)';
        tabBtnNetbox.style.background = 'var(--bg-secondary)';
        tabBtnNetbox.style.color = '#f8fafc';
      }
      if (tabBtnZabbix) {
        tabBtnZabbix.classList.remove('active');
        tabBtnZabbix.style.borderBottom = '2px solid transparent';
        tabBtnZabbix.style.background = 'transparent';
        tabBtnZabbix.style.color = 'var(--text-muted)';
      }
      if (panelNetbox) panelNetbox.style.display = 'block';
      if (panelZabbix) panelZabbix.style.display = 'none';
    } else if (tabName === 'zabbix') {
      if (tabBtnZabbix) {
        tabBtnZabbix.classList.add('active');
        tabBtnZabbix.style.borderBottom = '2px solid var(--accent)';
        tabBtnZabbix.style.background = 'var(--bg-secondary)';
        tabBtnZabbix.style.color = '#f8fafc';
      }
      if (tabBtnNetbox) {
        tabBtnNetbox.classList.remove('active');
        tabBtnNetbox.style.borderBottom = '2px solid transparent';
        tabBtnNetbox.style.background = 'transparent';
        tabBtnNetbox.style.color = 'var(--text-muted)';
      }
      if (panelZabbix) panelZabbix.style.display = 'block';
      if (panelNetbox) panelNetbox.style.display = 'none';
    }
  }

  if (tabBtnNetbox) {
    tabBtnNetbox.addEventListener('click', () => switchSettingsTab('netbox'));
  }
  if (tabBtnZabbix) {
    tabBtnZabbix.addEventListener('click', () => switchSettingsTab('zabbix'));
  }

  // Visibilidad de contraseñas / tokens
  if (btnToggleNetboxTokenVis && inputNetboxToken) {
    btnToggleNetboxTokenVis.addEventListener('click', () => {
      const isPass = inputNetboxToken.type === 'password';
      inputNetboxToken.type = isPass ? 'text' : 'password';
      const icon = btnToggleNetboxTokenVis.querySelector('i');
      if (icon) icon.className = isPass ? 'fas fa-eye-slash' : 'fas fa-eye';
    });
  }

  if (btnToggleZabbixPassVis && inputZabbixPass) {
    btnToggleZabbixPassVis.addEventListener('click', () => {
      const isPass = inputZabbixPass.type === 'password';
      inputZabbixPass.type = isPass ? 'text' : 'password';
      const icon = btnToggleZabbixPassVis.querySelector('i');
      if (icon) icon.className = isPass ? 'fas fa-eye-slash' : 'fas fa-eye';
    });
  }

  // Carga de configuración inicial desde el Backend
  async function loadSettingsData() {
    if (alertNetboxMsg) alertNetboxMsg.style.display = 'none';
    if (alertZabbixMsg) alertZabbixMsg.style.display = 'none';
    if (alertGlobalSettings) alertGlobalSettings.style.display = 'none';

    try {
      const data = await API.getIntegrationsConfig();
      if (!data) return;

      // NetBox: IP y API Token
      if (data.netbox) {
        if (inputNetboxUrl && data.netbox.url) inputNetboxUrl.value = data.netbox.url;
        if (inputNetboxToken && data.netbox.token) inputNetboxToken.value = data.netbox.token;
      }

      // Zabbix: IP y Credenciales API
      if (data.zabbix) {
        if (inputZabbixUrl && data.zabbix.url) inputZabbixUrl.value = data.zabbix.url;
        if (inputZabbixUser && data.zabbix.user) inputZabbixUser.value = data.zabbix.user;
        if (inputZabbixPass && data.zabbix.pass) inputZabbixPass.value = data.zabbix.pass;
      }
    } catch (err) {
      console.error('Error cargando configuraciones de integraciones:', err);
    }
  }

  window.openSettingsModal = function() {
    const modal = document.getElementById('modal-settings');
    if (modal) {
      modal.style.display = 'flex';
      switchSettingsTab('netbox');
      loadSettingsData();
    }
  };

  window.closeSettingsModal = function() {
    const modal = document.getElementById('modal-settings');
    if (modal) {
      modal.style.display = 'none';
    }
  };

  if (btnOpenSettings) {
    btnOpenSettings.addEventListener('click', window.openSettingsModal);
  }

  if (btnCloseSettingsModal) {
    btnCloseSettingsModal.addEventListener('click', window.closeSettingsModal);
  }

  if (btnCancelSettings) {
    btnCancelSettings.addEventListener('click', window.closeSettingsModal);
  }

  // Prueba de Conexión NetBox
  if (btnTestNetboxConn) {
    btnTestNetboxConn.addEventListener('click', async () => {
      const url = inputNetboxUrl ? inputNetboxUrl.value.trim() : '';
      const token = inputNetboxToken ? inputNetboxToken.value.trim() : '';

      btnTestNetboxConn.disabled = true;
      btnTestNetboxConn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Conectando...';

      try {
        const res = await API.testNetboxConnection({ url, token });
        if (res.success) {
          showPanelAlert(alertNetboxMsg, '✅ ' + res.message, 'success');
          if (badgeNetboxStatus) {
            badgeNetboxStatus.textContent = res.version ? `v${res.version}` : 'Conectado';
            badgeNetboxStatus.style.background = 'rgba(16, 185, 129, 0.15)';
            badgeNetboxStatus.style.color = '#10b981';
          }
        } else {
          showPanelAlert(alertNetboxMsg, '❌ ' + (res.message || 'Error de conexión'), 'error');
        }
      } catch (err) {
        showPanelAlert(alertNetboxMsg, '❌ Error al probar NetBox: ' + err.message, 'error');
      } finally {
        btnTestNetboxConn.disabled = false;
        btnTestNetboxConn.innerHTML = '<i class="fas fa-vial"></i> Probar Conexión NetBox';
      }
    });
  }

  // Prueba de Conexión Zabbix
  if (btnTestZabbixConn) {
    btnTestZabbixConn.addEventListener('click', async () => {
      const url = inputZabbixUrl ? inputZabbixUrl.value.trim() : '';
      const user = inputZabbixUser ? inputZabbixUser.value.trim() : '';
      const pass = inputZabbixPass ? inputZabbixPass.value : '';

      btnTestZabbixConn.disabled = true;
      btnTestZabbixConn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Conectando...';

      try {
        const res = await API.testZabbixConnection({ url, user, pass });
        if (res.success) {
          showPanelAlert(alertZabbixMsg, '✅ ' + res.message, 'success');
          if (badgeZabbixStatus) {
            badgeZabbixStatus.textContent = res.version ? `v${res.version}` : 'Conectado';
            badgeZabbixStatus.style.background = 'rgba(16, 185, 129, 0.15)';
            badgeZabbixStatus.style.color = '#10b981';
          }
        } else {
          showPanelAlert(alertZabbixMsg, '❌ ' + (res.message || 'Error de conexión'), 'error');
        }
      } catch (err) {
        showPanelAlert(alertZabbixMsg, '❌ Error al probar Zabbix: ' + err.message, 'error');
      } finally {
        btnTestZabbixConn.disabled = false;
        btnTestZabbixConn.innerHTML = '<i class="fas fa-vial"></i> Probar Conexión Zabbix';
      }
    });
  }

  // Guardar ambas configuraciones (NetBox y Zabbix)
  if (btnSaveSettings) {
    btnSaveSettings.addEventListener('click', async () => {
      btnSaveSettings.disabled = true;
      btnSaveSettings.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Guardando...';

      try {
        const netboxPayload = {
          url: inputNetboxUrl ? inputNetboxUrl.value.trim() : '',
          token: inputNetboxToken ? inputNetboxToken.value.trim() : ''
        };

        const zabbixPayload = {
          url: inputZabbixUrl ? inputZabbixUrl.value.trim() : '',
          user: inputZabbixUser ? inputZabbixUser.value.trim() : '',
          pass: inputZabbixPass ? inputZabbixPass.value : ''
        };

        const [resNetbox, resZabbix] = await Promise.all([
          API.saveNetboxConfig(netboxPayload),
          API.saveZabbixConfig(zabbixPayload)
        ]);

        showPanelAlert(alertGlobalSettings, '✅ Configuraciones de IP y API guardadas exitosamente.', 'success');
        await loadSettingsData();
      } catch (err) {
        showPanelAlert(alertGlobalSettings, '❌ Error al guardar configuraciones: ' + err.message, 'error');
      } finally {
        btnSaveSettings.disabled = false;
        btnSaveSettings.innerHTML = '<i class="fas fa-save"></i> Guardar Configuración';
      }
    });
  }

  // Navegación con historial del navegador (Atrás / Adelante)
  window.addEventListener('hashchange', async () => {
    const hashParams = new URLSearchParams((window.location.hash || '').replace(/^#/, ''));
    const hashMapId = hashParams.get('map');
    const hashTab = hashParams.get('tab');
    if (hashMapId && currentMap && currentMap.id !== hashMapId) {
      await loadMap(hashMapId);
    }
    if (hashTab) {
      const activeTabEl = document.querySelector('.sidebar-tab.active');
      if (!activeTabEl || activeTabEl.dataset.tab !== hashTab) {
        switchTab(hashTab);
      }
    }
  });
});


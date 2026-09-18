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

// Referencias de shapes en Konva
const nodeGroups = new Map();
const linkLines = new Map();

// Estado de selección
let selectedNode = null;
const selectedNodes = new Set(); // Conjunto de nodos en selección múltiple
let selectionRect = null;
let isSelectingWithRightClick = false;
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

// ─── 1. Autenticación SSO y Detección de Tokens ─────────────────────────────
function initSSOAuth() {
  const hash = window.location.hash;
  const urlParams = new URLSearchParams(window.location.search);
  let foundToken = null;

  if (hash) {
    const hashParams = new URLSearchParams(hash.replace(/^#/, ''));
    foundToken = hashParams.get('token') || hashParams.get('access_token');
  }
  if (!foundToken) {
    foundToken = urlParams.get('token');
  }

  if (foundToken) {
    API.setToken(foundToken);
    history.replaceState(null, null, window.location.pathname);
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
    if (e.evt && e.evt.button === 2) {
      e.evt.preventDefault();
      isSelectingWithRightClick = true;
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
    if (!isSelectingWithRightClick) return;
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

  // Evento Mouseup: Finalizar selección de área y marcar nodos encerrados
  const finishSelection = (e) => {
    if (isSelectingWithRightClick) {
      isSelectingWithRightClick = false;
      const selW = selectionRect.width();
      const selH = selectionRect.height();
      const selX = selectionRect.x();
      const selY = selectionRect.y();

      selectionRect.visible(false);
      uiLayer.batchDraw();
      stage.draggable(true);

      if (selW > 5 && selH > 5) {
        deselectNode();
        clearMultiSelection();

        // Recorrer todos los nodos del mapa actual y verificar intersección por coordenadas
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
                addNodeToMultiSelection(node);
              }
            }
          });
        }

        if (selectedNodes.size > 0) {
          showMultiSelectionNotice(selectedNodes.size);
        } else {
          hideMultiSelectionNotice();
        }
      }
    }
  };

  stage.on('mouseup touchend', finishSelection);
  window.addEventListener('mouseup', (e) => {
    if (isSelectingWithRightClick) finishSelection(e);
  });

  // Deseleccionar al hacer clic izquierdo en fondo vacío
  stage.on('click tap', (e) => {
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

// ─── 4. Renderizado de Nodos y Enlaces en Konva ──────────────────────────────
function getRoleIcon(deviceType = '') {
  const t = deviceType.toLowerCase();
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
  const isSubmap = node.device_type === 'submap';
  const nameFont = 'bold 11px system-ui, -apple-system, sans-serif';
  const nameW = measureTextWidth(node.name, nameFont);

  let subLabelText = '';
  if (isSubmap) {
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

  // Margen izquierdo del título: statusDot (14px) + gap + icon (16px) + gap = 44px + text + margen derecho (16px)
  const titleNeeded = 44 + nameW + 16;
  // Margen izquierdo del subtítulo: 14px + subW + margen derecho (16px)
  const subNeeded = 14 + subW + 16;

  const minWidth = isSubmap ? 150 : 136;
  const maxWidth = isSubmap ? 290 : 250;
  const nodeWidth = Math.min(Math.max(minWidth, Math.ceil(Math.max(titleNeeded, subNeeded))), maxWidth);
  const nodeHeight = isSubmap ? 56 : 52;

  return { nodeWidth, nodeHeight, subLabelText };
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

function renderNode(node) {
  const isSubmap = node.device_type === 'submap';
  const { nodeWidth, nodeHeight, subLabelText } = computeNodeDimensions(node);

  const group = new Konva.Group({
    x: node.x,
    y: node.y,
    draggable: true,
    id: node.id
  });
  group.isSubmap = isSubmap;

  // Obtener color del estado del nodo (online/offline/warning)
  const nodeStatusColor = getNodeStatusColor(node.status, isSubmap);

  // Caja de fondo: relleno púrpura para submapas y contorno con color de estado (Red/Yellow/Green)
  const box = new Konva.Rect({
    width: nodeWidth,
    height: nodeHeight,
    fill: isSubmap ? 'rgba(74, 14, 122, 0.75)' : '#162235',
    stroke: nodeStatusColor,
    strokeWidth: 1.5,
    cornerRadius: 8,
    shadowColor: 'rgba(0, 0, 0, 0.45)',
    shadowBlur: 0,
    shadowOpacity: 0.4,
    shadowOffset: { x: 0, y: 2 },
    shadowForStrokeEnabled: false,
    perfectDrawEnabled: false,
    name: 'box'
  });

  // Indicador de estado circular
  const statusDot = new Konva.Circle({
    x: 14,
    y: isSubmap ? 16 : 15,
    radius: 4.5,
    fill: nodeStatusColor,
    listening: false,
    perfectDrawEnabled: false
  });

  // Icono
  const iconEmoji = getRoleIcon(node.device_type);
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
    fill: '#f8fafc',
    listening: false,
    perfectDrawEnabled: false,
    name: 'label'
  });

  // Subtexto (cantidad de equipos o IP)
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
    fill: isSubmap ? '#c084fc' : '#38bdf8',
    listening: false,
    perfectDrawEnabled: false,
    name: 'ipText'
  });

  group.add(box);
  group.add(statusDot);
  group.add(iconText);
  group.add(label);
  group.add(ipText);

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
    } catch (err) {
      console.error('Error guardando posición:', err);
    }
  });

  // Clic en nodo: selección individual o conexión de enlace
  group.on('click tap', (e) => {
    e.cancelBubble = true;
    if (linkMode) {
      handleLinkNodeClick(node);
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

  // Doble clic: drill-down si es submapa
  group.on('dblclick dbltap', () => {
    if (isSubmap && node.extra_data && node.extra_data.target_map_id) {
      loadMap(node.extra_data.target_map_id);
    }
  });

  // Hover visual optimizado en nodesLayer
  group.on('mouseenter', () => {
    document.body.style.cursor = linkMode ? 'crosshair' : 'pointer';
    if (!box.isHighlighted) {
      box.stroke('#38bdf8');
      nodesLayer.batchDraw();
    }
  });
  group.on('mouseleave', () => {
    document.body.style.cursor = 'default';
    if (!box.isHighlighted) {
      box.stroke(getNodeStatusColor(node.status, isSubmap));
      nodesLayer.batchDraw();
    }
  });

  nodesLayer.add(group);
  nodeGroups.set(node.id, group);
}

function renderLink(link, nodesDict) {
  const source = nodesDict.get(link.source_node_id);
  const target = nodesDict.get(link.target_node_id);
  if (!source || !target) return;

  const srcHalf = getNodeHalfDimensions(source);
  const tgtHalf = getNodeHalfDimensions(target);

  const line = new Konva.Line({
    points: [
      source.x + srcHalf.halfW,
      source.y + srcHalf.halfH,
      target.x + tgtHalf.halfW,
      target.y + tgtHalf.halfH
    ],
    stroke: link.status === 'ok' ? '#0ea5e9' : '#ef4444',
    strokeWidth: 2,
    hitStrokeWidth: 12,
    lineCap: 'round',
    lineJoin: 'round',
    perfectDrawEnabled: false,
    id: link.id
  });

  line.on('mouseenter', () => {
    document.body.style.cursor = 'pointer';
    line.stroke('#f43f5e');
    line.strokeWidth(3.5);
    linksLayer.batchDraw();
  });

  line.on('mouseleave', () => {
    document.body.style.cursor = 'default';
    line.stroke(link.status === 'ok' ? '#0ea5e9' : '#ef4444');
    line.strokeWidth(2);
    linksLayer.batchDraw();
  });

  line.on('click tap', async (e) => {
    e.cancelBubble = true;
    const srcName = source.name || 'Nodo Origen';
    const tgtName = target.name || 'Nodo Destino';
    if (confirm(`¿Deseas eliminar el enlace entre "${srcName}" y "${tgtName}"?`)) {
      try {
        await API.deleteLink(link.id);
        line.destroy();
        linkLines.delete(link.id);
        if (currentMap && currentMap.links) {
          currentMap.links = currentMap.links.filter(l => l.id !== link.id);
        }
        linksLayer.batchDraw();
      } catch (err) {
        alert('Error eliminando enlace: ' + err.message);
      }
    }
  });

  linksLayer.add(line);
  linkLines.set(link.id, { line, sourceId: source.id, targetId: target.id });
}

function updateAttachedLinks(nodeId, newX, newY) {
  let hasUpdated = false;
  const { halfW, halfH } = getNodeHalfDimensions(nodeId);

  linkLines.forEach(({ line, sourceId, targetId }) => {
    if (sourceId === nodeId) {
      const points = line.points();
      points[0] = newX + halfW;
      points[1] = newY + halfH;
      line.points(points);
      hasUpdated = true;
    } else if (targetId === nodeId) {
      const points = line.points();
      points[2] = newX + halfW;
      points[3] = newY + halfH;
      line.points(points);
      hasUpdated = true;
    }
  });
  // Solo redibujar linksLayer si realmente se modificó alguna línea
  if (hasUpdated && linksLayer) {
    linksLayer.batchDraw();
  }
}

// ─── 5. Herramienta de Conexión de Enlaces ──────────────────────────────────
function startLinkMode() {
  linkMode = true;
  linkSourceNode = null;
  document.getElementById('btn-toggle-link-mode').classList.add('btn-active');
  document.getElementById('link-mode-banner').style.display = 'flex';
  document.getElementById('link-mode-text').textContent = 'Paso 1: Haz clic en el nodo origen';
}

function cancelLinkMode() {
  linkMode = false;
  if (linkSourceNode) {
    const grp = nodeGroups.get(linkSourceNode.id);
    if (grp) grp.findOne('.box').stroke('#27354a');
  }
  linkSourceNode = null;
  document.getElementById('btn-toggle-link-mode').classList.remove('btn-active');
  document.getElementById('link-mode-banner').style.display = 'none';
  nodesLayer.batchDraw();
}

async function handleLinkNodeClick(node) {
  if (!linkSourceNode) {
    // Primer clic: Origen
    linkSourceNode = node;
    const grp = nodeGroups.get(node.id);
    if (grp) {
      grp.findOne('.box').stroke('#10b981');
      nodesLayer.batchDraw();
    }
    document.getElementById('link-mode-text').textContent = `Conectando desde "${node.name}". Haz clic en el nodo destino.`;
  } else {
    // Segundo clic: Destino
    if (linkSourceNode.id === node.id) {
      alert('No puedes conectar un nodo consigo mismo.');
      cancelLinkMode();
      return;
    }

    try {
      const newLink = await API.createLink({
        map_id: currentMap.id,
        source_node_id: linkSourceNode.id,
        target_node_id: node.id,
        status: 'ok'
      });

      currentMap.links.push(newLink);
      const dict = new Map();
      currentMap.nodes.forEach(n => dict.set(n.id, n));
      renderLink(newLink, dict);

      // Los nodos ya están naturalmente encima porque nodesLayer está sobre linksLayer
      linksLayer.batchDraw();

    } catch (err) {
      alert('Error creando enlace: ' + err.message);
    } finally {
      cancelLinkMode();
    }
  }
}

// ─── 6. Inspector de Propiedades del Nodo ───────────────────────────────────

// Cache for realtime telemetry polling interval
let _realtimePollInterval = null;

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

  // Actualizar panel lateral
  document.getElementById('no-selection-msg').style.display = 'none';
  document.getElementById('node-properties-panel').style.display = 'block';

  const isSubmap = node.device_type === 'submap';
  let extra = node.extra_data || {};

  // Título y badge
  document.getElementById('prop-node-title').textContent = node.name || 'Sin Nombre';
  document.getElementById('prop-node-type-badge').textContent = isSubmap ? 'Submapa' : (extra.role || node.device_type || 'Dispositivo');

  // Subtítulo
  const subtitleEl = document.getElementById('prop-node-subtitle');
  if (isSubmap) {
    subtitleEl.textContent = 'Contenedor de Topología Hija';
  } else if (extra.manufacturer || extra.model) {
    subtitleEl.textContent = `${extra.manufacturer || ''} ${extra.model || ''}`.trim();
  } else {
    subtitleEl.textContent = 'Ficha técnica NetBox';
  }

  // Datos Técnicos Observables (Read-Only)
  document.getElementById('prop-node-ip').textContent = node.ip || 'Sin IP configurada';
  document.getElementById('prop-node-site').textContent = node.site_name || 'No asignado';
  document.getElementById('prop-node-role').textContent = isSubmap ? 'Contenedor Submapa' : (extra.role || node.device_type || 'N/A');
  document.getElementById('prop-node-mfr').textContent = extra.manufacturer || (isSubmap ? 'Sistema' : 'Genérico');
  document.getElementById('prop-node-model').textContent = extra.model || (isSubmap ? 'Submapa Virtual' : 'N/A');
  document.getElementById('prop-node-serial').textContent = extra.serial || 'No registrado';

  const statusEl = document.getElementById('prop-node-status');
  const statusVal = extra.status || node.status || 'active';
  statusEl.textContent = (statusVal === 'active' || statusVal === 'ok') ? 'Activo' : statusVal;
  statusEl.style.color = (statusVal === 'active' || statusVal === 'ok') ? 'var(--success)' : 'var(--danger)';

  document.getElementById('prop-node-coords').textContent = `X: ${Math.round(node.x)}, Y: ${Math.round(node.y)}`;

  // Enlace directo a NetBox
  const netboxBtn = document.getElementById('btn-open-netbox');
  if (node.device_id) {
    netboxBtn.href = `http://10.9.8.52:8089/dcim/devices/${node.device_id}/`;
    netboxBtn.style.display = 'inline-flex';

    // Enriquecer datos si faltan detalles de hardware
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

  // Enlace directo a Zabbix
  const zabbixBtn = document.getElementById('btn-open-zabbix');
  if (zabbixBtn) {
    if (!isSubmap && node.name) {
      const zabbixBase = 'https://10.9.8.5:8082';
      const hostName = encodeURIComponent(node.name);
      zabbixBtn.href = `${zabbixBase}/zabbix.php?action=latest.view&filter_name=${hostName}&filter_groupids[]=0`;
      zabbixBtn.style.display = 'inline-flex';
    } else {
      zabbixBtn.style.display = 'none';
    }
  }

  // Acción de Convertir a Submapa vs Entrar a Submapa
  const convertSubmapBox = document.getElementById('convert-submap-action-box');
  const submapBox = document.getElementById('submap-action-box');

  if (isSubmap) {
    if (convertSubmapBox) convertSubmapBox.style.display = 'none';
    if (submapBox) submapBox.style.display = node.extra_data?.target_map_id ? 'block' : 'none';
  } else {
    if (convertSubmapBox) convertSubmapBox.style.display = 'block';
    if (submapBox) submapBox.style.display = 'none';
  }

  // ─── TELEMETRÍA EN TIEMPO REAL (Zabbix) ─────────────────────────────────
  // Mostrar panel de telemetría y cargar datos reales
  const telemetryPanel = document.getElementById('telemetry-panel');
  if (telemetryPanel) {
    // Solo mostrar para equipos reales con IP (no submapas vacíos)
    if (!isSubmap && node.ip) {
      telemetryPanel.style.display = 'block';
      // Reset indicadores mientras carga
      setTelemetryLoading();
      // Cargar datos reales en segundo plano (no bloqueante)
      loadNodeTelemetry(node.id);
    } else if (isSubmap) {
      telemetryPanel.style.display = 'block';
      setTelemetryLoading();
      loadNodeTelemetry(node.id);
    } else {
      telemetryPanel.style.display = 'none';
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
        box.stroke(getNodeStatusColor(selectedNode.status, selectedNode.device_type === 'submap'));
        box.strokeWidth(1.5);
        box.isHighlighted = false;
      }
    }
    selectedNode = null;
    nodesLayer.batchDraw();
  }
  document.getElementById('no-selection-msg').style.display = 'block';
  document.getElementById('node-properties-panel').style.display = 'none';
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
      box.stroke(getNodeStatusColor(node.status, node.device_type === 'submap'));
      box.strokeWidth(1.5);
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
        box.stroke(getNodeStatusColor(node.status, node.device_type === 'submap'));
        box.strokeWidth(1.5);
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

          // Redibujar capas
          nodesLayer.batchDraw();
          linksLayer.batchDraw();

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

  // Parámetros Inalámbricos (Cambium / Wireless)
  const wBox = document.getElementById('telemetry-wireless-box');
  const w = data.wireless;
  if (wBox) {
    if (w && (w.channel_width_text || w.frequency_mhz || w.rssi_dbm || w.snr_db)) {
      wBox.style.display = 'block';
      safe('telemetry-channel-bw', w.channel_width_text || (w.channel_width_id ? `ID ${w.channel_width_id}` : '—'));
      safe('telemetry-freq', w.frequency_mhz ? `${w.frequency_mhz} MHz` : '—');
      safe('telemetry-rssi', w.rssi_dbm ? `${w.rssi_dbm} dBm` : '—');
      safe('telemetry-snr', w.snr_db ? `${w.snr_db} dB` : '—');
    } else {
      wBox.style.display = 'none';
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

// Actualiza el dot de color y contorno en el canvas Konva para un nodo específico
function applyNodeStatusToCanvas(nodeId, status) {
  const grp = nodeGroups.get(nodeId);
  if (!grp) return;
  const dotShape = grp.getChildren(c => c.getClassName() === 'Circle' && !c.listening())[0];
  const boxShape = grp.findOne('.box');
  const isSubmap = grp.isSubmap || false;
  const color = getNodeStatusColor(status, isSubmap);

  if (dotShape) {
    dotShape.fill(color);
  }
  if (boxShape && !boxShape.isHighlighted) {
    boxShape.stroke(color);
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

      // Actualizar dot de cada nodo en el canvas sin re-renderizar
      for (const [nodeId, nodeStatus] of Object.entries(data.nodes)) {
        applyNodeStatusToCanvas(nodeId, nodeStatus.status);
        // Si este nodo está seleccionado, refrescar panel también
        if (selectedNode && selectedNode.id === nodeId) {
          applyTelemetryToPanel(nodeStatus);
        }
      }

      // Actualizar status local en currentMap
      if (currentMap) {
        currentMap.nodes.forEach(n => {
          if (data.nodes[n.id]) n.status = data.nodes[n.id].status;
        });
      }
    } catch (e) {
      console.warn('[Realtime] Error en polling de estado:', e);
    }
  };

  // Primera ejecución inmediata + polling cada 45s
  poll();
  _realtimePollInterval = setInterval(poll, 45000);
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

  // Actualizar Breadcrumb
  const crumbs = await API.getMapBreadcrumb(mapId);
  renderBreadcrumbs(crumbs);

  // Renderizar enlaces primero (capa inferior)
  const nodesDict = new Map();
  mapData.nodes.forEach(n => nodesDict.set(n.id, n));
  mapData.links.forEach(l => renderLink(l, nodesDict));

  // Renderizar nodos (capa superior)
  mapData.nodes.forEach(n => renderNode(n));

  linksLayer.batchDraw();
  nodesLayer.batchDraw();
  refreshMapsTabList();
  checkMapCanPopulateFromSite();

  // Arrancar polling de estado en tiempo real para este mapa
  startRealtimePolling(mapId);
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

    item.innerHTML = `
      <div class="device-head">
        <span class="device-title" title="${dev.name}">${dev.name}</span>
        <button class="device-btn-add" title="Agregar al centro del lienzo">
          <i class="fas fa-plus-circle"></i>
        </button>
      </div>
      <div class="device-ip">${dev.ip || 'Sin IP'}</div>
      <div class="device-meta">
        <span class="meta-pill role">${dev.role}</span>
        <span class="meta-pill site">${dev.site}</span>
        ${dev.model ? `<span class="meta-pill">${dev.model}</span>` : ''}
      </div>
    `;

    // Soporte Drag & Drop
    item.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('application/json', JSON.stringify(dev));
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

// ─── 9. Pestaña de Mapas y Jerarquía CRUD ──────────────────────────────────
let cachedMaps = [];

async function refreshMapsTabList(filterText = '') {
  cachedMaps = await API.getMaps();
  const treeContainer = document.getElementById('maps-tree');
  if (!treeContainer) return;
  treeContainer.innerHTML = '';

  updateParentMapSelectOptions();

  // Filtrar mapas si hay texto de búsqueda
  let filtered = cachedMaps;
  if (filterText && filterText.trim()) {
    const q = filterText.trim().toLowerCase();
    filtered = cachedMaps.filter(m => m.name.toLowerCase().includes(q) || (m.description && m.description.toLowerCase().includes(q)));
  }

  if (filtered.length === 0) {
    treeContainer.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 20px;">No se encontraron mapas.</div>';
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

  // Raíces
  const rootMaps = filtered.filter(m => !m.parent_map_id || !allIds.has(m.parent_map_id));

  function renderMapNode(mapObj, level = 0) {
    const wrapper = document.createElement('div');
    wrapper.className = 'map-node-wrapper';

    const card = document.createElement('div');
    const isSubmap = level > 0 || Boolean(mapObj.parent_map_id);
    const isActive = currentMap && currentMap.id === mapObj.id;

    card.className = `map-item-card ${isSubmap ? 'is-submap' : ''} ${isActive ? 'active' : ''}`;
    const iconClass = isSubmap ? 'fa-folder submap' : 'fa-sitemap';
    const isDefault = mapObj.id === 'default-map';

    card.innerHTML = `
      <div class="map-card-head">
        <div class="map-card-title-group" title="Hacer clic para abrir este mapa en el lienzo">
          <i class="fas ${iconClass} map-card-icon"></i>
          <span class="map-card-name">${mapObj.name}</span>
        </div>
        ${isActive ? '<span class="map-active-badge"><i class="fas fa-check"></i> Activo</span>' : ''}
      </div>

      <div class="map-card-meta">
        <div class="map-card-stats">
          <span><i class="fas fa-server"></i> ${mapObj.nodes_count || 0} nodos</span>
          <span><i class="fas fa-project-diagram"></i> ${mapObj.links_count || 0} enlaces</span>
        </div>
        <div class="map-actions">
          <button class="map-action-btn open-btn" title="Cargar este mapa en el lienzo">
            <i class="fas fa-eye"></i>
          </button>
          <button class="map-action-btn add-sub-btn" title="Crear submapa hijo de este mapa">
            <i class="fas fa-folder-plus"></i>
          </button>
          <button class="map-action-btn populate-btn" title="Poblar o sincronizar equipos desde NetBox">
            <i class="fas fa-magic"></i>
          </button>
          <button class="map-action-btn edit-btn" title="Editar propiedades del mapa">
            <i class="fas fa-pen"></i>
          </button>
          ${!isDefault ? `
          <button class="map-action-btn delete-btn" title="Eliminar mapa">
            <i class="fas fa-trash-alt"></i>
          </button>` : ''}
        </div>
      </div>
    `;

    // Clic en abrir
    card.querySelector('.map-card-title-group').addEventListener('click', () => loadMap(mapObj.id));
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

    wrapper.appendChild(card);

    // Hijos recursivos
    const children = childrenMap.get(mapObj.id) || [];
    if (children.length > 0) {
      const childContainer = document.createElement('div');
      childContainer.className = 'map-children-container';
      children.forEach(child => {
        childContainer.appendChild(renderMapNode(child, level + 1));
      });
      wrapper.appendChild(childContainer);
    }

    return wrapper;
  }

  rootMaps.forEach(root => {
    treeContainer.appendChild(renderMapNode(root, 0));
  });
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

  // Poblar select de sitios
  if (selectSiteSubmap) {
    selectSiteSubmap.innerHTML = '<option value="">-- Elige un Sitio de NetBox --</option>';
    cachedSitesSummary.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.name;
      opt.textContent = `${s.name} (${s.device_count} equipos)`;
      opt.dataset.count = s.device_count;
      selectSiteSubmap.appendChild(opt);
    });
  }

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

      if (currentMap && currentMap.id === editId) {
        currentMap.name = updated.name;
        currentMap.description = updated.description;
        currentMap.grid_size = updated.grid_size;
        currentMap.parent_map_id = updated.parent_map_id;
        const crumbs = await API.getMapBreadcrumb(currentMap.id);
        renderBreadcrumbs(crumbs);
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

      // Si insertó un nodo en el mapa activo actual, renderizarlo
      if (res.submap_node && parentId && currentMap && currentMap.id === parentId) {
        currentMap.nodes.push(res.submap_node);
        renderNode(res.submap_node);
        nodesLayer.batchDraw();
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

      if (parentId && currentMap && currentMap.id === parentId && insertSubmapNode) {
        const centerX = (-stage.x() + stage.width() / 2) / stage.scaleX();
        const centerY = (-stage.y() + stage.height() / 2) / stage.scaleY();
        const snapX = snapToGrid ? Math.round(centerX / GRID_SIZE) * GRID_SIZE : centerX;
        const snapY = snapToGrid ? Math.round(centerY / GRID_SIZE) * GRID_SIZE : centerY;

        const submapNode = await API.createNode({
          map_id: currentMap.id,
          name: newMap.name,
          ip: '',
          device_type: 'submap',
          site_name: desc || 'Submapa',
          x: snapX,
          y: snapY,
          status: 'ok',
          extra_data: { target_map_id: newMap.id }
        });
        currentMap.nodes.push(submapNode);
        renderNode(submapNode);
        nodesLayer.batchDraw();
      }

      if (!parentId) {
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

  const ok = confirm(`¿Estás seguro de eliminar el mapa "${mapName}"?\n\nADVERTENCIA: Se eliminarán todos sus submapas hijos, nodos y enlaces de forma permanente.`);
  if (!ok) return;

  try {
    const res = await API.deleteMap(mapId);
    if (res) {
      if (currentMap && (currentMap.id === mapId || currentMap.parent_map_id === mapId)) {
        await loadMap('default-map');
      }
      await refreshMapsTabList();
    }
  } catch (err) {
    alert('Error eliminando mapa: ' + err.message);
  }
}

// ─── 11. Gestión de Pestañas (Sidebar Tabs) ──────────────────────────────────
function switchTab(tabId) {
  document.querySelectorAll('.sidebar-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });
  document.querySelectorAll('.sidebar-body').forEach(b => {
    b.style.display = b.id === tabId ? 'flex' : 'none';
  });
  if (tabId === 'tab-spectrum') {
    handleSpectrumTabActivated();
  }
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
        <label style="display: flex; align-items: center; gap: 7px; cursor: pointer; flex: 1; overflow: hidden; min-width: 0;">
          <input type="checkbox" class="chk-spec-device" data-node-id="${d.node_id}" ${isSelected ? 'checked' : ''} style="cursor: pointer; flex-shrink: 0;">
          <span style="font-size: 0.78rem;">${roleIcon}</span>
          <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">
            <div style="font-size: 0.76rem; font-weight: 700; color: #f1f5f9; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${d.name}">
              ${d.name}
            </div>
            <div style="font-size: 0.65rem; color: #94a3b8; font-family: monospace;">
              ${d.ip || 'Sin IP'} · <span style="color: ${bwColor}; font-weight: 600;">${d.bandwidth_text}</span>
            </div>
          </div>
        </label>
        <div style="text-align: right; flex-shrink: 0; padding-left: 6px;">
          <span style="font-size: 0.72rem; font-weight: 800; color: #38bdf8; font-family: monospace; display: block;">
            ${d.frequency_mhz} MHz
          </span>
          <span style="font-size: 0.62rem; color: #64748b; font-family: monospace;">
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
  const baseMinWidth = isModal ? 2200 : 1100;
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
    return `
      <div class="spectrum-band-zone ${b.cls}" style="left: ${leftPct}%; width: ${widthPct}%;" title="${b.label}">
        <span style="position: absolute; top: 2px; left: 4px; font-size: 0.58rem; font-weight: 700; color: rgba(255,255,255,0.25); text-transform: uppercase; pointer-events: none;">
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

  const laneHeight = isModal ? 34 : 26;
  const laneGap = isModal ? 8 : 5;
  const rulerHeaderHeight = 36;
  const totalCanvasHeight = Math.max(isModal ? 440 : 180, rulerHeaderHeight + (lanes.length * (laneHeight + laneGap)) + 20);
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
        <div style="position: relative; z-index: 2; display: flex; align-items: center; gap: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: ${isModal ? '0.72rem' : '0.62rem'};">
          <span>${roleIcon}</span>
          <strong style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${safeName}</strong>
          <span style="opacity: 0.85; font-family: monospace; font-size: 0.6rem;">(${dev.bandwidth_mhz}M)</span>
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
        document.getElementById('zbx-stat-nodes').textContent = analysis.total_nodes;
        document.getElementById('zbx-stat-matched').textContent = `${analysis.nodes.filter(n => n.zabbix_matched).length} / ${analysis.total_nodes}`;
        document.getElementById('zbx-stat-links').textContent = analysis.total_links;
        document.getElementById('zbx-stat-direct').textContent = analysis.direct_relations_count;
        document.getElementById('zbx-stat-indirect').textContent = analysis.indirect_relations_count;
        document.getElementById('zbx-stat-root').textContent = analysis.root_nodes_count;
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

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sincronizando...';
  consoleBox.style.display = 'block';
  consoleBox.innerHTML = '<div style="color: #38bdf8;">[1/3] Iniciando sincronización BSM con Zabbix 7.0...</div>';

  try {
    consoleBox.innerHTML += '<div style="color: #94a3b8;">[2/3] Mapeando jerarquía de mapas, nodos y dependencias de enlaces...</div>';
    const res = await API.syncZabbix(true);
    const rep = res.report || {};

    consoleBox.innerHTML += `
      <div style="color: #10b981; margin-top: 6px; font-weight: bold;">✔ ¡Sincronización con Zabbix exitosa!</div>
      <div style="color: #cbd5e1; margin-top: 4px;">• Mapas raíz creados en Zabbix: <strong>${rep.maps_synced || 0}</strong></div>
      <div style="color: #cbd5e1;">• Submapas dependientes: <strong>${rep.submaps_synced || 0}</strong></div>
      <div style="color: #cbd5e1;">• Dispositivos sincronizados: <strong>${rep.nodes_synced || 0}</strong></div>
      <div style="color: #10b981;">• Hosts vinculados a triggers en Zabbix: <strong>${rep.nodes_matched_zabbix || 0}</strong></div>
      <div style="color: #38bdf8;">• Dependencias directas creadas: <strong>${rep.direct_dependencies_created || 0}</strong></div>
      <div style="color: #f59e0b;">• Rutas redundantes configuradas: <strong>${rep.redundant_dependencies_created || 0}</strong></div>
      <div style="color: #64748b; font-size: 0.7rem; margin-top: 6px;">Las alertas de Zabbix ahora reconocerán automáticamente la causa raíz en caídas.</div>
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
  if (!confirm('¿Estás seguro de eliminar todos los servicios de NexusDude en Zabbix?\n\nEsto limpiará la estructura BSM de NexusDude para permitir una re-sincronización limpia.')) {
    return;
  }

  const consoleBox = document.getElementById('zbx-sync-console');
  if (consoleBox) {
    consoleBox.style.display = 'block';
    consoleBox.innerHTML = '<div style="color: #f59e0b;">Eliminando servicios en Zabbix...</div>';
  }

  try {
    const res = await API.clearZabbixServices();
    if (consoleBox) {
      consoleBox.innerHTML = `<div style="color: #10b981;">✔ Se eliminaron ${res.deleted_services || 0} servicios en Zabbix.</div>`;
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
    await loadMapsTree();

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

// ─── 12. Inicialización General ─────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  initSSOAuth();
  initCanvas();

  // Cambio de pestañas
  document.querySelectorAll('.sidebar-tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  // Verificación de autenticación
  try {
    currentUser = await API.verifyAuth();
    const userBadge = document.getElementById('user-badge');
    const authOverlay = document.getElementById('auth-overlay');

    if (currentUser && currentUser.authenticated) {
      userBadge.innerHTML = `<span class="status-dot"></span><span>${currentUser.username} (${currentUser.role})</span>`;
      userBadge.classList.remove('unauthenticated');
      authOverlay.style.display = 'none';

      // Cargar mapas y filtros de inventario
      const maps = await API.getMaps();
      if (maps && maps.length > 0) {
        await loadMap(maps[0].id);
      }
      await loadInventoryFilters();
      await triggerSearch();

    } else {
      userBadge.innerHTML = `<span class="status-dot"></span><span>No autenticado</span>`;
      userBadge.classList.add('unauthenticated');
      authOverlay.style.display = 'flex';
    }
  } catch (e) {
    console.error('Error de autenticación:', e);
    document.getElementById('auth-overlay').style.display = 'flex';
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
    await API.refreshInventory();
    await loadInventoryFilters();
    await triggerSearch();
  });

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

  // Botón Conectar Enlace
  document.getElementById('btn-toggle-link-mode').addEventListener('click', () => {
    if (linkMode) {
      cancelLinkMode();
    } else {
      startLinkMode();
    }
  });
  document.getElementById('btn-cancel-link').addEventListener('click', cancelLinkMode);

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
    }
  });
});


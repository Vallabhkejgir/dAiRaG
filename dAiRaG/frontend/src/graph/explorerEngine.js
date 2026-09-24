import { CLUSTER_LAYOUT, ENTITY_FIELD_PRIORITY, ENTITY_STYLES } from "./constants.js";
import {
  state,
  elements,
  byId,
  createElement,
  clamp,
  uniq,
  getNode,
  getRelationship,
  styleForEntity,
  getAdjacentRelationshipIds,
  getOppositeNodeId,
  formatCount,
  displayIdForNode,
  hasRenderableValue,
} from "./runtime.js";

function selectedFocusNodeIds() {
  if (!state.selectedNodeId) {
    return new Set();
  }

  const focusNodeIds = new Set([state.selectedNodeId]);
  for (const relationshipId of getAdjacentRelationshipIds(state.selectedNodeId)) {
    const relationship = getRelationship(relationshipId);
    if (!relationship) {
      continue;
    }
    focusNodeIds.add(getOppositeNodeId(relationship, state.selectedNodeId));
  }

  return focusNodeIds;
}

function selectedRelationshipContext() {
  if (!state.selectedRelationshipId) {
    return {
      endpointNodeIds: new Set(),
      connectedNodeIds: new Set(),
      connectedRelationshipIds: new Set(),
    };
  }

  const relationship = getRelationship(state.selectedRelationshipId);
  if (!relationship) {
    return {
      endpointNodeIds: new Set(),
      connectedNodeIds: new Set(),
      connectedRelationshipIds: new Set(),
    };
  }

  const endpointNodeIds = new Set([relationship.source, relationship.target]);
  const connectedRelationshipIds = new Set([relationship.id]);

  for (const nodeId of endpointNodeIds) {
    for (const relationshipId of getAdjacentRelationshipIds(nodeId)) {
      connectedRelationshipIds.add(relationshipId);
    }
  }

  const connectedNodeIds = new Set(endpointNodeIds);
  for (const relationshipId of connectedRelationshipIds) {
    const item = getRelationship(relationshipId);
    if (!item) {
      continue;
    }
    connectedNodeIds.add(item.source);
    connectedNodeIds.add(item.target);
  }

  return { endpointNodeIds, connectedNodeIds, connectedRelationshipIds };
}

function sortNodesByType(nodes) {
  return [...nodes].sort((left, right) => left.label.localeCompare(right.label));
}

function anchorForNode(entityType, index, total, degree) {
  const cluster = CLUSTER_LAYOUT[entityType] ?? { x: 0, y: 0, angle: 0, spread: 16 };
  const golden = 2.399963229728653;
  const angle = cluster.angle + index * golden;
  const radialStep = cluster.spread + Math.min(degree, 12) * 0.55;
  const radius = radialStep * Math.sqrt(index + 1);
  const offsetX = Math.cos(angle) * radius;
  const offsetY = Math.sin(angle) * radius;
  const arcBias = total > 1 ? (index / (total - 1) - 0.5) * cluster.spread * 8 : 0;

  return {
    x: cluster.x + offsetX + arcBias * 0.28,
    y: cluster.y + offsetY - arcBias * 0.12,
  };
}

function getNodeRadius(nodeId) {
  const degree = getAdjacentRelationshipIds(nodeId).length;
  return clamp(3.8 + Math.sqrt(degree || 1) * 1.15, 3.8, 10.5);
}

function prepareGraphData(payload) {
  state.data = payload;

  for (const node of payload.nodes) {
    state.nodesById.set(node.id, node);
    state.adjacencyByNode.set(node.id, []);
  }

  for (const relationship of payload.relationships) {
    state.relationshipsById.set(relationship.id, relationship);
    if (!state.adjacencyByNode.has(relationship.source)) {
      state.adjacencyByNode.set(relationship.source, []);
    }
    if (!state.adjacencyByNode.has(relationship.target)) {
      state.adjacencyByNode.set(relationship.target, []);
    }
    state.adjacencyByNode.get(relationship.source).push(relationship.id);
    state.adjacencyByNode.get(relationship.target).push(relationship.id);
  }

  const nodesByType = {};
  for (const node of payload.nodes) {
    if (!nodesByType[node.entityType]) {
      nodesByType[node.entityType] = [];
    }
    nodesByType[node.entityType].push(node);
  }

  for (const [entityType, nodes] of Object.entries(nodesByType)) {
    const orderedNodes = sortNodesByType(nodes);
    orderedNodes.forEach((node, index) => {
      const degree = getAdjacentRelationshipIds(node.id).length;
      const anchor = anchorForNode(entityType, index, orderedNodes.length, degree);
      state.layoutByNodeId.set(node.id, {
        x: anchor.x,
        y: anchor.y,
        vx: 0,
        vy: 0,
        anchorX: anchor.x,
        anchorY: anchor.y,
        radius: getNodeRadius(node.id),
      });
    });
  }
}

function ensureLayout(nodeId) {
  if (!state.layoutByNodeId.has(nodeId)) {
    state.layoutByNodeId.set(nodeId, {
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      anchorX: 0,
      anchorY: 0,
      radius: getNodeRadius(nodeId),
    });
  }
  return state.layoutByNodeId.get(nodeId);
}

function addNodeVisible(nodeId) {
  if (getNode(nodeId)) {
    state.visibleNodeIds.add(nodeId);
  }
}

function recomputeVisibleRelationships() {
  state.visibleRelationshipIds.clear();
  for (const relationship of state.relationshipsById.values()) {
    if (
      state.visibleNodeIds.has(relationship.source) &&
      state.visibleNodeIds.has(relationship.target)
    ) {
      state.visibleRelationshipIds.add(relationship.id);
    }
  }
  syncChatHighlightedRelationships();
}

function visibleNodeSetsMatch(nodeIds) {
  if (nodeIds.size !== state.visibleNodeIds.size) {
    return false;
  }

  for (const nodeId of nodeIds) {
    if (!state.visibleNodeIds.has(nodeId)) {
      return false;
    }
  }

  return true;
}

function isShowingFullGraph() {
  return visibleNodeSetsMatch(new Set(state.nodesById.keys()));
}

function restoreCamera(camera) {
  if (!camera) {
    return;
  }
  state.camera.x = camera.x ?? state.camera.x;
  state.camera.y = camera.y ?? state.camera.y;
  state.camera.scale = camera.scale ?? state.camera.scale;
}

function rememberFullGraphCamera() {
  if (isShowingFullGraph()) {
    state.fullGraphCamera = { ...state.camera };
  }
}

function captureViewState() {
  return {
    visibleNodeIds: [...state.visibleNodeIds],
    selectedNodeId: state.selectedNodeId,
    selectedRelationshipId: state.selectedRelationshipId,
    granularOverlayVisible: state.granularOverlayVisible,
    viewMode: state.viewMode,
    activeFocusNodeId: state.activeFocusNodeId,
    camera: { ...state.camera },
  };
}

function clearViewHistory() {
  state.viewHistory = [];
}

function restoreViewState(snapshot) {
  if (!snapshot) {
    return;
  }

  const visibleNodeIds = (snapshot.visibleNodeIds ?? []).filter((nodeId) => getNode(nodeId));
  state.visibleNodeIds = visibleNodeIds.length
    ? new Set(visibleNodeIds)
    : new Set(state.nodesById.keys());
  recomputeVisibleRelationships();

  const selectedRelationshipId =
    snapshot.selectedRelationshipId &&
    state.visibleRelationshipIds.has(snapshot.selectedRelationshipId)
      ? snapshot.selectedRelationshipId
      : null;
  const selectedNodeId =
    !selectedRelationshipId &&
    snapshot.selectedNodeId &&
    state.visibleNodeIds.has(snapshot.selectedNodeId)
      ? snapshot.selectedNodeId
      : null;

  state.selectedRelationshipId = selectedRelationshipId;
  state.selectedNodeId = selectedNodeId;
  state.granularOverlayVisible = Boolean(snapshot.granularOverlayVisible);
  state.viewMode =
    snapshot.viewMode === "focus" && state.visibleNodeIds.size !== state.nodesById.size
      ? "focus"
      : "global";
  state.activeFocusNodeId =
    snapshot.activeFocusNodeId && state.visibleNodeIds.has(snapshot.activeFocusNodeId)
      ? snapshot.activeFocusNodeId
      : null;

  if (snapshot.camera) {
    restoreCamera(snapshot.camera);
  }

  syncOverlayToggleButton();
  updateMetadataCard();
  updateGraphSummary();
}

function restorePreviousView() {
  const snapshot = state.viewHistory.pop();
  if (!snapshot) {
    return;
  }
  restoreViewState(snapshot);
}

function showFullGraph(options = {}) {
  if (options.clearHistory !== false) {
    clearViewHistory();
  }
  if (options.resetGranular !== false) {
    state.granularOverlayVisible = false;
  }
  state.visibleNodeIds = new Set(state.nodesById.keys());
  recomputeVisibleRelationships();
  state.viewMode = "global";
  state.activeFocusNodeId = null;
  if (options.select !== false) {
    state.selectedNodeId = null;
    state.selectedRelationshipId = null;
  }
  syncOverlayToggleButton();
  updateMetadataCard();
  updateGraphSummary();
  if (options.fit !== false) {
    fitGraph();
  }
}

function resetGraphView() {
  const alreadyShowingFullGraph = state.viewMode === "global" && isShowingFullGraph();
  setChatHighlightedNodeIds([]);
  showFullGraph({ fit: !alreadyShowingFullGraph });
  if (alreadyShowingFullGraph && state.fullGraphCamera) {
    restoreCamera(state.fullGraphCamera);
  }
}

function getNeighborhood(nodeId, depth = 1) {
  const visited = new Set([nodeId]);
  const queue = [{ nodeId, depth: 0 }];

  while (queue.length) {
    const current = queue.shift();
    if (current.depth >= depth) {
      continue;
    }
    for (const relationshipId of getAdjacentRelationshipIds(current.nodeId)) {
      const relationship = getRelationship(relationshipId);
      if (!relationship) {
        continue;
      }
      const otherId = getOppositeNodeId(relationship, current.nodeId);
      if (!visited.has(otherId)) {
        visited.add(otherId);
        queue.push({ nodeId: otherId, depth: current.depth + 1 });
      }
    }
  }

  return visited;
}

function focusNeighborhood(nodeId, depth = 1, options = {}) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }

  const neighborhoodNodeIds = getNeighborhood(nodeId, depth);
  const isSameView =
    visibleNodeSetsMatch(neighborhoodNodeIds) &&
    state.selectedNodeId === nodeId &&
    !state.selectedRelationshipId;

  if (!isSameView) {
    const previousView = captureViewState();
    if (state.viewMode === "focus" && state.activeFocusNodeId) {
      previousView.selectedNodeId = state.activeFocusNodeId;
      previousView.selectedRelationshipId = null;
    }
    state.viewHistory.push(previousView);
  }

  state.visibleNodeIds = neighborhoodNodeIds;
  recomputeVisibleRelationships();
  state.viewMode = "focus";
  state.activeFocusNodeId = nodeId;
  selectNode(nodeId, { center: false });
  if (options.fit !== false) {
    fitGraph();
  }
}

function expandNode(nodeId) {
  const node = getNode(nodeId);
  if (!node) {
    return;
  }
  addNodeVisible(nodeId);
  for (const relationshipId of getAdjacentRelationshipIds(nodeId)) {
    const relationship = getRelationship(relationshipId);
    if (!relationship) {
      continue;
    }
    addNodeVisible(relationship.source);
    addNodeVisible(relationship.target);
  }
  recomputeVisibleRelationships();
  state.viewMode = state.visibleNodeIds.size === state.nodesById.size ? "global" : "focus";
  selectNode(nodeId, { center: false });
}

function centerOnNode(nodeId) {
  const layout = ensureLayout(nodeId);
  state.camera.x = layout.x;
  state.camera.y = layout.y;
}

function selectNode(nodeId, options = {}) {
  if (!getNode(nodeId)) {
    return;
  }
  state.selectedNodeId = nodeId;
  state.selectedRelationshipId = null;
  if (options.center) {
    centerOnNode(nodeId);
  }
  updateMetadataCard();
  updateGraphSummary();
}

function selectRelationship(relationshipId) {
  if (!getRelationship(relationshipId)) {
    return;
  }
  state.selectedRelationshipId = relationshipId;
  state.selectedNodeId = null;
  updateMetadataCard();
  updateGraphSummary();
}

function clearSelection() {
  state.selectedNodeId = null;
  state.selectedRelationshipId = null;
  state.granularOverlayVisible = false;
  syncOverlayToggleButton();
  updateMetadataCard();
  updateGraphSummary();
}

function syncChatHighlightedRelationships() {
  if (!state.chatHighlightedNodeIds.size) {
    state.chatHighlightedRelationshipIds = new Set();
    return;
  }

  state.chatHighlightedRelationshipIds = new Set(
    [...state.visibleRelationshipIds].filter((relationshipId) => {
      const relationship = getRelationship(relationshipId);
      return (
        relationship &&
        state.chatHighlightedNodeIds.has(relationship.source) &&
        state.chatHighlightedNodeIds.has(relationship.target)
      );
    })
  );
}

function setChatHighlightedNodeIds(nodeIds = []) {
  state.chatHighlightedNodeIds = new Set(
    uniq((nodeIds ?? []).filter((nodeId) => getNode(nodeId)))
  );
  syncChatHighlightedRelationships();
}

function extractEvidenceEntityTypesFromCypher(cypher) {
  if (!cypher) {
    return [];
  }

  return uniq(
    [...cypher.matchAll(/["']([A-Za-z]+)["']\s+AS\s+entity_type/gi)]
      .map((match) => match[1])
      .filter((entityType) => ENTITY_STYLES[entityType] || ENTITY_FIELD_PRIORITY[entityType])
  );
}

function metadataTextForNode(node) {
  return Object.values(node?.metadata ?? {})
    .filter((value) => value !== null && value !== undefined)
    .map((value) => String(value).toLowerCase())
    .join(" ");
}

function deriveEvidenceNodeIdsFromResponse(response) {
  const directNodeIds = uniq([
    ...(response.evidenceNodeIds ?? []),
    ...(response.revealNodeIds ?? []),
    response.focusNodeId,
  ].filter((nodeId) => getNode(nodeId)));

  if (directNodeIds.length) {
    return directNodeIds;
  }

  const entityTypes = extractEvidenceEntityTypesFromCypher(response.cypher ?? "");
  if (!entityTypes.length) {
    return [];
  }

  const normalizedSearchTerms = uniq([
    typeof response.cypherParams?.search_term === "string"
      ? response.cypherParams.search_term.trim().toLowerCase()
      : "",
    ...((Array.isArray(response.cypherParams?.search_terms)
      ? response.cypherParams.search_terms
      : [])
      .map((term) => String(term).trim().toLowerCase())
      .filter(Boolean)),
    ...((Array.isArray(response.cypherParams?.search_term_groups)
      ? response.cypherParams.search_term_groups.flatMap((group) =>
          Array.isArray(group)
            ? group.map((term) => String(term).trim().toLowerCase())
            : []
        )
      : []).filter(Boolean)),
  ].filter(Boolean));

  if (!normalizedSearchTerms.length) {
    return [];
  }

  return [...state.visibleNodeIds].filter((nodeId) => {
    const node = getNode(nodeId);
    if (!node || !entityTypes.includes(node.entityType)) {
      return false;
    }
    const labelText = String(node.label ?? "").toLowerCase();
    const metadataText = metadataTextForNode(node);
    return normalizedSearchTerms.some(
      (searchTerm) => labelText.includes(searchTerm) || metadataText.includes(searchTerm)
    );
  });
}

function visiblePathNodeIds(startNodeId, endNodeId) {
  if (
    !startNodeId ||
    !endNodeId ||
    !state.visibleNodeIds.has(startNodeId) ||
    !state.visibleNodeIds.has(endNodeId)
  ) {
    return [];
  }

  if (startNodeId === endNodeId) {
    return [startNodeId];
  }

  const queue = [startNodeId];
  const visited = new Set([startNodeId]);
  const previousNodeById = new Map();

  while (queue.length) {
    const currentNodeId = queue.shift();
    for (const relationshipId of getAdjacentRelationshipIds(currentNodeId)) {
      if (!state.visibleRelationshipIds.has(relationshipId)) {
        continue;
      }
      const relationship = getRelationship(relationshipId);
      if (!relationship) {
        continue;
      }
      const nextNodeId = getOppositeNodeId(relationship, currentNodeId);
      if (!state.visibleNodeIds.has(nextNodeId) || visited.has(nextNodeId)) {
        continue;
      }
      visited.add(nextNodeId);
      previousNodeById.set(nextNodeId, currentNodeId);
      if (nextNodeId === endNodeId) {
        queue.length = 0;
        break;
      }
      queue.push(nextNodeId);
    }
  }

  if (!visited.has(endNodeId)) {
    return [];
  }

  const pathNodeIds = [endNodeId];
  let cursor = endNodeId;
  while (cursor !== startNodeId) {
    cursor = previousNodeById.get(cursor);
    if (!cursor) {
      return [];
    }
    pathNodeIds.push(cursor);
  }

  pathNodeIds.reverse();
  return pathNodeIds;
}

function expandEvidencePathNodeIds(nodeIds, anchorNodeId) {
  const expandedNodeIds = new Set(
    uniq((nodeIds ?? []).filter((nodeId) => getNode(nodeId)))
  );

  if (!anchorNodeId || !expandedNodeIds.has(anchorNodeId)) {
    return [...expandedNodeIds];
  }

  for (const nodeId of [...expandedNodeIds]) {
    for (const pathNodeId of visiblePathNodeIds(anchorNodeId, nodeId)) {
      expandedNodeIds.add(pathNodeId);
    }
  }

  return [...expandedNodeIds];
}

function visibleNodeCount() {
  return state.visibleNodeIds.size;
}

function visibleRelationshipCount() {
  return state.visibleRelationshipIds.size;
}

function metadataPairsForRelationship(relationship) {
  const items = [
    ["type", relationship.type],
    ["source", getNode(relationship.source)?.label ?? relationship.source],
    ["target", getNode(relationship.target)?.label ?? relationship.target],
  ];
  if (relationship.summary) {
    items.push(["summary", relationship.summary]);
  }
  const metadata = relationship.metadata ?? {};
  for (const [key, value] of Object.entries(metadata)) {
    if (!value) {
      continue;
    }
    if (!items.find(([existing]) => existing === key)) {
      items.push([key, value]);
    }
    if (items.length >= 10) {
      break;
    }
  }
  return items;
}

function metadataPairsForNode(node) {
  const metadata = node.metadata ?? {};
  const priority = ENTITY_FIELD_PRIORITY[node.entityType] ?? [];
  const pairs = [["entity_id", displayIdForNode(node)]];

  for (const key of priority) {
    const value = metadata[key];
    if (!hasRenderableValue(value)) {
      continue;
    }
    if (!pairs.find(([existing]) => existing === key)) {
      pairs.push([key, value]);
    }
    if (pairs.length >= 8) {
      break;
    }
  }

  for (const [key, value] of Object.entries(metadata)) {
    if (!hasRenderableValue(value)) {
      continue;
    }
    if (!pairs.find(([existing]) => existing === key)) {
      pairs.push([key, value]);
    }
    if (pairs.length >= 8) {
      break;
    }
  }

  return pairs;
}

function connectionChip(node, relationship) {
  const button = createElement("button", "metadata-connection");
  button.type = "button";
  button.textContent = `${relationship.type}: ${node.label}`;
  button.addEventListener("click", () => {
    if (!state.visibleNodeIds.has(node.id)) {
      addNodeVisible(node.id);
      recomputeVisibleRelationships();
    }
    selectNode(node.id, { center: true });
  });
  return button;
}

function stageRelativePoint(event) {
  const rect = elements.graphStage.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function clampMetadataCardPosition(left, top) {
  const stageRect = elements.graphStage.getBoundingClientRect();
  const cardRect = elements.metadataCard.getBoundingClientRect();
  const maxLeft = Math.max(16, stageRect.width - cardRect.width - 16);
  const maxTop = Math.max(16, stageRect.height - cardRect.height - 16);

  return {
    left: clamp(left, 16, maxLeft),
    top: clamp(top, 16, maxTop),
  };
}

function setMetadataCardPosition(left, top) {
  const clamped = clampMetadataCardPosition(left, top);
  elements.metadataCard.style.left = `${clamped.left}px`;
  elements.metadataCard.style.top = `${clamped.top}px`;
  elements.metadataCard.style.transform = "none";
}

function interactiveMetadataTarget(target) {
  return target.closest("button, a, input, textarea, select, option, label, .metadata-connection");
}

function startMetadataCardDrag(event) {
  if (elements.metadataCard.classList.contains("hidden")) {
    return;
  }

  if (interactiveMetadataTarget(event.target)) {
    return;
  }

  const cardRect = elements.metadataCard.getBoundingClientRect();
  const stageRect = elements.graphStage.getBoundingClientRect();
  const pointer = stageRelativePoint(event);
  const currentLeft = cardRect.left - stageRect.left;
  const currentTop = cardRect.top - stageRect.top;

  setMetadataCardPosition(currentLeft, currentTop);
  state.draggingMetadataCard = {
    offsetX: pointer.x - currentLeft,
    offsetY: pointer.y - currentTop,
  };
  state.draggingMetadataCardPointerId = event.pointerId ?? null;
  elements.metadataCardHeader.classList.add("is-dragging");
  elements.metadataCard.setPointerCapture?.(event.pointerId);
  event.preventDefault();
  event.stopPropagation();
}

function syncOverlayToggleButton() {
  if (!elements.overlayToggleButton) {
    return;
  }

  if (state.selectedRelationshipId) {
    elements.overlayToggleButton.textContent = state.granularOverlayVisible
      ? "Hide Connected Edges"
      : "Show Connected Edges";
    return;
  }

  elements.overlayToggleButton.textContent = state.granularOverlayVisible
    ? "Hide Connected Entities"
    : "Show Connected Entities";
}

function updateSelectionHint() {
  return;
}

function updateGraphSummary() {
  const hasFocusedView = !!state.viewHistory.length;
  const selectedFocusedNode =
    !!state.selectedNodeId && state.selectedNodeId === state.activeFocusNodeId;

  if (elements.focusNodeButton) {
    elements.focusNodeButton.disabled = !state.selectedNodeId || selectedFocusedNode;
  }
  if (elements.unfocusButton) {
    elements.unfocusButton.hidden = false;
    elements.unfocusButton.disabled =
      !hasFocusedView || (!!state.selectedNodeId && !selectedFocusedNode);
  }
}

function updateMetadataCard() {
  if (state.selectedNodeId) {
    const node = getNode(state.selectedNodeId);
    const connectionCount = getAdjacentRelationshipIds(node.id).length;
    const dataPairs = metadataPairsForNode(node);

    elements.metadataEyebrow.textContent = `${node.entityType} Entity`;
    elements.metadataTitle.textContent = node.label;
    elements.metadataSubtitle.textContent = node.subtitle || `Entity id: ${node.entityId}`;
    elements.overlayToggleButton.hidden = false;
    elements.focusNodeButton.hidden = false;
    elements.unfocusButton.hidden = false;
    syncOverlayToggleButton();

    const fieldFragment = document.createDocumentFragment();
    fieldFragment.append(
      createElement("dt", null, "connections"),
      createElement("dd", null, formatCount(connectionCount))
    );
    for (const [key, value] of dataPairs) {
      fieldFragment.append(
        createElement("dt", null, key.replace(/_/g, " ")),
        createElement("dd", null, String(value))
      );
    }
    elements.metadataFields.replaceChildren(fieldFragment);
    elements.metadataConnections.replaceChildren();
    elements.metadataConnectionsSection.classList.add("hidden");
    elements.metadataCard.classList.remove("hidden");
    return;
  }

  if (state.selectedRelationshipId) {
    const relationship = getRelationship(state.selectedRelationshipId);
    const source = getNode(relationship.source);
    const target = getNode(relationship.target);

    elements.metadataEyebrow.textContent = "Relationship";
    elements.metadataTitle.textContent = relationship.type;
    elements.metadataSubtitle.textContent = `${source?.label ?? relationship.source} -> ${target?.label ?? relationship.target}`;

    const fieldFragment = document.createDocumentFragment();
    for (const [key, value] of metadataPairsForRelationship(relationship)) {
      fieldFragment.append(
        createElement("dt", null, key.replace(/_/g, " ")),
        createElement("dd", null, value)
      );
    }
    elements.metadataFields.replaceChildren(fieldFragment);

    const connectionsFragment = document.createDocumentFragment();
    if (source) {
      connectionsFragment.append(connectionChip(source, relationship));
    }
    if (target) {
      connectionsFragment.append(connectionChip(target, relationship));
    }
    elements.metadataConnectionsLabel.textContent = "Endpoints";
    elements.metadataConnections.replaceChildren(connectionsFragment);
    elements.metadataConnectionsSection.classList.remove("hidden");
    elements.overlayToggleButton.hidden = false;
    elements.focusNodeButton.hidden = true;
    elements.unfocusButton.hidden = false;
    syncOverlayToggleButton();
    elements.metadataCard.classList.remove("hidden");
    return;
  }

  elements.metadataConnections.replaceChildren();
  elements.metadataConnectionsSection.classList.add("hidden");
  elements.overlayToggleButton.hidden = true;
  elements.focusNodeButton.hidden = true;
  elements.unfocusButton.hidden = true;
  elements.metadataCard.classList.add("hidden");
}

export function applyChatResponse(response) {
  if (response.viewMode === "focus" && response.focusNodeId) {
    focusNeighborhood(response.focusNodeId, response.focusDepth ?? 1, { fit: false });
    for (const nodeId of response.revealNodeIds ?? []) {
      addNodeVisible(nodeId);
    }
    recomputeVisibleRelationships();
    selectNode(response.focusNodeId, { center: true });
    if (response.expandFocus) {
      expandNode(response.focusNodeId);
    }
    setChatHighlightedNodeIds(
      expandEvidencePathNodeIds(
        deriveEvidenceNodeIdsFromResponse(response),
        response.focusNodeId
      )
    );
    return;
  }

  if (response.viewMode === "global") {
    showFullGraph({ select: false, fit: false });
  }

  setChatHighlightedNodeIds(
    expandEvidencePathNodeIds(
      deriveEvidenceNodeIdsFromResponse(response),
      response.focusNodeId
    )
  );
}

function resizeCanvas() {
  const rect = elements.graphCanvas.getBoundingClientRect();
  state.viewport.width = rect.width;
  state.viewport.height = rect.height;
  state.viewport.dpr = window.devicePixelRatio || 1;
  elements.graphCanvas.width = Math.floor(rect.width * state.viewport.dpr);
  elements.graphCanvas.height = Math.floor(rect.height * state.viewport.dpr);
  state.ctx.setTransform(state.viewport.dpr, 0, 0, state.viewport.dpr, 0, 0);
}

function worldToScreen(point) {
  return {
    x: (point.x - state.camera.x) * state.camera.scale + state.viewport.width / 2,
    y: (point.y - state.camera.y) * state.camera.scale + state.viewport.height / 2,
  };
}

function screenToWorld(point) {
  return {
    x: (point.x - state.viewport.width / 2) / state.camera.scale + state.camera.x,
    y: (point.y - state.viewport.height / 2) / state.camera.scale + state.camera.y,
  };
}

function visibleNodes() {
  return [...state.visibleNodeIds].map((nodeId) => getNode(nodeId)).filter(Boolean);
}

function stepSimulation() {
  const nodeIds = [...state.visibleNodeIds];
  const fullRepulsion = nodeIds.length <= 260;

  for (const nodeId of nodeIds) {
    const layout = ensureLayout(nodeId);
    layout.radius = getNodeRadius(nodeId);
    layout.vx *= 0.84;
    layout.vy *= 0.84;
    layout.vx += (layout.anchorX - layout.x) * 0.0014;
    layout.vy += (layout.anchorY - layout.y) * 0.0014;
  }

  if (fullRepulsion) {
    for (let index = 0; index < nodeIds.length; index += 1) {
      const left = ensureLayout(nodeIds[index]);
      for (let inner = index + 1; inner < nodeIds.length; inner += 1) {
        const right = ensureLayout(nodeIds[inner]);
        const dx = right.x - left.x;
        const dy = right.y - left.y;
        const distanceSquared = dx * dx + dy * dy + 0.01;
        const distance = Math.sqrt(distanceSquared);
        const force = 1400 / distanceSquared;
        const nx = dx / distance;
        const ny = dy / distance;
        left.vx -= nx * force;
        left.vy -= ny * force;
        right.vx += nx * force;
        right.vy += ny * force;
      }
    }
  }

  for (const relationshipId of state.visibleRelationshipIds) {
    const relationship = getRelationship(relationshipId);
    if (!relationship) {
      continue;
    }
    const source = ensureLayout(relationship.source);
    const target = ensureLayout(relationship.target);
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 0.1);
    const desired = 74 + Math.min((source.radius + target.radius) * 4.5, 56);
    const spring = (distance - desired) * 0.0026;
    const nx = dx / distance;
    const ny = dy / distance;
    source.vx += nx * spring;
    source.vy += ny * spring;
    target.vx -= nx * spring;
    target.vy -= ny * spring;
  }

  for (const nodeId of nodeIds) {
    if (nodeId === state.draggingNodeId) {
      continue;
    }
    const layout = ensureLayout(nodeId);
    layout.x += clamp(layout.vx, -8, 8);
    layout.y += clamp(layout.vy, -8, 8);
  }
}

function drawGraph() {
  state.ctx.clearRect(0, 0, state.viewport.width, state.viewport.height);
  state.drawCache = { nodes: [], relationships: [] };

  const selectedNodeId = state.selectedNodeId;
  const focusNodeIds = selectedFocusNodeIds();
  const relationshipContext = selectedRelationshipContext();
  const granularRelationshipView = state.granularOverlayVisible && !!state.selectedRelationshipId;
  const chatHighlightedNodeIds = state.chatHighlightedNodeIds;
  const chatHighlightedRelationshipIds = state.chatHighlightedRelationshipIds;
  const hasChatEvidence = chatHighlightedNodeIds.size > 0;
  const selectedHighlightedNodeId =
    selectedNodeId && chatHighlightedNodeIds.has(selectedNodeId) ? selectedNodeId : null;

  for (const relationshipId of state.visibleRelationshipIds) {
    const relationship = getRelationship(relationshipId);
    if (!relationship) {
      continue;
    }
    const sourceLayout = ensureLayout(relationship.source);
    const targetLayout = ensureLayout(relationship.target);
    const source = worldToScreen(sourceLayout);
    const target = worldToScreen(targetLayout);
    const isSelected = relationshipId === state.selectedRelationshipId;
    const isDirectSelectionRelationship =
      selectedNodeId &&
      (relationship.source === selectedNodeId || relationship.target === selectedNodeId);
    const isWithinFocusedNeighborhood =
      selectedNodeId &&
      focusNodeIds.has(relationship.source) &&
      focusNodeIds.has(relationship.target);
    const isConnectedToSelectedRelationship =
      granularRelationshipView && relationshipContext.connectedRelationshipIds.has(relationshipId);
    const isHovered = relationshipId === state.hoveredRelationshipId;
    const isChatHighlighted = chatHighlightedRelationshipIds.has(relationshipId);
    const isAssociatedWithSelectedHighlightedNode =
      !!selectedHighlightedNodeId &&
      (relationship.source === selectedHighlightedNodeId ||
        relationship.target === selectedHighlightedNodeId);
    const baseStrokeStyle = isSelected
      ? "rgba(31, 125, 228, 0.95)"
      : isHovered
        ? "rgba(31, 125, 228, 0.72)"
        : isDirectSelectionRelationship
          ? "rgba(109, 182, 255, 0.72)"
          : isWithinFocusedNeighborhood
            ? "rgba(109, 182, 255, 0.42)"
            : isConnectedToSelectedRelationship
              ? "rgba(109, 182, 255, 0.5)"
            : selectedNodeId
              ? "rgba(102, 180, 255, 0.09)"
              : granularRelationshipView
                ? "rgba(102, 180, 255, 0.08)"
              : "rgba(102, 180, 255, 0.22)";
    const evidenceStrokeStyle = isSelected
      ? "rgba(31, 125, 228, 0.95)"
      : isHovered
        ? "rgba(31, 125, 228, 0.78)"
        : isDirectSelectionRelationship
          ? "rgba(109, 182, 255, 0.78)"
          : isWithinFocusedNeighborhood
            ? "rgba(109, 182, 255, 0.62)"
            : isConnectedToSelectedRelationship
              ? "rgba(109, 182, 255, 0.64)"
              : "rgba(102, 180, 255, 0.52)";
    const strokeStyle = hasChatEvidence
      ? isChatHighlighted
        ? selectedHighlightedNodeId
          ? isAssociatedWithSelectedHighlightedNode
            ? evidenceStrokeStyle
            : "rgba(102, 180, 255, 0.12)"
          : evidenceStrokeStyle
        : "rgba(102, 180, 255, 0.08)"
      : baseStrokeStyle;
    const baseWidth = isSelected
      ? 3.4
      : isDirectSelectionRelationship
        ? 2.4
        : isWithinFocusedNeighborhood
          ? 1.6
          : isConnectedToSelectedRelationship
            ? 1.8
            : 1;
    const width = hasChatEvidence
      ? isChatHighlighted
        ? selectedHighlightedNodeId
          ? isAssociatedWithSelectedHighlightedNode
            ? Math.max(baseWidth, 1.6)
            : 0.95
          : Math.max(baseWidth, 1.6)
        : 0.85
      : baseWidth;

    state.ctx.strokeStyle = strokeStyle;
    state.ctx.lineWidth = width;
    state.ctx.beginPath();
    state.ctx.moveTo(source.x, source.y);
    state.ctx.lineTo(target.x, target.y);
    state.ctx.stroke();

    state.drawCache.relationships.push({
      id: relationshipId,
      x1: source.x,
      y1: source.y,
      x2: target.x,
      y2: target.y,
      midX: (source.x + target.x) / 2,
      midY: (source.y + target.y) / 2,
    });
  }

  const nodeIds = [...state.visibleNodeIds];
  nodeIds.sort((left, right) => ensureLayout(left).radius - ensureLayout(right).radius);

  for (const nodeId of nodeIds) {
    const node = getNode(nodeId);
    const layout = ensureLayout(nodeId);
    const position = worldToScreen(layout);
    const style = styleForEntity(node.entityType);
    const isSelected = nodeId === state.selectedNodeId;
    const isHovered = nodeId === state.hoveredNodeId;
    const isChatHighlighted = chatHighlightedNodeIds.has(nodeId);
    const isWithinNodeNeighborhood = !selectedNodeId || focusNodeIds.has(nodeId);
    const isRelationshipEndpoint = relationshipContext.endpointNodeIds.has(nodeId);
    const isConnectedToSelectedRelationship = relationshipContext.connectedNodeIds.has(nodeId);
    const baseNodeOpacity = selectedNodeId
      ? isSelected
        ? 1
        : isHovered
          ? 0.94
          : isWithinNodeNeighborhood
            ? 0.96
            : 0.18
      : granularRelationshipView
        ? isRelationshipEndpoint
          ? 1
          : isHovered
            ? 0.94
            : isConnectedToSelectedRelationship
              ? 0.92
              : 0.2
        : 1;
    const nodeOpacity = hasChatEvidence
      ? isChatHighlighted
        ? baseNodeOpacity
        : Math.min(baseNodeOpacity, 0.16)
      : isChatHighlighted
        ? Math.max(baseNodeOpacity, 0.98)
        : baseNodeOpacity;

    if (isSelected) {
      state.ctx.beginPath();
      state.ctx.fillStyle = "rgba(31, 125, 228, 0.16)";
      state.ctx.arc(position.x, position.y, layout.radius + 8, 0, Math.PI * 2);
      state.ctx.fill();
    }

    if (!selectedNodeId && isRelationshipEndpoint) {
      state.ctx.beginPath();
      state.ctx.fillStyle = "rgba(31, 125, 228, 0.14)";
      state.ctx.arc(position.x, position.y, layout.radius + 7, 0, Math.PI * 2);
      state.ctx.fill();
    }

    state.ctx.globalAlpha = nodeOpacity;
    state.ctx.beginPath();
    state.ctx.fillStyle = style.fill;
    state.ctx.arc(position.x, position.y, layout.radius, 0, Math.PI * 2);
    state.ctx.fill();

    state.ctx.strokeStyle = isSelected || isRelationshipEndpoint ? "#1a1a1a" : isHovered ? "#1f7de4" : style.stroke;
    state.ctx.lineWidth = isSelected || isRelationshipEndpoint ? 2.2 : 1.2;
    state.ctx.stroke();

    const shouldLabel =
      state.granularOverlayVisible &&
      (selectedNodeId
        ? isWithinNodeNeighborhood
        : granularRelationshipView
          ? isRelationshipEndpoint || isConnectedToSelectedRelationship
          : false);
    if (shouldLabel) {
      state.ctx.globalAlpha = isSelected || isRelationshipEndpoint ? 1 : 0.9;
      state.ctx.fillStyle = style.text;
      state.ctx.font = isSelected ? "700 12px 'Segoe UI'" : "600 11px 'Segoe UI'";
      state.ctx.fillText(displayIdForNode(node), position.x + layout.radius + 8, position.y + 4);
    }
    state.ctx.globalAlpha = 1;

    state.drawCache.nodes.push({
      id: nodeId,
      x: position.x,
      y: position.y,
      radius: layout.radius,
    });
  }
}

function distanceToSegment(point, segment) {
  const dx = segment.x2 - segment.x1;
  const dy = segment.y2 - segment.y1;
  const lengthSquared = dx * dx + dy * dy;
  if (!lengthSquared) {
    return Math.hypot(point.x - segment.x1, point.y - segment.y1);
  }
  let t = ((point.x - segment.x1) * dx + (point.y - segment.y1) * dy) / lengthSquared;
  t = clamp(t, 0, 1);
  const projectionX = segment.x1 + t * dx;
  const projectionY = segment.y1 + t * dy;
  return Math.hypot(point.x - projectionX, point.y - projectionY);
}

function hitTestNode(pointer) {
  let closestNodeId = null;
  let closestDistance = Number.POSITIVE_INFINITY;

  for (const item of state.drawCache.nodes) {
    const distance = Math.hypot(pointer.x - item.x, pointer.y - item.y);
    const hitRadius = Math.max(item.radius + 8, 12);
    if (distance <= hitRadius && distance < closestDistance) {
      closestNodeId = item.id;
      closestDistance = distance;
    }
  }

  return closestNodeId;
}

function hitTestRelationship(pointer) {
  for (let index = state.drawCache.relationships.length - 1; index >= 0; index -= 1) {
    const item = state.drawCache.relationships[index];
    if (distanceToSegment(pointer, item) <= 6) {
      return item.id;
    }
  }
  return null;
}

function fitGraph() {
  const nodeIds = [...state.visibleNodeIds];
  if (!nodeIds.length) {
    return;
  }

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (const nodeId of nodeIds) {
    const layout = ensureLayout(nodeId);
    minX = Math.min(minX, layout.x);
    minY = Math.min(minY, layout.y);
    maxX = Math.max(maxX, layout.x);
    maxY = Math.max(maxY, layout.y);
  }

  const width = Math.max(maxX - minX, 1);
  const height = Math.max(maxY - minY, 1);
  const padding = 120;
  state.camera.x = minX + width / 2;
  state.camera.y = minY + height / 2;
  const scaleX = (state.viewport.width - padding) / width;
  const scaleY = (state.viewport.height - padding) / height;
  state.camera.scale = clamp(Math.min(scaleX, scaleY, 1.22), 0.12, 2.2);
  rememberFullGraphCamera();
}

function pointerPosition(event) {
  const rect = elements.graphCanvas.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function handlePointerDown(event) {
  const pointer = pointerPosition(event);
  state.lastPointer = pointer;

  const nodeId = hitTestNode(pointer);
  if (nodeId) {
    state.draggingNodeId = nodeId;
    selectNode(nodeId);
    elements.graphCanvas.classList.add("is-grabbing");
    return;
  }

  const relationshipId = hitTestRelationship(pointer);
  if (relationshipId) {
    state.isPanning = false;
    selectRelationship(relationshipId);
    return;
  }

  state.isPanning = true;
  clearSelection();
  elements.graphCanvas.classList.add("is-grabbing");
}

function handlePointerMove(event) {
  if (state.draggingMetadataCard) {
    const point = stageRelativePoint(event);
    setMetadataCardPosition(
      point.x - state.draggingMetadataCard.offsetX,
      point.y - state.draggingMetadataCard.offsetY
    );
    return;
  }

  const pointer = pointerPosition(event);

  if (state.draggingNodeId) {
    const world = screenToWorld(pointer);
    const layout = ensureLayout(state.draggingNodeId);
    layout.x = world.x;
    layout.y = world.y;
    layout.anchorX = world.x;
    layout.anchorY = world.y;
    layout.vx = 0;
    layout.vy = 0;
    return;
  }

  if (state.isPanning && state.lastPointer) {
    const dx = (pointer.x - state.lastPointer.x) / state.camera.scale;
    const dy = (pointer.y - state.lastPointer.y) / state.camera.scale;
    state.camera.x -= dx;
    state.camera.y -= dy;
    state.lastPointer = pointer;
    return;
  }

  state.hoveredNodeId = hitTestNode(pointer);
  state.hoveredRelationshipId = state.hoveredNodeId ? null : hitTestRelationship(pointer);
}

function handlePointerUp() {
  if (state.draggingMetadataCardPointerId !== null) {
    elements.metadataCard.releasePointerCapture?.(state.draggingMetadataCardPointerId);
  }
  state.draggingMetadataCard = null;
  state.draggingMetadataCardPointerId = null;
  state.draggingNodeId = null;
  state.isPanning = false;
  state.lastPointer = null;
  elements.graphCanvas.classList.remove("is-grabbing");
  elements.metadataCardHeader.classList.remove("is-dragging");
}

function handleWheel(event) {
  event.preventDefault();
  const pointer = pointerPosition(event);
  const zoomFactor = Math.exp(-event.deltaY * 0.0044);
  zoomAtPoint(pointer, zoomFactor);
}

function zoomAtPoint(screenPoint, zoomFactor) {
  const before = screenToWorld(screenPoint);
  state.camera.scale = clamp(state.camera.scale * zoomFactor, 0.08, 5.4);
  const after = screenToWorld(screenPoint);
  state.camera.x += before.x - after.x;
  state.camera.y += before.y - after.y;
}

function zoomFromCenter(zoomFactor) {
  zoomAtPoint(
    {
      x: state.viewport.width / 2,
      y: state.viewport.height / 2,
    },
    zoomFactor
  );
}

function animationLoop() {
  stepSimulation();
  drawGraph();
  requestAnimationFrame(animationLoop);
}

function collectElements() {
  elements.graphStage = byId("graphStage");
  elements.graphCanvas = byId("graphCanvas");
  elements.metadataCard = byId("metadataCard");
  elements.metadataCardHeader = byId("metadataCardHeader");
  elements.overlayToggleButton = byId("overlayToggleButton");
  elements.resetViewButton = byId("resetViewButton");
  elements.zoomInButton = byId("zoomInButton");
  elements.zoomOutButton = byId("zoomOutButton");
  elements.metadataCloseButton = byId("metadataCloseButton");
  elements.focusNodeButton = byId("focusNodeButton");
  elements.unfocusButton = byId("unfocusButton");
  elements.showAllButton = byId("showAllButton");
  elements.metadataEyebrow = byId("metadataEyebrow");
  elements.metadataTitle = byId("metadataTitle");
  elements.metadataSubtitle = byId("metadataSubtitle");
  elements.metadataFields = byId("metadataFields");
  elements.metadataConnectionsSection = byId("metadataConnectionsSection");
  elements.metadataConnectionsLabel = byId("metadataConnectionsLabel");
  elements.metadataConnections = byId("metadataConnections");
}


function bindEvents() {
  elements.graphCanvas.addEventListener("pointerdown", handlePointerDown);
  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", handlePointerUp);
  elements.graphCanvas.addEventListener("wheel", handleWheel, { passive: false });
  elements.graphCanvas.addEventListener("dblclick", (event) => {
    const nodeId = hitTestNode(pointerPosition(event));
    if (nodeId) {
      expandNode(nodeId);
    }
  });

  elements.metadataCard.addEventListener("pointerdown", startMetadataCardDrag);

  elements.overlayToggleButton.addEventListener("click", () => {
    state.granularOverlayVisible = !state.granularOverlayVisible;
    syncOverlayToggleButton();
  });
  elements.resetViewButton.addEventListener("click", resetGraphView);
  elements.zoomInButton.addEventListener("click", () => zoomFromCenter(1.32));
  elements.zoomOutButton.addEventListener("click", () => zoomFromCenter(1 / 1.32));
  elements.metadataCloseButton.addEventListener("click", () => clearSelection());
  elements.focusNodeButton.addEventListener("click", () => {
    if (state.selectedNodeId) {
      focusNeighborhood(state.selectedNodeId, 1);
    }
  });
  elements.unfocusButton.addEventListener("click", () => restorePreviousView());
  elements.showAllButton.addEventListener("click", () => showFullGraph());

  window.addEventListener("resize", () => {
    resizeCanvas();
    if (!elements.metadataCard.classList.contains("hidden") && elements.metadataCard.style.transform === "none") {
      const currentLeft = Number.parseFloat(elements.metadataCard.style.left) || 16;
      const currentTop = Number.parseFloat(elements.metadataCard.style.top) || 16;
      setMetadataCardPosition(currentLeft, currentTop);
    }
    fitGraph();
  });
}

async function loadGraph() {
  const response = await fetch("/api/graph");
  if (!response.ok) {
    throw new Error(`Graph API failed with ${response.status}`);
  }
  return response.json();
}

let explorerMounted = false;
let animationFrameScheduled = false;

async function init() {
  collectElements();
  state.canvas = elements.graphCanvas;
  state.ctx = elements.graphCanvas.getContext("2d");

  try {
    const payload = await loadGraph();
    prepareGraphData(payload);
  } catch (error) {
    console.error(error);
    explorerMounted = false;
    return;
  }

  resizeCanvas();
  bindEvents();
  syncOverlayToggleButton();
  showFullGraph();
  if (!animationFrameScheduled) {
    animationFrameScheduled = true;
    animationLoop();
  }
}

export async function mountGraphExplorer() {
  if (explorerMounted) {
    return undefined;
  }
  explorerMounted = true;
  await init();
  return undefined;
}

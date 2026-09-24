import { ENTITY_STYLES } from "./constants.js";

export const state = {
  data: null,
  nodesById: new Map(),
  relationshipsById: new Map(),
  adjacencyByNode: new Map(),
  visibleNodeIds: new Set(),
  visibleRelationshipIds: new Set(),
  layoutByNodeId: new Map(),
  selectedNodeId: null,
  selectedRelationshipId: null,
  hoveredNodeId: null,
  hoveredRelationshipId: null,
  granularOverlayVisible: false,
  viewMode: "global",
  viewHistory: [],
  activeFocusNodeId: null,
  canvas: null,
  ctx: null,
  viewport: { width: 0, height: 0, dpr: 1 },
  camera: { x: 0, y: 0, scale: 1 },
  fullGraphCamera: null,
  drawCache: { nodes: [], relationships: [] },
  draggingNodeId: null,
  draggingMetadataCard: null,
  draggingMetadataCardPointerId: null,
  isPanning: false,
  lastPointer: null,
  chatHighlightedNodeIds: new Set(),
  chatHighlightedRelationshipIds: new Set(),
};

export const elements = {};

export function byId(id) {
  return document.getElementById(id);
}

export function createElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  if (text !== undefined) {
    element.textContent = text;
  }
  return element;
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function uniq(values) {
  return [...new Set(values)];
}

export function getNode(nodeId) {
  return state.nodesById.get(nodeId) ?? null;
}

export function getRelationship(relationshipId) {
  return state.relationshipsById.get(relationshipId) ?? null;
}

export function styleForEntity(entityType) {
  return ENTITY_STYLES[entityType] ?? { fill: "#6f8eb5", stroke: "#d4deee", text: "#39506e" };
}

export function getAdjacentRelationshipIds(nodeId) {
  return state.adjacencyByNode.get(nodeId) ?? [];
}

export function getOppositeNodeId(relationship, nodeId) {
  return relationship.source === nodeId ? relationship.target : relationship.source;
}

export function formatCount(value) {
  return value.toLocaleString();
}

export function displayIdForNode(node) {
  return String(node.entityId ?? node.id ?? node.label ?? "");
}

export function hasRenderableValue(value) {
  return value !== undefined && value !== null && value !== "";
}

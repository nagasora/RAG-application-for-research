/**
 * Pure helpers for the editable mind-map view. They intentionally accept plain
 * objects so layout safety can be tested without mounting React.
 */
export function mindMapChildren(nodes, parentId) {
  return nodes
    .filter(node => node.parent_id === parentId)
    .sort((left, right) => left.order_index - right.order_index || left.id.localeCompare(right.id));
}

export function mindMapDescendantIds(nodes, nodeId) {
  const result = [];
  const seen = new Set([nodeId]);
  const queue = [nodeId];
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    for (const child of mindMapChildren(nodes, queue[cursor])) {
      if (seen.has(child.id)) continue;
      seen.add(child.id);
      result.push(child.id);
      queue.push(child.id);
    }
  }
  return result;
}

export function visibleMindMap(nodes, collapsedNodeIds = []) {
  if (!nodes.length) return { nodes: [], edges: [] };
  const collapsed = new Set(collapsedNodeIds);
  const hidden = new Set();
  for (const nodeId of collapsed) {
    for (const descendantId of mindMapDescendantIds(nodes, nodeId)) hidden.add(descendantId);
  }
  const visibleNodes = nodes.filter(node => !hidden.has(node.id));
  const visibleIds = new Set(visibleNodes.map(node => node.id));
  const edges = visibleNodes.flatMap(node => (
    node.parent_id && visibleIds.has(node.parent_id)
      ? [{ id: `${node.parent_id}:${node.id}`, source: node.parent_id, target: node.id }]
      : []
  ));
  return { nodes: visibleNodes, edges };
}

export function mindMapKeyboardTarget(nodes, currentId, key) {
  const current = nodes.find(node => node.id === currentId);
  if (!current) return null;
  if (key === "ArrowLeft") return current.parent_id && nodes.some(node => node.id === current.parent_id) ? current.parent_id : null;
  if (key === "ArrowRight") return mindMapChildren(nodes, current.id)[0]?.id ?? null;
  if (key !== "ArrowUp" && key !== "ArrowDown") return null;
  const siblings = mindMapChildren(nodes, current.parent_id);
  const index = siblings.findIndex(node => node.id === current.id);
  if (index < 0) return null;
  const offset = key === "ArrowUp" ? -1 : 1;
  return siblings[index + offset]?.id ?? null;
}

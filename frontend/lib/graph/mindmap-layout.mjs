const NODE_WIDTH = 190;
const NODE_HEIGHT = 98;
const PADDING_X = 64;
const PADDING_Y = 56;

/**
 * Calculates deterministic coordinates for a graph shown as a mind map.
 *
 * This module intentionally has no React dependency so its input safety can be
 * regression-tested without mounting a component.
 */
export function mindMapLayout(nodes, edges, rootId) {
  if (!nodes.length) return [];

  const root = nodes.find(node => node.id === rootId) ?? nodes[0];
  const nodeIds = new Set(nodes.map(node => node.id));
  const positions = new Map([[root.id, { side: "center", depth: 0 }]]);
  const neighbours = new Map();

  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    neighbours.set(edge.source, [...(neighbours.get(edge.source) ?? []), { id: edge.target, direction: "out" }]);
    neighbours.set(edge.target, [...(neighbours.get(edge.target) ?? []), { id: edge.source, direction: "in" }]);
  }

  const queue = [root.id];
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const currentId = queue[cursor];
    const current = positions.get(currentId);
    if (!current) continue;
    for (const neighbour of neighbours.get(currentId) ?? []) {
      if (positions.has(neighbour.id)) continue;
      const side = current.side === "center" ? (neighbour.direction === "out" ? "right" : "left") : current.side;
      positions.set(neighbour.id, { side, depth: current.depth + 1 });
      queue.push(neighbour.id);
    }
  }

  // Keep disconnected ideas visible as a separate right-hand branch.
  for (const node of nodes) {
    if (!positions.has(node.id)) positions.set(node.id, { side: "right", depth: 1 });
  }

  const rootX = 560;
  const rootY = Math.max(290, Math.ceil(nodes.length / 2) * 68 + 110);
  const grouped = new Map();
  for (const node of nodes) {
    const position = positions.get(node.id);
    if (!position || position.side === "center") continue;
    const key = `${position.side}:${position.depth}`;
    grouped.set(key, [...(grouped.get(key) ?? []), node]);
  }

  const rawNodes = nodes.map(node => {
    const position = positions.get(node.id);
    if (!position || position.side === "center") {
      return { ...node, x: rootX - NODE_WIDTH / 2, y: rootY - NODE_HEIGHT / 2 };
    }
    const group = grouped.get(`${position.side}:${position.depth}`) ?? [node];
    const index = group.findIndex(item => item.id === node.id);
    const y = rootY + (index - (group.length - 1) / 2) * 132;
    const x = rootX + (position.side === "right" ? 1 : -1) * (position.depth * 282 + NODE_WIDTH / 2);
    return { ...node, x, y };
  });

  const minX = Math.min(...rawNodes.map(node => node.x));
  const minY = Math.min(...rawNodes.map(node => node.y));
  const offsetX = minX < PADDING_X ? PADDING_X - minX : 0;
  const offsetY = minY < PADDING_Y ? PADDING_Y - minY : 0;
  return rawNodes.map(node => ({ ...node, x: node.x + offsetX, y: node.y + offsetY }));
}

export type MindMapLayoutNode = { id: string };

export type MindMapLayoutEdge = {
  source: string;
  target: string;
};

export type PositionedMindMapNode<T extends MindMapLayoutNode> = T & {
  x: number;
  y: number;
};

export declare function mindMapLayout<T extends MindMapLayoutNode>(
  nodes: readonly T[],
  edges: readonly MindMapLayoutEdge[],
  rootId?: string,
): PositionedMindMapNode<T>[];

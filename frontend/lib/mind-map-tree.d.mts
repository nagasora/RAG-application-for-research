export type MindMapTreeNode = {
  id: string;
  parent_id: string | null;
  order_index: number;
};

export declare function mindMapChildren<T extends MindMapTreeNode>(nodes: readonly T[], parentId: string | null): T[];
export declare function mindMapDescendantIds<T extends MindMapTreeNode>(nodes: readonly T[], nodeId: string): string[];
export declare function visibleMindMap<T extends MindMapTreeNode>(
  nodes: readonly T[],
  collapsedNodeIds?: readonly string[],
): { nodes: T[]; edges: Array<{ id: string; source: string; target: string }> };
export declare function mindMapKeyboardTarget<T extends MindMapTreeNode>(
  nodes: readonly T[],
  currentId: string,
  key: string,
): string | null;

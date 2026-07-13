"use client";

import { KeyboardEvent, useId, useMemo, useState } from "react";

export type GraphNodeType = "source" | "idea" | "constraint" | "hypothesis";
export type GraphNodeStatus = "active" | "review_pending" | "review_required" | "verified" | "rejected" | "superseded" | "pruned";
export type GraphEdgeRelation = string;

export type GraphNode = {
  id: string;
  label: string;
  summary?: string;
  type: GraphNodeType;
  status: GraphNodeStatus;
  layer: number;
  /** Optional persisted canvas coordinates. Values use the SVG viewBox coordinate system. */
  position?: { x: number; y: number };
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  relation: GraphEdgeRelation;
  summary?: string;
};

type GraphCanvasProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeIds?: readonly string[];
  onNodeSelect?: (node: GraphNode) => void;
  onEdgeSelect?: (edge: GraphEdge) => void;
  ariaLabel?: string;
  className?: string;
};

const NODE_WIDTH = 190;
const NODE_HEIGHT = 98;
const HORIZONTAL_GAP = 105;
const VERTICAL_GAP = 48;
const PADDING_X = 64;
const PADDING_Y = 56;

const TYPE_LABEL: Record<GraphNodeType, string> = {
  source: "Source",
  idea: "Idea",
  constraint: "Constraint",
  hypothesis: "Hypothesis",
};

const STATUS_LABEL: Record<GraphNodeStatus, string> = {
  active: "有効",
  review_pending: "レビュー待ち",
  review_required: "要レビュー",
  verified: "検証済み",
  rejected: "棄却",
  superseded: "更新済み",
  pruned: "枝払い済み",
};

const RELATION_LABEL: Record<GraphEdgeRelation, string> = {
  informs: "根拠にする",
  extends: "拡張する",
  formulates: "定式化する",
  contradicts: "矛盾する",
};

const STATUS_STYLE: Record<GraphNodeStatus, { fill: string; stroke: string; badge: string }> = {
  active: { fill: "#f8fffa", stroke: "#4e9570", badge: "#dcefe3" },
  review_pending: { fill: "#fffbf2", stroke: "#c18735", badge: "#f8e7c7" },
  review_required: { fill: "#fff8eb", stroke: "#aa6b18", badge: "#f8e1b8" },
  verified: { fill: "#f0fbf5", stroke: "#28795a", badge: "#cbe9d8" },
  rejected: { fill: "#fff6f5", stroke: "#b85b54", badge: "#f6d7d4" },
  superseded: { fill: "#f6f6f4", stroke: "#9ca5a0", badge: "#e4e6e2" },
  pruned: { fill: "#f8f6f3", stroke: "#9b8471", badge: "#eadfd5" },
};

const RELATION_STYLE: Record<string, { color: string; dash?: string }> = {
  informs: { color: "#4f8069" },
  supports: { color: "#3c806c" },
  extends: { color: "#467f98" },
  formulates: { color: "#8a649d", dash: "7 4" },
  contradicts: { color: "#bd5751", dash: "5 4" },
  implements: { color: "#5777a4" },
  depends_on: { color: "#7c7162", dash: "3 3" },
  related: { color: "#77857f", dash: "2 4" },
};

type PositionedNode = GraphNode & { x: number; y: number };

function truncate(value: string, maximum: number) {
  return value.length > maximum ? `${value.slice(0, Math.max(0, maximum - 1))}…` : value;
}

function nodeKeyboardSelect(event: KeyboardEvent<SVGGElement>, onSelect: () => void) {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  onSelect();
}

/**
 * Read-first SVG graph. Layout is deterministic when a node has no persisted position,
 * so the parent may progressively add persisted canvas coordinates without changing its API.
 */
export function GraphCanvas({
  nodes,
  edges,
  selectedNodeIds = [],
  onNodeSelect,
  onEdgeSelect,
  ariaLabel = "研究知識グラフ",
  className = "",
}: GraphCanvasProps) {
  const markerId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [focusedEdgeId, setFocusedEdgeId] = useState<string | null>(null);
  const selected = useMemo(() => new Set(selectedNodeIds), [selectedNodeIds]);
  const { positionedNodes, layers, width, height } = useMemo(() => {
    const grouped = new Map<number, GraphNode[]>();
    for (const node of nodes) grouped.set(node.layer, [...(grouped.get(node.layer) ?? []), node]);
    const layerNumbers = [...grouped.keys()].sort((left, right) => left - right);
    const largestLayer = Math.max(1, ...[...grouped.values()].map(layer => layer.length));
    const maxPositionX = Math.max(0, ...nodes.map(node => node.position?.x ?? 0));
    const maxPositionY = Math.max(0, ...nodes.map(node => node.position?.y ?? 0));
    const calculatedWidth = Math.max(
      PADDING_X * 2 + Math.max(1, layerNumbers.length) * NODE_WIDTH + Math.max(0, layerNumbers.length - 1) * HORIZONTAL_GAP,
      maxPositionX + NODE_WIDTH + PADDING_X,
    );
    const calculatedHeight = Math.max(
      PADDING_Y * 2 + largestLayer * NODE_HEIGHT + Math.max(0, largestLayer - 1) * VERTICAL_GAP,
      maxPositionY + NODE_HEIGHT + PADDING_Y,
    );
    const nextNodes = layerNumbers.flatMap((layer, layerIndex) => (grouped.get(layer) ?? []).map((node, nodeIndex) => ({
      ...node,
      x: node.position?.x ?? PADDING_X + layerIndex * (NODE_WIDTH + HORIZONTAL_GAP),
      y: node.position?.y ?? PADDING_Y + nodeIndex * (NODE_HEIGHT + VERTICAL_GAP) + (largestLayer - (grouped.get(layer)?.length ?? 0)) * (NODE_HEIGHT + VERTICAL_GAP) / 2,
    })));
    return { positionedNodes:nextNodes, layers:layerNumbers, width:calculatedWidth, height:calculatedHeight };
  }, [nodes]);
  const nodesById = useMemo(() => new Map(positionedNodes.map(node => [node.id, node])), [positionedNodes]);

  if (!nodes.length) {
    return <section aria-label={ariaLabel} className={`grid min-h-72 place-items-center rounded-2xl border border-dashed border-[#cbd2cd] bg-[#fbfbf8] p-6 text-center ${className}`}>
      <div><p className="text-sm font-semibold text-[#52605b]">表示する知識ノードはまだありません。</p><p className="mt-2 text-xs leading-5 text-[#7a837f]">論文・会話・メモからノードを作成すると、ここに関係性が表示されます。</p></div>
    </section>;
  }

  return <section aria-label={ariaLabel} className={`overflow-hidden rounded-2xl border border-[#d8ded9] bg-[#f8faf7] ${className}`}>
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[#d8ded9] bg-white/80 px-4 py-3 text-[10px] text-[#52605b]">
      <span className="font-bold uppercase tracking-[.14em] text-[#35634f]">Graph canvas</span>
      <span>{nodes.length} nodes</span><span>{edges.length} edges</span>
      <span className="ml-auto hidden text-[#7a837f] sm:inline">Enter または Space でノードを選択</span>
    </div>
    <div className="overflow-auto overscroll-contain" tabIndex={0} aria-label={`${ariaLabel}。横方向と縦方向にスクロールできます。`}>
      <svg viewBox={`0 0 ${width} ${height}`} role="group" aria-label={ariaLabel} className="block min-h-[390px] min-w-[720px] w-full" preserveAspectRatio="xMinYMin meet">
        <defs>
          <marker id={`graph-arrow-${markerId}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker>
        </defs>
        {layers.map((layer, index) => {
          const x = PADDING_X + index * (NODE_WIDTH + HORIZONTAL_GAP);
          return <g key={layer} aria-hidden="true"><text x={x} y="27" className="fill-[#7a837f] text-[11px] font-bold" letterSpacing="1.2">LAYER {layer}</text><line x1={x} x2={x + NODE_WIDTH} y1="37" y2="37" stroke="#d8ded9" /></g>;
        })}
        {edges.map(edge => {
          const source = nodesById.get(edge.source); const target = nodesById.get(edge.target);
          if (!source || !target) return null;
          const style = RELATION_STYLE[edge.relation] ?? { color: "#77857f", dash: "2 4" };
          const startX = source.x + NODE_WIDTH; const startY = source.y + NODE_HEIGHT / 2;
          const endX = target.x; const endY = target.y + NODE_HEIGHT / 2;
          const midpointX = (startX + endX) / 2; const midpointY = (startY + endY) / 2;
          const relation = RELATION_LABEL[edge.relation as keyof typeof RELATION_LABEL] ?? edge.relation;
          const path = `M ${startX} ${startY} C ${startX + 36} ${startY}, ${endX - 36} ${endY}, ${endX} ${endY}`;
          const selectable = Boolean(onEdgeSelect);
          const isFocused = focusedEdgeId === edge.id;
          return <g key={edge.id} role={selectable ? "button" : undefined} tabIndex={selectable ? 0 : undefined} aria-label={selectable ? `${relation}: ${source.label} から ${target.label}` : undefined} className={selectable ? "cursor-pointer focus:outline-none" : ""} onClick={selectable ? () => onEdgeSelect?.(edge) : undefined} onKeyDown={selectable ? event => nodeKeyboardSelect(event, () => onEdgeSelect?.(edge)) : undefined} onFocus={selectable ? () => setFocusedEdgeId(edge.id) : undefined} onBlur={selectable ? () => setFocusedEdgeId(current => current === edge.id ? null : current) : undefined}>
            {isFocused && <path d={path} fill="none" stroke="#10231b" strokeWidth="6" strokeOpacity=".72" strokeLinecap="round" pointerEvents="none" />}
            <path d={path} fill="none" stroke={style.color} strokeWidth="2.25" strokeDasharray={style.dash} markerEnd={`url(#graph-arrow-${markerId})`} className="transition-opacity group-hover:opacity-80" />
            <rect x={midpointX - 36} y={midpointY - 12} width="72" height="19" rx="8" fill="#f8faf7" stroke={style.color} strokeOpacity=".28" />
            <text x={midpointX} y={midpointY + 1.5} textAnchor="middle" className="fill-[#52605b] text-[9px] font-semibold">{relation}</text>
          </g>;
        })}
        {positionedNodes.map(node => {
          const style = STATUS_STYLE[node.status]; const isSelected = selected.has(node.id); const isFocused = focusedNodeId === node.id; const isSuperseded = node.status === "superseded";
          const select = () => onNodeSelect?.(node);
          return <g key={node.id} role="button" tabIndex={0} aria-pressed={isSelected} aria-label={`${node.label}、${TYPE_LABEL[node.type]}、${STATUS_LABEL[node.status]}、Layer ${node.layer}`} className="cursor-pointer outline-none" onClick={select} onKeyDown={event => nodeKeyboardSelect(event, select)} onFocus={() => setFocusedNodeId(node.id)} onBlur={() => setFocusedNodeId(current => current === node.id ? null : current)}>
            <rect x={node.x - (isSelected ? 4 : 0)} y={node.y - (isSelected ? 4 : 0)} width={NODE_WIDTH + (isSelected ? 8 : 0)} height={NODE_HEIGHT + (isSelected ? 8 : 0)} rx="17" fill={isSelected ? "#dcefe3" : "transparent"} />
            <rect x={node.x} y={node.y} width={NODE_WIDTH} height={NODE_HEIGHT} rx="14" fill={style.fill} stroke={isSelected ? "#164f3b" : style.stroke} strokeWidth={isSelected ? "3" : "1.5"} strokeDasharray={isSuperseded ? "6 4" : undefined} />
            {isFocused && <rect x={node.x - 6} y={node.y - 6} width={NODE_WIDTH + 12} height={NODE_HEIGHT + 12} rx="20" fill="none" stroke="#10231b" strokeWidth="3" strokeDasharray="7 4" pointerEvents="none" />}
            <rect x={node.x + 12} y={node.y + 12} width="6" height="6" rx="3" fill={style.stroke} />
            <text x={node.x + 25} y={node.y + 18} className="fill-[#68736f] text-[9px] font-bold" letterSpacing=".7">{TYPE_LABEL[node.type].toUpperCase()} · {STATUS_LABEL[node.status]}</text>
            <text x={node.x + 12} y={node.y + 43} className="fill-[#17201d] text-[13px] font-semibold">{truncate(node.label, 23)}</text>
            {node.summary && <text x={node.x + 12} y={node.y + 64} className="fill-[#68736f] text-[10px]">{truncate(node.summary, 31)}</text>}
            <rect x={node.x + 12} y={node.y + 75} width={isSelected ? "62" : "53"} height="13" rx="6.5" fill={style.badge} />
            <text x={node.x + 18} y={node.y + 84.5} className="fill-[#40534a] text-[8px] font-bold">{isSelected ? "SELECTED" : `LAYER ${node.layer}`}</text>
          </g>;
        })}
      </svg>
    </div>
    <div className="flex flex-wrap gap-x-4 gap-y-2 border-t border-[#d8ded9] bg-white/75 px-4 py-2.5 text-[10px] text-[#68736f]" aria-label="グラフ凡例">
      {(Object.keys(STATUS_LABEL) as GraphNodeStatus[]).map(status => <span key={status} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ backgroundColor:STATUS_STYLE[status].stroke }}/>{STATUS_LABEL[status]}</span>)}
      <span className="ml-auto">実線: 根拠・拡張 / 破線: 定式化・矛盾</span>
    </div>
  </section>;
}

"use client";

import { KeyboardEvent, useId, useMemo, useState } from "react";

import { mindMapLayout } from "@/lib/graph/mindmap-layout.mjs";
import {
  mindMapDescendantIds,
  mindMapKeyboardTarget,
  visibleMindMap,
} from "@/lib/mind-map-tree.mjs";

export type EditableMindMapNode = {
  id: string;
  parent_id: string | null;
  kind: "root" | "theme" | "claim" | "question" | "method" | "finding" | "task" | "note" | "link";
  title: string;
  body: string;
  status: "review_pending" | "active" | "rejected";
  depth: number;
  order_index: number;
};

type MindMapCanvasProps = {
  nodes: EditableMindMapNode[];
  selectedNodeId?: string | null;
  collapsedNodeIds?: readonly string[];
  generatingNodeId?: string | null;
  onNodeSelect: (node: EditableMindMapNode) => void;
  onNodeToggle: (node: EditableMindMapNode) => void;
};

const WIDTH = 210;
const HEIGHT = 108;
const KIND_COLOR: Record<EditableMindMapNode["kind"], string> = {
  root:"#164f3b", theme:"#447a64", claim:"#256b82", question:"#8a5e26",
  method:"#6d5a91", finding:"#2f7b5b", task:"#a05252", note:"#697772", link:"#55728d",
};
const KIND_LABEL: Record<EditableMindMapNode["kind"], string> = {
  root:"Root", theme:"Theme", claim:"Claim", question:"Question", method:"Method",
  finding:"Finding", task:"Task", note:"Note", link:"Link",
};

function clipped(value: string, maximum: number) {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > maximum ? `${normalized.slice(0, maximum - 1)}…` : normalized;
}

export function MindMapCanvas({
  nodes,
  selectedNodeId = null,
  collapsedNodeIds = [],
  generatingNodeId = null,
  onNodeSelect,
  onNodeToggle,
}: MindMapCanvasProps) {
  const markerId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [zoom, setZoom] = useState(1);
  const visible = useMemo(() => visibleMindMap(nodes, collapsedNodeIds), [collapsedNodeIds, nodes]);
  const positioned = useMemo(() => mindMapLayout(
    visible.nodes,
    visible.edges,
    visible.nodes.find(node => node.parent_id === null)?.id ?? visible.nodes[0]?.id ?? "",
  ), [visible]);
  const byId = useMemo(() => new Map(positioned.map(node => [node.id, node])), [positioned]);
  const highlighted = useMemo(() => new Set(selectedNodeId ? mindMapDescendantIds(nodes, selectedNodeId) : []), [nodes, selectedNodeId]);
  const width = Math.max(820, ...positioned.map(node => node.x + WIDTH + 80));
  const height = Math.max(520, ...positioned.map(node => node.y + HEIGHT + 80));

  if (!nodes.length) {
    return <div className="grid min-h-[480px] place-items-center rounded-2xl border border-dashed border-[#cbd2cd] bg-[#fbfbf8] p-8 text-center">
      <div><p className="text-sm font-semibold text-[#52605b]">マインドマップはまだありません。</p><p className="mt-2 text-xs leading-5 text-[#7a837f]">解析済み論文または選択した根拠から作成できます。</p></div>
    </div>;
  }

  const keyboard = (event: KeyboardEvent<SVGGElement>, node: EditableMindMapNode) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onNodeSelect(node);
      return;
    }
    if (event.key === " ") {
      event.preventDefault();
      onNodeToggle(node);
      return;
    }
    const targetId = mindMapKeyboardTarget(visible.nodes, node.id, event.key);
    const target = targetId ? visible.nodes.find(item => item.id === targetId) : null;
    if (!target) return;
    event.preventDefault();
    onNodeSelect(target);
    document.getElementById(`mind-map-node-${target.id}`)?.focus();
  };

  return <section aria-label="編集可能なマインドマップ" className="overflow-hidden rounded-2xl border border-[#d8ded9] bg-[#f8faf7]">
    <div className="flex items-center gap-3 border-b border-[#d8ded9] bg-white/85 px-4 py-3 text-[10px] text-[#52605b]">
      <span className="font-bold uppercase tracking-[.14em] text-[#35634f]">Mind map</span>
      <span>{visible.nodes.length}/{nodes.length} nodes</span>
      <span className="hidden sm:inline">Enter: 選択 · Space: 折りたたみ · 矢印: 移動</span>
      <div className="ml-auto flex items-center gap-1" aria-label="マインドマップの拡大縮小">
        <button type="button" aria-label="マインドマップを縮小" onClick={() => setZoom(value => Math.max(.65, value - .15))} className="grid h-7 w-7 place-items-center rounded border border-[#d8ded9] bg-white text-sm">−</button>
        <span className="w-10 text-center font-mono">{Math.round(zoom * 100)}%</span>
        <button type="button" aria-label="マインドマップを拡大" onClick={() => setZoom(value => Math.min(1.5, value + .15))} className="grid h-7 w-7 place-items-center rounded border border-[#d8ded9] bg-white text-sm">+</button>
      </div>
    </div>
    <div className="graph-dot-grid overflow-auto overscroll-contain" tabIndex={0}>
      <svg viewBox={`0 0 ${width} ${height}`} className="block min-h-[480px]" style={{ width:`${zoom * 100}%`, minWidth:`${Math.max(760, 760 * zoom)}px` }} preserveAspectRatio="xMinYMin meet">
        <defs><marker id={`mind-map-arrow-${markerId}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#718079"/></marker></defs>
        {visible.edges.map(edge => {
          const source = byId.get(edge.source); const target = byId.get(edge.target);
          if (!source || !target) return null;
          const startX = source.x + WIDTH; const startY = source.y + HEIGHT / 2;
          const endX = target.x; const endY = target.y + HEIGHT / 2;
          const bend = Math.max(42, (endX - startX) / 2);
          return <path key={edge.id} d={`M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`} fill="none" stroke={highlighted.has(target.id) ? "#d68a22" : "#718079"} strokeWidth={highlighted.has(target.id) ? 3 : 2} markerEnd={`url(#mind-map-arrow-${markerId})`}/>;
        })}
        {positioned.map(node => {
          const selected = node.id === selectedNodeId;
          const collapsed = collapsedNodeIds.includes(node.id);
          const hasChildren = nodes.some(item => item.parent_id === node.id);
          return <g id={`mind-map-node-${node.id}`} key={node.id} role="button" tabIndex={0} aria-pressed={selected} aria-label={`${node.title}、${KIND_LABEL[node.kind]}、${node.status}`} onClick={() => onNodeSelect(node)} onDoubleClick={() => hasChildren && onNodeToggle(node)} onKeyDown={event => keyboard(event, node)} className="cursor-pointer outline-none">
            <rect x={node.x - (selected ? 4 : 0)} y={node.y - (selected ? 4 : 0)} width={WIDTH + (selected ? 8 : 0)} height={HEIGHT + (selected ? 8 : 0)} rx="18" fill={selected ? "#dcefe3" : highlighted.has(node.id) ? "#fff0d8" : "#fff"} stroke={selected ? "#164f3b" : KIND_COLOR[node.kind]} strokeWidth={selected ? 3 : 1.6} strokeDasharray={node.status === "review_pending" ? "5 3" : undefined}/>
            <circle cx={node.x + 16} cy={node.y + 17} r="4" fill={KIND_COLOR[node.kind]}/>
            <text x={node.x + 27} y={node.y + 20} className="fill-[#68736f] text-[9px] font-bold" letterSpacing=".6">{KIND_LABEL[node.kind].toUpperCase()} · {node.status === "review_pending" ? "レビュー待ち" : node.status === "active" ? "有効" : "棄却"}</text>
            <text x={node.x + 14} y={node.y + 48} className="fill-[#17201d] text-[13px] font-semibold">{clipped(node.title, 25)}</text>
            <text x={node.x + 14} y={node.y + 70} className="fill-[#68736f] text-[10px]">{clipped(node.body, 34)}</text>
            <text x={node.x + 14} y={node.y + 94} className="fill-[#89918e] text-[9px]">depth {node.depth} · order {node.order_index}</text>
            {hasChildren && <g aria-hidden="true" onClick={event => { event.stopPropagation(); onNodeToggle(node); }}>
              <circle cx={node.x + WIDTH - 14} cy={node.y + HEIGHT / 2} r="10" fill="#edf5f0" stroke="#9db9aa"/>
              <text x={node.x + WIDTH - 14} y={node.y + HEIGHT / 2 + 4} textAnchor="middle" className="fill-[#164f3b] text-[13px] font-bold">{collapsed ? "+" : "−"}</text>
            </g>}
            {generatingNodeId === node.id && <text x={node.x + WIDTH - 12} y={node.y + 18} textAnchor="end" className="fill-[#a06a28] text-[9px] font-bold">生成中…</text>}
          </g>;
        })}
      </svg>
    </div>
  </section>;
}

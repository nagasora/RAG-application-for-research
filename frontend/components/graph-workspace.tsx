"use client";

import { ArrowPathIcon, PlusIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { GraphCanvas, type GraphEdge, type GraphNode, type GraphNodeStatus, type GraphNodeType } from "@/components/graph-canvas";
import { createGraphEdge, createGraphNode, getGraphSnapshot, importGraphSource, listGraphSourceSpans, listGraphSources, retrieveGraph, updateGraphEdgeStatus, updateGraphNodeStatus, type GraphRetrievalHit, type KnowledgeEdge as ApiKnowledgeEdge, type KnowledgeEdgeStatusUpdate, type KnowledgeNode as ApiKnowledgeNode, type KnowledgeNodeStatusUpdate, type SourceSpan, type SourceVersion } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type EvidenceRef = { source_span_id: string };
type KnowledgeNode = ApiKnowledgeNode & { evidence?: EvidenceRef[] };
type KnowledgeEdge = ApiKnowledgeEdge;
type CanvasLayout = { knowledge_node_id: string; x: number; y: number };
type Snapshot = { nodes: KnowledgeNode[]; edges: KnowledgeEdge[]; layouts: CanvasLayout[] };

type GraphWorkspaceProps = {
  canWrite: boolean;
  onOpenPaper: (paperId: string) => void;
};

const SOURCE_KINDS = ["latex", "python", "notebook", "csv", "chat", "markdown"] as const;
type SourceKind = typeof SOURCE_KINDS[number];
const MAX_SOURCE_BYTES = 5 * 1024 * 1024;
const EDGE_RELATIONS = ["informs", "supports", "extends", "formulates", "contradicts", "implements", "depends_on", "related"] as const;
const EDGE_STATUSES: KnowledgeEdgeStatusUpdate["status"][] = ["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"];
const NODE_STATUSES: KnowledgeNodeStatusUpdate["status"][] = ["review_pending", "active", "verified", "rejected", "superseded", "review_required", "pruned"];

function hopEdgeIds(hits: GraphRetrievalHit[]) {
  return hits.flatMap(hit => (hit.hop_path ?? []).flatMap(step => {
    const edgeId = typeof step === "object" && step !== null && "edge_id" in step ? step.edge_id : undefined;
    return typeof edgeId === "string" ? [edgeId] : [];
  }));
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function label(content: string) {
  return content.replace(/\s+/g, " ").trim().slice(0, 80) || "内容なし";
}

function toCanvasNode(node: KnowledgeNode, layouts: Map<string, CanvasLayout>): GraphNode {
  const layout = layouts.get(node.id);
  return {
    id: node.id, label: label(node.content), summary: node.phase,
    type: node.node_type, status: node.status, layer: node.layer,
    position: layout ? { x:layout.x, y:layout.y } : undefined,
  };
}

function toCanvasEdge(edge: KnowledgeEdge): GraphEdge {
  return {
    id: edge.id, source: edge.source_node_id, target: edge.target_node_id,
    relation: edge.relation,
    summary: edge.relation,
  };
}

export function GraphWorkspace({ canWrite, onOpenPaper }: GraphWorkspaceProps) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ nodes:[], edges:[], layouts:[] });
  const [sources, setSources] = useState<SourceVersion[]>([]);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedEdgeId, setSelectedEdgeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [content, setContent] = useState("");
  const [nodeType, setNodeType] = useState<"idea" | "hypothesis" | "constraint">("idea");
  const [sourceKind, setSourceKind] = useState<SourceKind>("markdown");
  const [sourceLocator, setSourceLocator] = useState("");
  const [sourceContent, setSourceContent] = useState("");
  const [sourceImporting, setSourceImporting] = useState(false);
  const [sourceNotice, setSourceNotice] = useState("");
  const [edgeSourceId, setEdgeSourceId] = useState("");
  const [edgeTargetId, setEdgeTargetId] = useState("");
  const [edgeRelation, setEdgeRelation] = useState<(typeof EDGE_RELATIONS)[number]>("informs");
  const [edgeSourceVersionId, setEdgeSourceVersionId] = useState("");
  const [edgeSpans, setEdgeSpans] = useState<SourceSpan[]>([]);
  const [edgeSpanIds, setEdgeSpanIds] = useState<string[]>([]);
  const [edgeSpansLoading, setEdgeSpansLoading] = useState(false);
  const [edgeCreating, setEdgeCreating] = useState(false);
  const [edgeNotice, setEdgeNotice] = useState("");
  const [edgeStatus, setEdgeStatus] = useState<KnowledgeEdgeStatusUpdate["status"]>("review_pending");
  const [edgeStatusReason, setEdgeStatusReason] = useState("");
  const [edgeStatusUpdating, setEdgeStatusUpdating] = useState(false);
  const [edgeStatusNotice, setEdgeStatusNotice] = useState("");
  const [nodeStatus, setNodeStatus] = useState<KnowledgeNodeStatusUpdate["status"]>("review_pending");
  const [nodeStatusUpdating, setNodeStatusUpdating] = useState(false);
  const [nodeStatusNotice, setNodeStatusNotice] = useState("");
  const [expansion, setExpansion] = useState<GraphRetrievalHit[]>([]);
  const [expanding, setExpanding] = useState(false);
  const [expansionNotice, setExpansionNotice] = useState("");

  const load = async () => {
    setLoading(true); setError("");
    try {
      const [rawSnapshot, nextSources] = await Promise.all([
        getGraphSnapshot(), listGraphSources(),
      ]);
      const nextSnapshot: Snapshot = {
        nodes:rawSnapshot.nodes ?? [], edges:rawSnapshot.edges ?? [], layouts:rawSnapshot.layouts ?? [],
      };
      setSnapshot(nextSnapshot); setSources(nextSources);
      setSelectedNodeIds(current => current.filter(id => nextSnapshot.nodes.some(node => node.id === id)));
      setSelectedEdgeId(current => nextSnapshot.edges.some(edge => edge.id === current) ? current : "");
      setEdgeSourceId(current => nextSnapshot.nodes.some(node => node.id === current) ? current : "");
      setEdgeTargetId(current => nextSnapshot.nodes.some(node => node.id === current) ? current : "");
      setEdgeSourceVersionId(current => nextSources.some(source => source.id === current) ? current : "");
    } catch (requestError) { setError(apiErrorMessage(requestError, "知識グラフを読み込めませんでした")); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (!edgeSourceVersionId) { setEdgeSpans([]); setEdgeSpanIds([]); return; }
    const controller = new AbortController();
    setEdgeSpansLoading(true); setEdgeSpans([]); setEdgeSpanIds([]);
    listGraphSourceSpans(edgeSourceVersionId, controller.signal)
      .then(setEdgeSpans)
      .catch(requestError => {
        if (!controller.signal.aborted) setError(apiErrorMessage(requestError, "根拠SourceSpanを取得できませんでした"));
      })
      .finally(() => { if (!controller.signal.aborted) setEdgeSpansLoading(false); });
    return () => controller.abort();
  }, [edgeSourceVersionId]);

  useEffect(() => {
    if (selectedNodeIds.length >= 2) {
      setEdgeSourceId(selectedNodeIds[selectedNodeIds.length - 2]);
      setEdgeTargetId(selectedNodeIds[selectedNodeIds.length - 1]);
    } else if (selectedNodeIds.length === 1) {
      setEdgeSourceId(selectedNodeIds[0]); setEdgeTargetId("");
    }
  }, [selectedNodeIds]);

  const canvasNodes = useMemo(() => {
    const layouts = new Map(snapshot.layouts.map(item => [item.knowledge_node_id, item]));
    return snapshot.nodes.map(node => toCanvasNode(node, layouts));
  }, [snapshot]);
  const canvasEdges = useMemo(() => snapshot.edges.map(toCanvasEdge), [snapshot.edges]);
  const selected = snapshot.nodes.find(node => node.id === selectedNodeIds.at(-1)) ?? null;
  const selectedEdge = snapshot.edges.find(edge => edge.id === selectedEdgeId) ?? null;
  const sourceById = useMemo(() => new Map(sources.map(source => [source.id, source])), [sources]);
  const selectedEvidenceSource = sourceById.get(edgeSourceVersionId);
  const expansionNodeIds = useMemo(() => expansion.map(hit => hit.node.id), [expansion]);
  const expansionEdgeIds = useMemo(() => hopEdgeIds(expansion), [expansion]);

  const expandNode = async (node: KnowledgeNode) => {
    setExpanding(true); setExpansion([]); setExpansionNotice(""); setError("");
    try {
      const hits = await retrieveGraph({
        seeds:[{ node_id:node.id, relevance:1, confidence:node.confidence ?? 1, retrieval_reason:"selected_node" }],
        top_k:16, max_degree:12, max_first_hop_candidates:16,
      });
      setExpansion(hits);
      setExpansionNotice(hits.length > 1 ? `${hits.length - 1}件の下流ノードを、最大2 hopまで根拠付きで表示しています。` : "有効・検証済みの下流関係はありません。レビュー待ちの関係は展開対象外です。");
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "ノードの関係を展開できませんでした"));
    } finally { setExpanding(false); }
  };

  const createNode = async (event: FormEvent) => {
    event.preventDefault(); if (!canWrite || !content.trim() || creating) return;
    setCreating(true); setError("");
    try {
      await createGraphNode({
        node_type:nodeType, content:content.trim(), layer:1, status:"review_pending",
        phase:"unclassified", evidence_excerpt:"", evidence_span_ids:[], metadata:{},
      });
      setContent(""); await load();
    } catch (requestError) { setError(apiErrorMessage(requestError, "ノードを作成できませんでした")); }
    finally { setCreating(false); }
  };

  const importSource = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !sourceLocator.trim() || !sourceContent.trim() || sourceImporting) return;
    if (new TextEncoder().encode(sourceContent).byteLength > MAX_SOURCE_BYTES) {
      setError("Source本文はUTF-8で5MB以下にしてください。");
      return;
    }
    setSourceImporting(true); setError(""); setSourceNotice("");
    try {
      const contentHash = await sha256Hex(sourceContent);
      const imported = await importGraphSource({
        kind: sourceKind, locator:sourceLocator.trim(), content:sourceContent, content_hash:contentHash,
      });
      if (imported.source.content_hash.toLowerCase() !== contentHash) {
        throw new Error("登録されたSourceのハッシュを検証できませんでした");
      }
      setSourceNotice(`${(imported.spans ?? []).length}件のSpanを抽出し、SHA-256を照合して登録しました。`);
      setSourceLocator(""); setSourceContent("");
      await load();
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "Sourceテキストを登録できませんでした"));
    } finally { setSourceImporting(false); }
  };

  const toggleSelectedNode = (node: GraphNode) => {
    const graphNode = snapshot.nodes.find(item => item.id === node.id);
    if (!graphNode) return;
    const isRemoving = selectedNodeIds.includes(node.id);
    if (isRemoving) {
      if (selectedNodeIds.at(-1) === node.id) { setExpansion([]); setExpansionNotice(""); }
      setSelectedNodeIds(current => current.filter(id => id !== node.id));
      return;
    }
    setNodeStatus(graphNode.status); setNodeStatusNotice("");
    void expandNode(graphNode);
    setSelectedNodeIds(current => [...current, node.id]);
  };

  const selectEdge = (edge: GraphEdge) => {
    const nextEdge = snapshot.edges.find(item => item.id === edge.id);
    if (!nextEdge) return;
    setSelectedEdgeId(nextEdge.id);
    setEdgeStatus(nextEdge.status);
    setEdgeStatusReason("");
    setEdgeStatusNotice("");
  };

  const toggleEdgeSpan = (spanId: string) => setEdgeSpanIds(current => current.includes(spanId) ? current.filter(id => id !== spanId) : [...current, spanId]);

  const createEdge = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !edgeSourceId || !edgeTargetId || edgeSourceId === edgeTargetId || !edgeSpanIds.length || edgeCreating) return;
    setEdgeCreating(true); setError(""); setEdgeNotice("");
    try {
      await createGraphEdge({
        source_node_id:edgeSourceId, target_node_id:edgeTargetId, relation:edgeRelation,
        evidence_span_ids:edgeSpanIds, evidence_excerpt:"", metadata:{},
      });
      setEdgeNotice(`${edgeSpanIds.length}件の根拠Spanを添えてエッジを作成しました。`);
      setEdgeSpanIds([]); await load();
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "根拠付きエッジを作成できませんでした"));
    } finally { setEdgeCreating(false); }
  };

  const updateEdgeStatus = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !selectedEdge || !edgeStatusReason.trim() || edgeStatusUpdating) return;
    setEdgeStatusUpdating(true); setError(""); setEdgeStatusNotice("");
    try {
      const updated = await updateGraphEdgeStatus(selectedEdge.id, {
        status: edgeStatus,
        reason: edgeStatusReason.trim(),
      });
      setSnapshot(current => ({
        ...current,
        edges: current.edges.map(edge => edge.id === updated.id ? updated : edge),
      }));
      setEdgeStatus(updated.status);
      setEdgeStatusReason("");
      setEdgeStatusNotice("エッジの状態を更新しました。");
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "知識エッジの状態を更新できませんでした"));
    } finally { setEdgeStatusUpdating(false); }
  };

  const updateNodeStatus = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !selected || nodeStatusUpdating) return;
    setNodeStatusUpdating(true); setError(""); setNodeStatusNotice("");
    try {
      const result = await updateGraphNodeStatus(selected.id, { status:nodeStatus });
      await load();
      setNodeStatus(result.node.status);
      setNodeStatusNotice(result.affected_node_ids?.length ? `ノードの状態を更新しました。関連する${result.affected_node_ids.length}件も要レビューとして更新されています。` : "ノードの状態を更新しました。");
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "知識ノードの状態を更新できませんでした"));
    } finally { setNodeStatusUpdating(false); }
  };

  return <section className="rise"><div className="mb-7 flex flex-wrap items-end justify-between gap-4"><div><p className="mb-2 text-xs font-bold uppercase tracking-[.2em] text-[#a06a28]">Grounded knowledge graph</p><h1 className="serif text-4xl font-semibold md:text-5xl">根拠とアイデアを、混ぜずに繋ぐ。</h1><p className="mt-3 max-w-3xl text-sm leading-6 text-[#68736f]">Sourceは不変の位置アンカーを保ち、仮説やアイデアはレビュー待ちとして別レイヤーに置かれます。</p></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-full border border-[#164f3b] px-4 py-2 text-xs font-semibold text-[#164f3b] disabled:opacity-40"><ArrowPathIcon className="h-4 w-4"/>更新</button></div>
    {error && <div role="alert" className="mb-5 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
    <div className="grid gap-5 xl:grid-cols-[230px_minmax(0,1fr)_285px]">
      <aside className="paper-card rounded-3xl p-5"><h2 className="serif text-xl font-semibold">Source & Context</h2><p className="mt-2 text-xs leading-5 text-[#68736f]">{sources.length}個の不変Source Version</p><div className="mt-4 max-h-64 space-y-2 overflow-y-auto">{sources.length ? sources.map(source => <button key={source.id} type="button" onClick={() => source.paper_id && onOpenPaper(source.paper_id)} disabled={!source.paper_id} className="w-full rounded-xl border border-[#deddd5] bg-white/70 p-3 text-left text-xs disabled:cursor-default"><span className="font-bold text-[#35634f]">{source.kind}</span><p className="mt-1 break-all text-[#52605b]">{source.locator}</p><p className="mt-1 font-mono text-[9px] text-[#89918e]">{source.content_hash.slice(0, 12)}…</p></button>) : <p className="text-xs leading-5 text-[#7a837f]">まだSource Versionはありません。下のフォームから原典テキストを登録できます。</p>}</div>
        <form onSubmit={importSource} className="mt-5 border-t border-[#deddd5] pt-5"><h3 className="text-sm font-semibold text-[#26342e]">Sourceテキストを登録</h3><p className="mt-1 text-[10px] leading-4 text-[#68736f]">本文からSpanを抽出し、ブラウザで算出したSHA-256と照合して不変のSourceとして保存します（UTF-8で5MBまで）。</p>{!canWrite && <p className="mt-3 rounded-lg bg-amber-50 p-2 text-[10px] leading-4 text-amber-800">viewer権限ではSourceを登録できません。</p>}<label className="mt-4 block text-[10px] font-bold text-[#52605b]" htmlFor="graph-source-kind">形式</label><select id="graph-source-kind" value={sourceKind} disabled={!canWrite || sourceImporting} onChange={event => setSourceKind(event.target.value as SourceKind)} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50">{SOURCE_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}</select><label className="mt-3 block text-[10px] font-bold text-[#52605b]" htmlFor="graph-source-locator">出所・Locator</label><input id="graph-source-locator" value={sourceLocator} disabled={!canWrite || sourceImporting} onChange={event => setSourceLocator(event.target.value)} maxLength={4000} placeholder="例: repo://model.py@abc123" className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50"/><label className="mt-3 block text-[10px] font-bold text-[#52605b]" htmlFor="graph-source-content">本文</label><textarea id="graph-source-content" value={sourceContent} disabled={!canWrite || sourceImporting} onChange={event => setSourceContent(event.target.value)} maxLength={MAX_SOURCE_BYTES} rows={6} placeholder="LaTeX、コード、Notebookのセル、CSV、対話ログ、Markdownを貼り付け" className="mt-1 w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs leading-5 disabled:opacity-50"/><button disabled={!canWrite || !sourceLocator.trim() || !sourceContent.trim() || sourceImporting} className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-[#164f3b] px-3 py-2 text-xs font-semibold text-white disabled:opacity-40"><PlusIcon className="h-3.5 w-3.5"/>{sourceImporting ? "登録・解析中…" : "Sourceを登録"}</button>{sourceNotice && <p role="status" className="mt-3 text-[10px] leading-4 text-[#35634f]">{sourceNotice}</p>}</form></aside>
      <div className="min-w-0">{loading ? <div role="status" className="grid min-h-[500px] place-items-center rounded-3xl border border-[#d8ded9] text-sm text-[#68736f]">知識グラフを読み込んでいます…</div> : <GraphCanvas nodes={canvasNodes} edges={canvasEdges} selectedNodeIds={selectedNodeIds} highlightedNodeIds={expansionNodeIds} highlightedEdgeIds={expansionEdgeIds} onNodeSelect={toggleSelectedNode} onEdgeSelect={selectEdge}/>}<p className="mt-2 text-center text-[10px] text-[#68736f]">{selectedNodeIds.length}件を選択中 · ノードを選ぶと、有効・検証済みの関係を最大2 hopまで強調表示します。複数選択すると右のフォームへsource / targetが入ります。</p></div>
          <aside className="paper-card rounded-3xl p-5">
            <h2 className="serif text-xl font-semibold">Node Inspector</h2>
            {selected ? <div className="mt-4 space-y-4"><div><p className="text-[10px] font-bold uppercase tracking-wider text-[#7a837f]">{selected.node_type} · {selected.status}</p><p className="mt-2 text-sm leading-6 text-[#26342e]">{selected.content}</p></div><dl className="space-y-2 text-xs"><div><dt className="text-[#7a837f]">Phase</dt><dd>{selected.phase}</dd></div><div><dt className="text-[#7a837f]">Confidence</dt><dd>{selected.confidence ?? "未評価"}</dd></div><div><dt className="text-[#7a837f]">Evidence anchors</dt><dd>{(selected.evidence ?? []).length ? (selected.evidence ?? []).map(item => <p key={item.source_span_id} className="mt-1 break-all font-mono text-[10px] text-[#52605b]">span:{item.source_span_id}</p>) : <span className="text-[#a06a28]">生成物・メモ（原典根拠は未接続）</span>}</dd></div></dl>
              <section className="border-t border-[#deddd5] pt-4" aria-labelledby="node-status-title"><h3 id="node-status-title" className="text-sm font-semibold text-[#26342e]">ノードの状態</h3>{!canWrite && <p className="mt-2 rounded-lg bg-amber-50 p-2 text-[10px] leading-4 text-amber-800">viewer権限ではノードの状態を変更できません。</p>}<form onSubmit={updateNodeStatus} className="mt-3 space-y-3"><label htmlFor="node-status" className="block text-[10px] font-bold text-[#52605b]">新しい状態</label><select id="node-status" value={nodeStatus} disabled={!canWrite || nodeStatusUpdating} onChange={event => setNodeStatus(event.target.value as KnowledgeNodeStatusUpdate["status"])} className="w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50">{NODE_STATUSES.map(status => <option key={status} value={status}>{status}</option>)}</select><button disabled={!canWrite || nodeStatusUpdating || nodeStatus === selected.status} className="rounded-full bg-[#164f3b] px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{nodeStatusUpdating ? "更新中…" : "ノード状態を更新"}</button>{nodeStatusNotice && <p role="status" className="text-[10px] leading-4 text-[#35634f]">{nodeStatusNotice}</p>}</form></section>
              <section className="border-t border-[#deddd5] pt-4" aria-labelledby="node-expansion-title"><div className="flex items-center justify-between gap-2"><h3 id="node-expansion-title" className="text-sm font-semibold text-[#26342e]">根拠の順伝播</h3><button type="button" onClick={() => void expandNode(selected)} disabled={expanding} className="rounded-full border border-[#164f3b] px-3 py-1.5 text-[10px] font-semibold text-[#164f3b] disabled:opacity-40">{expanding ? "展開中…" : "再展開"}</button></div><p className="mt-1 text-[10px] leading-4 text-[#68736f]">有効・検証済みの関係だけを、最大2 hopまで表示します。候補はグラフを変更しません。</p>{expansionNotice && <p role="status" className="mt-2 text-[10px] leading-4 text-[#35634f]">{expansionNotice}</p>}{expansion.length > 1 && <ol className="mt-3 space-y-2">{expansion.filter(hit => hit.node.id !== selected.id).map(hit => <li key={hit.node.id} className="rounded-lg border border-[#ead9b8] bg-[#fffaf1] p-2 text-[10px] leading-4"><p className="font-semibold text-[#26342e]">{label(hit.node.content)}</p><p className="mt-1 text-[#68736f]">{hit.hop_count} hop · {hit.retrieval_reason.replace(/^selected_node; ?/, "")} · score {hit.score.toFixed(2)}</p></li>)}</ol>}</section>
            </div> : <p className="mt-4 text-sm text-[#68736f]">ノードを選ぶと、型・状態・根拠アンカーを確認できます。</p>}
            <section className="mt-6 border-t border-[#deddd5] pt-5" aria-labelledby="edge-inspector-title">
              <h3 id="edge-inspector-title" className="text-sm font-semibold text-[#26342e]">Edge Inspector</h3>
              {selectedEdge ? <div className="mt-3 space-y-3"><dl className="grid grid-cols-2 gap-2 text-xs"><div><dt className="text-[#7a837f]">Status</dt><dd className="font-semibold text-[#26342e]">{selectedEdge.status}</dd></div><div><dt className="text-[#7a837f]">Origin</dt><dd className="font-semibold text-[#26342e]">{selectedEdge.origin}</dd></div><div className="col-span-2"><dt className="text-[#7a837f]">Relation</dt><dd className="font-semibold text-[#26342e]">{selectedEdge.relation}</dd></div></dl>
                {!canWrite && <p className="rounded-lg bg-amber-50 p-2 text-[10px] leading-4 text-amber-800">viewer権限ではエッジの状態を変更できません。</p>}
                <form onSubmit={updateEdgeStatus} className="space-y-3">
                  <div><label htmlFor="edge-status" className="block text-[10px] font-bold text-[#52605b]">新しい状態</label><select id="edge-status" value={edgeStatus} disabled={!canWrite || edgeStatusUpdating} onChange={event => setEdgeStatus(event.target.value as KnowledgeEdgeStatusUpdate["status"])} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50">{EDGE_STATUSES.map(status => <option key={status} value={status}>{status}</option>)}</select></div>
                  <div><label htmlFor="edge-status-reason" className="block text-[10px] font-bold text-[#52605b]">変更理由</label><textarea id="edge-status-reason" value={edgeStatusReason} disabled={!canWrite || edgeStatusUpdating} required onChange={event => setEdgeStatusReason(event.target.value)} maxLength={4000} rows={3} placeholder="検証結果・矛盾・置換理由を記録" className="mt-1 w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs leading-5 disabled:opacity-50"/></div>
                  <button disabled={!canWrite || !edgeStatusReason.trim() || edgeStatusUpdating} className="rounded-full bg-[#164f3b] px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{edgeStatusUpdating ? "更新中…" : "状態を更新"}</button>
                  {edgeStatusNotice && <p role="status" className="text-[10px] text-[#35634f]">{edgeStatusNotice}</p>}
                </form>
              </div> : <p className="mt-3 text-xs leading-5 text-[#68736f]">キャンバス上のエッジを選ぶと、状態と出所を確認できます。</p>}
            </section>
        <form onSubmit={createEdge} className="mt-6 border-t border-[#deddd5] pt-5"><h3 className="text-sm font-semibold text-[#26342e]">根拠付きエッジを作成</h3><p className="mt-1 text-[10px] leading-4 text-[#68736f]">source・target・relationに加え、少なくとも1つのSourceSpanが必須です。</p>{!canWrite && <p className="mt-3 rounded-lg bg-amber-50 p-2 text-[10px] leading-4 text-amber-800">viewer権限ではエッジを作成できません。</p>}<label htmlFor="edge-source" className="mt-4 block text-[10px] font-bold text-[#52605b]">Source node</label><select id="edge-source" value={edgeSourceId} disabled={!canWrite || edgeCreating} onChange={event => setEdgeSourceId(event.target.value)} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50"><option value="">選択してください</option>{snapshot.nodes.map(node => <option key={node.id} value={node.id}>{label(node.content)}</option>)}</select><label htmlFor="edge-target" className="mt-3 block text-[10px] font-bold text-[#52605b]">Target node</label><select id="edge-target" value={edgeTargetId} disabled={!canWrite || edgeCreating} onChange={event => setEdgeTargetId(event.target.value)} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50"><option value="">選択してください</option>{snapshot.nodes.map(node => <option key={node.id} value={node.id}>{label(node.content)}</option>)}</select><label htmlFor="edge-relation" className="mt-3 block text-[10px] font-bold text-[#52605b]">Relation</label><select id="edge-relation" value={edgeRelation} disabled={!canWrite || edgeCreating} onChange={event => setEdgeRelation(event.target.value as typeof edgeRelation)} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50">{EDGE_RELATIONS.map(relation => <option key={relation} value={relation}>{relation}</option>)}</select><label htmlFor="edge-source-version" className="mt-3 block text-[10px] font-bold text-[#52605b]">根拠Source</label><select id="edge-source-version" value={edgeSourceVersionId} disabled={!canWrite || edgeCreating} onChange={event => setEdgeSourceVersionId(event.target.value)} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50"><option value="">選択してください</option>{sources.map(source => <option key={source.id} value={source.id}>{source.kind}: {source.locator}</option>)}</select>{selectedEvidenceSource && <div className="mt-2 rounded-lg border border-[#d8ded9] bg-[#f8faf7] p-2 text-[9px] leading-4 text-[#52605b]"><p className="break-all">{selectedEvidenceSource.locator}</p><p className="mt-1 break-all font-mono text-[#7a837f]">SHA-256: {selectedEvidenceSource.content_hash}</p></div>}<div className="mt-3 max-h-36 space-y-2 overflow-y-auto rounded-xl border border-[#deddd5] bg-white/60 p-2" aria-label="根拠SourceSpan">{edgeSpansLoading ? <p role="status" className="p-1 text-[10px] text-[#68736f]">Spanを読み込んでいます…</p> : !edgeSourceVersionId ? <p className="p-1 text-[10px] text-[#68736f]">根拠Sourceを選択してください。</p> : edgeSpans.length ? edgeSpans.map(span => <label key={span.id} className={`block rounded-lg p-2 text-[10px] leading-4 ${canWrite && !edgeCreating ? "cursor-pointer hover:bg-[#edf5f0]" : "opacity-60"}`}><input type="checkbox" disabled={!canWrite || edgeCreating} checked={edgeSpanIds.includes(span.id)} onChange={() => toggleEdgeSpan(span.id)} className="mr-1.5 align-middle"/><span className="font-semibold text-[#35634f]">{span.page ? `p.${span.page}` : "span"}</span><span className="ml-1 text-[#52605b]">{label(span.text)}</span></label>) : <p className="p-1 text-[10px] text-amber-800">このSourceには選択可能なSpanがありません。</p>}</div>{edgeSourceId === edgeTargetId && edgeSourceId && <p className="mt-2 text-[10px] text-red-700">同じノード同士は接続できません。</p>}<button disabled={!canWrite || !edgeSourceId || !edgeTargetId || edgeSourceId === edgeTargetId || !edgeSpanIds.length || edgeCreating} className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-[#164f3b] px-3 py-2 text-xs font-semibold text-white disabled:opacity-40"><PlusIcon className="h-3.5 w-3.5"/>{edgeCreating ? "作成中…" : "根拠付きエッジを作成"}</button>{edgeNotice && <p role="status" className="mt-3 text-[10px] leading-4 text-[#35634f]">{edgeNotice}</p>}</form>
        <form onSubmit={createNode} className="mt-6 border-t border-[#deddd5] pt-5"><p className="mb-3 text-xs font-bold text-[#52605b]">アイデアを追加</p><select value={nodeType} disabled={!canWrite} onChange={event => setNodeType(event.target.value as typeof nodeType)} className="w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs disabled:opacity-50"><option value="idea">Idea</option><option value="hypothesis">Hypothesis</option><option value="constraint">Constraint</option></select><textarea value={content} disabled={!canWrite} onChange={event => setContent(event.target.value)} maxLength={100000} rows={4} placeholder={canWrite ? "レビュー対象として残す考え" : "viewer権限では追加できません"} className="mt-2 w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-xs leading-5 disabled:opacity-50"/><button disabled={!canWrite || !content.trim() || creating} className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-[#164f3b] px-3 py-2 text-xs font-semibold text-white disabled:opacity-40"><PlusIcon className="h-3.5 w-3.5"/>{creating ? "追加中…" : "review pendingで追加"}</button></form>
      </aside>
    </div>
  </section>;
}

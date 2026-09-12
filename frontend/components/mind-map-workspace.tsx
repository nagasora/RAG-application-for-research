"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { EvidenceTarget } from "@/components/evidence-viewer";
import { GenerationModelSelector } from "@/components/generation-model-selector";
import { MindMapCanvas } from "@/components/mind-map-canvas";
import {
  confirmMindMapActions,
  confirmMindMapChildren,
  createMindMap,
  createMindMapNote,
  deleteMindMap,
  deleteMindMapNode,
  expandMindMapNode,
  generateMindMap,
  generateMindMapActions,
  getMindMap,
  listGraphSourceSpans,
  listGraphSources,
  listMindMaps,
  promoteMindMapNode,
  updateMindMapNode,
  type GenerationOverride,
  type MindMap,
  type MindMapActionCandidate,
  type MindMapCandidateResponse,
  type MindMapNode,
  type MindMapNodeDraft,
  type Paper,
  type SourceSpan,
  type SourceVersion,
} from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

export type MindMapAskSeed = {
  mindMapId: string;
  nodeId: string;
  content: string;
  intent: "explore" | "challenge" | "design";
  sourcePaperIds: string[];
};

type Props = {
  papers: Paper[];
  canWrite: boolean;
  onAskFromNode: (seed: MindMapAskSeed) => void;
  onOpenEvidence: (target: EvidenceTarget) => void;
};

const NODE_KINDS: MindMapNode["kind"][] = ["theme", "claim", "question", "method", "finding", "task", "note", "link"];
const NODE_STATUSES: MindMapNode["status"][] = ["review_pending", "active", "rejected"];
const GRAPH_KINDS = ["source", "idea", "constraint", "hypothesis", "experiment"] as const;
const KIND_LABEL: Record<MindMapNode["kind"], string> = {
  root:"Root", theme:"テーマ", claim:"主張", question:"問い", method:"方法", finding:"知見", task:"タスク", note:"メモ", link:"関連",
};
const STATUS_LABEL: Record<MindMapNode["status"], string> = {
  review_pending:"要確認", active:"確認済み", rejected:"不採用",
};

function scopePaperIds(map: MindMap | null, sources: SourceVersion[]) {
  if (!map) return [];
  const scope = map.source_scope as Record<string, unknown>;
  const direct = typeof scope.paper_id === "string" ? [scope.paper_id] : [];
  // Evidence-only maps are resolved from the loaded span registry below.
  return [...new Set(direct)];
}

function mapNodes(map: MindMap | null | undefined): MindMapNode[] {
  return map?.nodes ?? [];
}

export function MindMapWorkspace({ papers, canWrite, onAskFromNode, onOpenEvidence }: Props) {
  const [maps, setMaps] = useState<MindMap[]>([]);
  const [activeMap, setActiveMap] = useState<MindMap | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [sources, setSources] = useState<SourceVersion[]>([]);
  const [spans, setSpans] = useState<Record<string, SourceSpan[]>>({});
  const [sourceMode, setSourceMode] = useState<"paper" | "evidence">("paper");
  const [paperId, setPaperId] = useState("");
  const [selectedSpanIds, setSelectedSpanIds] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [candidate, setCandidate] = useState<MindMapCandidateResponse | null>(null);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [generationOverride, setGenerationOverride] = useState<GenerationOverride>({});
  const [nodeCandidates, setNodeCandidates] = useState<MindMapCandidateResponse | null>(null);
  const [selectedChildIds, setSelectedChildIds] = useState<string[]>([]);
  const [actionRunId, setActionRunId] = useState("");
  const [actionCandidates, setActionCandidates] = useState<MindMapActionCandidate[]>([]);
  const [selectedActionIds, setSelectedActionIds] = useState<string[]>([]);
  const [editTitle, setEditTitle] = useState("");
  const [editBody, setEditBody] = useState("");
  const [editKind, setEditKind] = useState<MindMapNode["kind"]>("theme");
  const [editStatus, setEditStatus] = useState<MindMapNode["status"]>("review_pending");
  const [graphKind, setGraphKind] = useState<(typeof GRAPH_KINDS)[number]>("idea");
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const eligiblePapers = useMemo(() => papers.filter(item => item.status === "ready" && item.content_scope === "full_text"), [papers]);
  const selectedNode = mapNodes(activeMap).find(node => node.id === selectedNodeId) ?? null;
  const spanById = useMemo(() => new Map(Object.values(spans).flat().map(span => [span.id, span])), [spans]);
  const sourceById = useMemo(() => new Map(sources.map(source => [source.id, source])), [sources]);
  const paperById = useMemo(() => new Map(papers.map(paper => [paper.id, paper])), [papers]);
  const evidenceSources = useMemo(() => sources.filter(source => (spans[source.id]?.length ?? 0) > 0), [sources, spans]);

  const reloadMap = async (mindMapId: string, signal?: AbortSignal) => {
    const next = await getMindMap(mindMapId, signal);
    setActiveMap(next);
    setMaps(current => current.map(item => item.id === next.id ? next : item));
    const nodes = mapNodes(next);
    setSelectedNodeId(current => nodes.some(node => node.id === current) ? current : (nodes.find(node => node.parent_id == null)?.id ?? null));
  };

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    Promise.all([listMindMaps(controller.signal), listGraphSources(controller.signal)])
      .then(async ([nextMaps, nextSources]) => {
        if (controller.signal.aborted) return;
        setMaps(nextMaps); setSources(nextSources);
        const entries = await Promise.all(nextSources.map(async source => [source.id, await listGraphSourceSpans(source.id, controller.signal)] as const));
        if (!controller.signal.aborted) setSpans(Object.fromEntries(entries));
        if (nextMaps[0]) {
          setActiveMap(nextMaps[0]);
          setSelectedNodeId(mapNodes(nextMaps[0]).find(node => node.parent_id == null)?.id ?? null);
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted) setError(apiErrorMessage(reason, "マインドマップを読み込めませんでした"));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedNode) return;
    setEditTitle(selectedNode.title);
    setEditBody(selectedNode.body);
    setEditKind(selectedNode.kind);
    setEditStatus(selectedNode.status);
    setNodeCandidates(null); setActionCandidates([]); setActionRunId("");
  }, [selectedNode?.id, selectedNode?.updated_at]);

  const run = async (key: string, work: () => Promise<void>) => {
    if (busyKey) return;
    setBusyKey(key); setError(""); setNotice("");
    try { await work(); }
    catch (reason) { setError(apiErrorMessage(reason, "操作を完了できませんでした")); }
    finally { setBusyKey(""); }
  };

  const generate = () => run("generate", async () => {
    if (sourceMode === "paper" && !paperId) throw new Error("解析済み全文論文を1本選択してください。");
    if (sourceMode === "evidence" && !selectedSpanIds.length) throw new Error("根拠を1件以上選択してください。");
    const next = await generateMindMap({
      title:title.trim() || null,
      paper_id:sourceMode === "paper" ? paperId : null,
      source_span_ids:sourceMode === "evidence" ? selectedSpanIds : [],
      evidence_ref_ids:[],
      ...generationOverride,
    });
    setCandidate(next);
    setSelectedCandidateIds(next.nodes.map(node => node.client_id));
    setNotice("候補を生成しました。まだ保存されていません。内容を確認して確定してください。");
  });

  const saveCandidate = () => run("save-candidate", async () => {
    if (!candidate) return;
    const selected = new Set(selectedCandidateIds);
    const nodes = candidate.nodes.filter(node => selected.has(node.client_id));
    const root = candidate.nodes.find(node => node.kind === "root");
    if (root && !selected.has(root.client_id)) throw new Error("Root は保存対象から外せません。");
    const saved = await createMindMap({
      title:candidate.title,
      source_scope:candidate.source_scope,
      generation_kind:"ai",
      generation_run_id:candidate.research_run_id,
      nodes,
    });
    setMaps(current => [saved, ...current.filter(item => item.id !== saved.id)]);
    setActiveMap(saved); setSelectedNodeId(mapNodes(saved).find(node => node.parent_id == null)?.id ?? null);
    setCandidate(null); setNotice("選択した候補を保存しました。生成ノードは要確認のままです。");
  });

  const createManual = () => run("manual", async () => {
    const rootTitle = title.trim() || "新しいマインドマップ";
    const root: MindMapNodeDraft = { client_id:crypto.randomUUID(), parent_client_id:null, kind:"root", title:rootTitle, body:"", generated:false, evidence_ref_ids:[], source_span_ids:[] };
    const saved = await createMindMap({ title:rootTitle, source_scope:{ mode:"manual" }, generation_kind:"manual", generation_run_id:null, nodes:[root] });
    setMaps(current => [saved, ...current]); setActiveMap(saved); setSelectedNodeId(mapNodes(saved)[0]?.id ?? null);
    setNotice("空のマインドマップを作成しました。");
  });

  const updateNode = () => selectedNode && run(`edit:${selectedNode.id}`, async () => {
    await updateMindMapNode(selectedNode.id, { title:editTitle, body:editBody, kind:editKind, status:editStatus });
    await reloadMap(selectedNode.mind_map_id);
    setNotice("ノードを更新しました。");
  });

  const expandNode = () => selectedNode && run(`expand:${selectedNode.id}`, async () => {
    const next = await expandMindMapNode(selectedNode.id);
    setNodeCandidates(next); setSelectedChildIds(next.nodes.map(node => node.client_id));
    setNotice("子ノード候補を生成しました。選択して追加してください。");
  });

  const saveChildren = () => selectedNode && nodeCandidates && run(`children:${selectedNode.id}`, async () => {
    const chosen = nodeCandidates.nodes.filter(node => selectedChildIds.includes(node.client_id));
    await confirmMindMapChildren(selectedNode.id, chosen, nodeCandidates.research_run_id);
    await reloadMap(selectedNode.mind_map_id);
    setNodeCandidates(null); setNotice("選択した候補だけを追加しました。");
  });

  const createNote = () => selectedNode && run(`note:${selectedNode.id}`, async () => {
    const firstPaperId = scopePaperIds(activeMap, sources)[0] ?? null;
    await createMindMapNote(selectedNode.id, {
      paper_id:firstPaperId,
      title:selectedNode.title,
      content:selectedNode.body || selectedNode.title,
      origin_kind:"mind_map",
      mind_map_node_id:selectedNode.id,
      origin_snapshot:{ mind_map_id:selectedNode.mind_map_id, node_id:selectedNode.id, title:selectedNode.title, body:selectedNode.body, source_span_ids:selectedNode.source_span_ids },
    });
    setNotice("不変の元スナップショット付きでNoteを作成しました。");
  });

  const generateActions = () => selectedNode && run(`action-generate:${selectedNode.id}`, async () => {
    const result = await generateMindMapActions(selectedNode.id);
    const candidates = result.candidates ?? [];
    setActionRunId(result.research_run_id); setActionCandidates(candidates);
    setSelectedActionIds(candidates.map(item => item.client_id));
    setNotice("Action候補を生成しました。まだ保存されていません。");
  });

  const saveActions = () => selectedNode && run(`actions:${selectedNode.id}`, async () => {
    await confirmMindMapActions(selectedNode.id, actionRunId, actionCandidates.filter(item => selectedActionIds.includes(item.client_id)));
    setActionCandidates([]); setNotice("選択したActionだけを保存しました。");
  });

  const promote = () => selectedNode && run(`promote:${selectedNode.id}`, async () => {
    await promoteMindMapNode(selectedNode.id, graphKind);
    await reloadMap(selectedNode.mind_map_id);
    setNotice("根拠付き・要確認のKnowledge Nodeとして昇格しました。");
  });

  const openNodeEvidence = (spanId: string) => {
    const span = spanById.get(spanId);
    const source = span ? sourceById.get(span.source_version_id) : null;
    if (!span || !source?.paper_id) return;
    onOpenEvidence({
      paperId:source.paper_id,
      paperTitle:paperById.get(source.paper_id)?.title ?? source.paper_title ?? "論文",
      page:span.page ?? 1,
    });
  };

  const ask = (intent: MindMapAskSeed["intent"]) => {
    if (!selectedNode || !activeMap) return;
    const mapScope = activeMap.source_scope as Record<string, unknown>;
    const mapSpanIds = Array.isArray(mapScope.source_span_ids) ? mapScope.source_span_ids.filter((id): id is string => typeof id === "string") : [];
    const fromSpans = [...new Set([...(selectedNode.source_span_ids ?? []), ...mapSpanIds])].map(id => spanById.get(id)).filter((value): value is SourceSpan => Boolean(value))
      .map(span => sourceById.get(span.source_version_id)?.paper_id).filter((value): value is string => Boolean(value));
    const paperIds = [...new Set([...scopePaperIds(activeMap, sources), ...fromSpans])];
    if (!paperIds.length) { setError("Askに渡せる論文がありません。論文または原文根拠に結び付いたノードを選択してください。"); return; }
    onAskFromNode({ mindMapId:activeMap.id, nodeId:selectedNode.id, content:`${selectedNode.title}\n${selectedNode.body}`.trim(), intent, sourcePaperIds:paperIds });
  };

  return <div className="mx-auto max-w-[1700px] p-4 md:p-6">
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-[10px] font-bold uppercase tracking-[.2em] text-[#447a64]">Research artifact</p><h2 className="font-serif text-2xl font-semibold text-[#17201d]">根拠付き・編集可能なマインドマップ</h2><p className="mt-1 text-xs text-[#68736f]">AIの出力は候補です。明示的に確定するまで保存されません。</p></div>
      {!canWrite && <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-600">viewer・読取専用</span>}
    </div>
    {(error || notice) && <div role={error ? "alert" : "status"} className={`mb-4 rounded-xl border px-4 py-3 text-sm ${error ? "border-red-200 bg-red-50 text-red-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>{error || notice}</div>}
    <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)_340px]">
      <aside className="space-y-4 rounded-2xl border border-[#d8ded9] bg-white p-4">
        <div><h3 className="text-sm font-bold text-[#26342e]">保存済み map</h3><div className="mt-2 max-h-48 space-y-1 overflow-auto">{maps.length ? maps.map(map => <button type="button" key={map.id} onClick={() => { setActiveMap(map); setSelectedNodeId(mapNodes(map).find(node => node.parent_id == null)?.id ?? null); setCandidate(null); }} className={`block w-full rounded-lg px-3 py-2 text-left text-xs ${activeMap?.id === map.id ? "bg-[#e5f1e9] font-bold text-[#164f3b]" : "hover:bg-[#f3f5f3]"}`}>{map.title}<span className="ml-2 text-[9px] font-normal">{mapNodes(map).length} nodes</span></button>) : <p className="text-xs text-[#7a837f]">まだありません。</p>}</div></div>
        <div className="border-t border-[#e1e5e2] pt-4">
          <h3 className="text-sm font-bold">新規作成</h3>
          <input value={title} onChange={event => setTitle(event.target.value)} maxLength={200} placeholder="マップの題名" disabled={!canWrite || Boolean(busyKey)} className="mt-2 w-full rounded-lg border border-[#ccd4ce] px-3 py-2 text-xs"/>
          <div className="mt-2 grid grid-cols-2 gap-1 rounded-lg bg-[#eef2ef] p-1">{(["paper", "evidence"] as const).map(mode => <button type="button" key={mode} onClick={() => setSourceMode(mode)} className={`rounded-md px-2 py-1.5 text-[10px] font-bold ${sourceMode === mode ? "bg-white text-[#164f3b] shadow-sm" : "text-[#68736f]"}`}>{mode === "paper" ? "論文1本" : "選択根拠"}</button>)}</div>
          {sourceMode === "paper" ? <select value={paperId} onChange={event => setPaperId(event.target.value)} disabled={!canWrite || Boolean(busyKey)} className="mt-2 w-full rounded-lg border border-[#ccd4ce] px-2 py-2 text-xs"><option value="">解析済み全文論文を選択</option>{eligiblePapers.map(paper => <option key={paper.id} value={paper.id}>{paper.title}</option>)}</select>
            : <div className="mt-2 max-h-44 space-y-2 overflow-auto rounded-lg border border-[#d8ded9] p-2">{evidenceSources.flatMap(source => (spans[source.id] ?? []).map(span => <label key={span.id} className="flex gap-2 text-[10px] leading-4"><input type="checkbox" checked={selectedSpanIds.includes(span.id)} onChange={() => setSelectedSpanIds(current => current.includes(span.id) ? current.filter(id => id !== span.id) : [...current, span.id])}/><span>{source.paper_title ?? source.display_name ?? "Source"} · p.{span.page ?? "?"}<br/><span className="text-[#68736f]">{span.text.slice(0, 90)}</span></span></label>))}</div>}
          <div className="mt-3"><GenerationModelSelector scope="mind_map" value={generationOverride} onChange={setGenerationOverride} canOverride={canWrite && !busyKey}/></div>
          <div className="mt-3 grid grid-cols-2 gap-2"><button type="button" onClick={generate} disabled={!canWrite || Boolean(busyKey)} className="rounded-lg bg-[#164f3b] px-3 py-2 text-xs font-bold text-white disabled:opacity-50">{busyKey === "generate" ? "生成中…" : "候補を生成"}</button><button type="button" onClick={createManual} disabled={!canWrite || Boolean(busyKey)} className="rounded-lg border border-[#9db5a8] px-3 py-2 text-xs font-bold text-[#164f3b] disabled:opacity-50">手動で空map</button></div>
        </div>
        {candidate && <div className="border-t border-[#e1e5e2] pt-4"><h3 className="text-xs font-bold">未保存の生成候補</h3><div className="mt-2 max-h-44 space-y-1 overflow-auto">{candidate.nodes.map(node => <label key={node.client_id} className="flex gap-2 rounded bg-amber-50 p-2 text-[10px]"><input type="checkbox" checked={selectedCandidateIds.includes(node.client_id)} disabled={node.kind === "root"} onChange={() => setSelectedCandidateIds(current => current.includes(node.client_id) ? current.filter(id => id !== node.client_id) : [...current, node.client_id])}/><span><strong>{node.title}</strong><br/>{KIND_LABEL[node.kind]}</span></label>)}</div><div className="mt-2 grid grid-cols-2 gap-2"><button type="button" onClick={saveCandidate} disabled={!canWrite || Boolean(busyKey)} className="rounded-lg bg-amber-700 px-2 py-2 text-xs font-bold text-white">選択を確定</button><button type="button" onClick={() => setCandidate(null)} className="rounded-lg border px-2 py-2 text-xs">破棄</button></div></div>}
      </aside>

      <main className="min-w-0">
        <MindMapCanvas nodes={mapNodes(activeMap).map(node => ({ ...node, parent_id:node.parent_id ?? null }))} selectedNodeId={selectedNodeId} collapsedNodeIds={collapsed} generatingNodeId={busyKey.startsWith("expand:") ? selectedNodeId : null} onNodeSelect={node => setSelectedNodeId(node.id)} onNodeToggle={node => setCollapsed(current => current.includes(node.id) ? current.filter(id => id !== node.id) : [...current, node.id])}/>
      </main>

      <aside className="rounded-2xl border border-[#d8ded9] bg-white p-4">
        {!selectedNode ? <p className="text-sm text-[#68736f]">ノードを選択すると詳細と研究連携を表示します。</p> : <>
          <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-bold">ノード詳細</h3><span className="rounded-full bg-amber-50 px-2 py-1 text-[9px] font-bold text-amber-800">{STATUS_LABEL[selectedNode.status]}</span></div>
          <label className="mt-3 block text-[10px] font-bold">題名<input value={editTitle} onChange={event => setEditTitle(event.target.value)} maxLength={200} disabled={!canWrite} className="mt-1 w-full rounded-lg border px-3 py-2 text-xs font-normal"/></label>
          <label className="mt-2 block text-[10px] font-bold">本文<textarea value={editBody} onChange={event => setEditBody(event.target.value)} maxLength={4000} rows={5} disabled={!canWrite} className="mt-1 w-full rounded-lg border px-3 py-2 text-xs font-normal"/></label>
          <div className="mt-2 grid grid-cols-2 gap-2"><select value={editKind} onChange={event => setEditKind(event.target.value as MindMapNode["kind"])} disabled={!canWrite || selectedNode.parent_id === null} className="rounded-lg border px-2 py-2 text-xs">{selectedNode.parent_id === null && <option value="root">Root</option>}{NODE_KINDS.map(kind => <option key={kind} value={kind}>{KIND_LABEL[kind]}</option>)}</select><select value={editStatus} onChange={event => setEditStatus(event.target.value as MindMapNode["status"])} disabled={!canWrite} className="rounded-lg border px-2 py-2 text-xs">{NODE_STATUSES.map(status => <option key={status} value={status}>{STATUS_LABEL[status]}</option>)}</select></div>
          <button type="button" onClick={updateNode} disabled={!canWrite || Boolean(busyKey)} className="mt-2 w-full rounded-lg bg-[#164f3b] px-3 py-2 text-xs font-bold text-white disabled:opacity-50">編集を保存</button>
          <section className="mt-4 border-t pt-4"><h4 className="text-xs font-bold">根拠</h4>{(selectedNode.source_span_ids ?? []).length ? <div className="mt-2 space-y-1">{(selectedNode.source_span_ids ?? []).map(id => <button type="button" key={id} onClick={() => openNodeEvidence(id)} className="block w-full rounded-lg border border-[#c9ddd0] px-2 py-2 text-left text-[10px] text-[#23513e]">原文根拠を開く · {id.slice(0, 8)}</button>)}</div> : <p className="mt-1 text-[10px] text-[#7a837f]">直接リンクされた原文根拠はありません。</p>}</section>
          <section className="mt-4 border-t pt-4"><h4 className="text-xs font-bold">操作</h4><div className="mt-2 grid grid-cols-2 gap-2"><button type="button" onClick={expandNode} disabled={!canWrite || Boolean(busyKey) || selectedNode.depth >= 8} className="rounded-lg border px-2 py-2 text-[10px] font-bold">子を展開</button><button type="button" onClick={createNote} disabled={!canWrite || Boolean(busyKey)} className="rounded-lg border px-2 py-2 text-[10px] font-bold">Note</button><button type="button" onClick={generateActions} disabled={!canWrite || Boolean(busyKey)} className="rounded-lg border px-2 py-2 text-[10px] font-bold">Action候補</button><button type="button" onClick={() => ask("explore")} className="rounded-lg border px-2 py-2 text-[10px] font-bold">Ask</button></div></section>
          {nodeCandidates && <section className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3"><h4 className="text-[10px] font-bold">未保存の子候補</h4>{nodeCandidates.nodes.map(node => <label key={node.client_id} className="mt-2 flex gap-2 text-[10px]"><input type="checkbox" checked={selectedChildIds.includes(node.client_id)} onChange={() => setSelectedChildIds(current => current.includes(node.client_id) ? current.filter(id => id !== node.client_id) : [...current, node.client_id])}/><span>{node.title}</span></label>)}<div className="mt-2 flex gap-2"><button type="button" onClick={saveChildren} disabled={!selectedChildIds.length || Boolean(busyKey)} className="rounded bg-amber-700 px-2 py-1 text-[10px] font-bold text-white">選択を追加</button><button type="button" onClick={() => setNodeCandidates(null)} className="text-[10px] underline">破棄</button></div></section>}
          {actionCandidates.length > 0 && <section className="mt-3 rounded-xl border border-sky-200 bg-sky-50 p-3"><h4 className="text-[10px] font-bold">未保存のAction候補</h4>{actionCandidates.map(action => <label key={action.client_id} className="mt-2 flex gap-2 text-[10px]"><input type="checkbox" checked={selectedActionIds.includes(action.client_id)} onChange={() => setSelectedActionIds(current => current.includes(action.client_id) ? current.filter(id => id !== action.client_id) : [...current, action.client_id])}/><span><strong>{action.title}</strong><br/>{action.description}</span></label>)}<div className="mt-2 flex gap-2"><button type="button" onClick={saveActions} disabled={!selectedActionIds.length || Boolean(busyKey)} className="rounded bg-sky-800 px-2 py-1 text-[10px] font-bold text-white">選択を保存</button><button type="button" onClick={() => setActionCandidates([])} className="text-[10px] underline">破棄</button></div></section>}
          <section className="mt-4 border-t pt-4"><h4 className="text-xs font-bold">Knowledge Graphへ昇格</h4><p className="mt-1 text-[10px] text-[#68736f]">型を確認し、明示操作で要確認ノードとして作成します。自動同期はしません。</p><div className="mt-2 flex gap-2"><select value={graphKind} onChange={event => setGraphKind(event.target.value as typeof graphKind)} disabled={!canWrite || Boolean(selectedNode.knowledge_node_id)} className="min-w-0 flex-1 rounded-lg border px-2 py-2 text-[10px]">{GRAPH_KINDS.map(kind => <option key={kind}>{kind}</option>)}</select><button type="button" onClick={promote} disabled={!canWrite || Boolean(busyKey) || Boolean(selectedNode.knowledge_node_id)} className="rounded-lg bg-[#365f7a] px-3 py-2 text-[10px] font-bold text-white disabled:opacity-50">{selectedNode.knowledge_node_id ? "昇格済み" : "昇格"}</button></div></section>
          <section className="mt-4 border-t pt-4"><div className="grid grid-cols-3 gap-2">{(["explore", "challenge", "design"] as const).map(intent => <button type="button" key={intent} onClick={() => ask(intent)} className="rounded-lg border px-1 py-2 text-[9px] font-bold">{intent === "explore" ? "広げる" : intent === "challenge" ? "反証" : "実験設計"}</button>)}</div></section>
          {canWrite && <section className="mt-4 border-t border-red-100 pt-4">{selectedNode.parent_id == null ? <button type="button" onClick={() => activeMap && window.confirm("マップ全体を削除しますか？ Note・Action・昇格済みGraphNode本体は残ります。") && run("delete-map", async () => { await deleteMindMap(activeMap.id); const nextMaps = maps.filter(item => item.id !== activeMap.id); setMaps(nextMaps); setActiveMap(nextMaps[0] ?? null); setSelectedNodeId(mapNodes(nextMaps[0])[0]?.id ?? null); })} className="text-[10px] font-bold text-red-700 underline">マップ全体を削除</button> : <button type="button" onClick={() => window.confirm("このノード以下の部分木を削除しますか？ 関連Note・Action・GraphNode本体は残ります。") && run("delete-subtree", async () => { await deleteMindMapNode(selectedNode.id); await reloadMap(selectedNode.mind_map_id); })} className="text-[10px] font-bold text-red-700 underline">部分木を削除</button>}</section>}
        </>}
      </aside>
    </div>
  </div>;
}

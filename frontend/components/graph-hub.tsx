"use client";

import { useState } from "react";

import { GraphWorkspace, type GraphAskSeed } from "@/components/graph-workspace";
import { MindMapWorkspace, type MindMapAskSeed } from "@/components/mind-map-workspace";
import type { EvidenceTarget } from "@/components/evidence-viewer";
import type { Paper } from "@/lib/api/client";

export function GraphHub({ papers, canWrite, onAskFromGraph, onAskFromMindMap, onOpenPaper, onOpenEvidence }: {
  papers: Paper[];
  canWrite: boolean;
  onAskFromGraph: (seed: GraphAskSeed) => void;
  onAskFromMindMap: (seed: MindMapAskSeed) => void;
  onOpenPaper: (paperId: string) => void;
  onOpenEvidence: (target: EvidenceTarget) => void;
}) {
  const [tab, setTab] = useState<"graph" | "mind-map">("graph");
  return <section>
    <div role="tablist" aria-label="グラフ成果物" className="mb-4 inline-flex rounded-xl border border-[#cad4cd] bg-white p-1">
      <button role="tab" aria-selected={tab === "graph"} type="button" onClick={() => setTab("graph")} className={`rounded-lg px-4 py-2 text-xs font-bold ${tab === "graph" ? "bg-[#164f3b] text-white" : "text-[#52605b]"}`}>知識グラフ</button>
      <button role="tab" aria-selected={tab === "mind-map"} type="button" onClick={() => setTab("mind-map")} className={`rounded-lg px-4 py-2 text-xs font-bold ${tab === "mind-map" ? "bg-[#164f3b] text-white" : "text-[#52605b]"}`}>マインドマップ</button>
    </div>
    {tab === "graph"
      ? <GraphWorkspace papers={papers} canWrite={canWrite} onAskFromNode={onAskFromGraph} onOpenPaper={onOpenPaper}/>
      : <MindMapWorkspace papers={papers} canWrite={canWrite} onAskFromNode={onAskFromMindMap} onOpenEvidence={onOpenEvidence}/>}
  </section>;
}

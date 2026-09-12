"use client";

import { ChartBarIcon, DocumentTextIcon, LightBulbIcon, TableCellsIcon } from "@heroicons/react/24/outline";
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import type { ExperimentComparisonResponse, Gap, Paper } from "@/lib/api/client";
import { getAssetFile } from "@/lib/api/client";
import type { EvidenceTarget } from "@/components/evidence-viewer";

type Props = { experiment: ExperimentComparisonResponse; gaps: Gap[]; papers: Paper[]; openEvidence: (target: EvidenceTarget) => void };
type Tab = "table" | "results" | "figures" | "gaps";
type Locator = { page?: number; chunk_id?: string; element_id?: string; quote?: string; bbox?: number[] | null };
type Evidence = { text?: string; locator?: Locator; measurements?: Array<{ raw?: string }> };
const columns: Array<[string, string]> = [["purpose", "目的"], ["design", "デザイン"], ["datasets", "対象・データ"], ["sample_sizes", "サンプル"], ["interventions", "提案手法"], ["comparators", "比較対象"], ["metrics", "指標"], ["observations", "主要結果"]];

function evidenceList(value: unknown): Evidence[] { return Array.isArray(value) ? value.filter(item => item && typeof item === "object") as Evidence[] : []; }
function textForCell(cell?: { text?: string }) { return cell?.text || "未判定"; }
function EvidenceChips({ paper, evidence, openEvidence }: { paper: Paper | undefined; evidence: Evidence[]; openEvidence: Props["openEvidence"] }) {
  return evidence.length ? <div className="mt-2 flex flex-wrap gap-1.5">{evidence.map((item, index) => { const locator = item.locator; const page = locator?.page; if (!paper || !Number.isInteger(page) || (page ?? 0) < 1) return <span key={index} className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-800">根拠位置を確認中</span>; return <button key={index} type="button" onClick={() => openEvidence({ paperId:paper.id, paperTitle:paper.title, page:page!, chunkId:locator?.chunk_id, elementId:locator?.element_id, bbox:locator?.bbox ?? undefined, quote:locator?.quote })} className="rounded-full border border-[#9ab6a6] bg-white px-2 py-1 text-[10px] font-semibold text-[#164f3b]">p.{page}{locator?.element_id ? " · 図表" : ""} の根拠</button>; })}</div> : null;
}
function FigureThumbnail({ paper, refItem, openEvidence }: { paper: Paper | undefined; refItem: Record<string, unknown>; openEvidence: Props["openEvidence"] }) {
  const page = typeof refItem.page === "number" ? refItem.page : 1; const elementId = typeof refItem.target_element_id === "string" ? refItem.target_element_id : null;
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => { if (!paper || !elementId) return; const controller = new AbortController(); let objectUrl: string | null = null; void getAssetFile(paper.id, elementId, controller.signal).then(blob => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } }).catch(() => setUrl(null)); return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); }; }, [paper?.id, elementId]);
  if (!paper || !elementId) return null;
  return <article className="overflow-hidden rounded-2xl border border-[#d8ded9] bg-white"><button type="button" onClick={() => openEvidence({ paperId:paper.id, paperTitle:paper.title, page, elementId, quote:typeof refItem.caption === "string" ? refItem.caption : undefined })} className="block w-full text-left"><div className="grid min-h-32 place-items-center bg-[#f1f4f0]">{url ? <img src={url} alt={typeof refItem.caption === "string" ? refItem.caption : "抽出図表"} className="max-h-64 w-full object-contain"/> : <span className="text-xs text-[#68736f]">図表を読み込めません。原文ページを開く</span>}</div><div className="p-3"><p className="text-[10px] font-bold text-[#35634f]">{typeof refItem.label === "string" ? refItem.label : "図表"} · p.{page}</p><p className="mt-1 text-xs leading-5 text-[#52605b]">{typeof refItem.caption === "string" ? refItem.caption : "キャプションは抽出されていません。"}</p></div></button></article>;
}
function SamePageReference({ paper, refItem, openEvidence }: { paper: Paper | undefined; refItem: Record<string, unknown>; openEvidence: Props["openEvidence"] }) {
  if (!paper) return null;
  const page = typeof refItem.page === "number" ? refItem.page : 1;
  return <article className="rounded-2xl border border-dashed border-[#c9c8bd] bg-[#faf9f4] p-4"><p className="text-[10px] font-bold text-[#7a7568]">同一ページの参考図表 · p.{page}</p><p className="mt-2 text-xs leading-5 text-[#62645e]">結果との厳密な対応は確定していません。原ページで位置とキャプションを確認してください。</p><button type="button" onClick={() => openEvidence({ paperId:paper.id, paperTitle:paper.title, page })} className="mt-3 rounded-full border border-[#b9bbb3] bg-white px-3 py-1.5 text-[11px] font-semibold text-[#164f3b]">該当ページを表示</button></article>;
}

export function AnalysisComparisonTabs({ experiment, gaps, papers, openEvidence }: Props) {
  const [tab, setTab] = useState<Tab>("table"); const paperById = useMemo(() => new Map(papers.map(item => [item.id, item])), [papers]);
  const profiles = experiment.profiles ?? []; const matrix=experiment.matrix ?? []; const profileByPaper = new Map(profiles.map(item => [item.paper_id, item]));
  const tabs: Array<[Tab, string, typeof TableCellsIcon]> = [["table", "比較表", TableCellsIcon], ["results", "実験結果・考察", DocumentTextIcon], ["figures", "図表", ChartBarIcon], ["gaps", "研究ギャップ", LightBulbIcon]];
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: Tab) => {
    const order = tabs.map(([id]) => id);
    const currentIndex = order.indexOf(current);
    let next: Tab | undefined;
    if (event.key === "ArrowRight") next = order[(currentIndex + 1) % order.length];
    if (event.key === "ArrowLeft") next = order[(currentIndex - 1 + order.length) % order.length];
    if (event.key === "Home") next = order[0];
    if (event.key === "End") next = order[order.length - 1];
    if (!next) return;
    event.preventDefault();
    setTab(next);
    tabRefs.current[next]?.focus();
  };
  return <section className="mt-8" aria-label="実験比較の結果"><div role="tablist" aria-label="比較結果の表示切替" className="flex gap-1 overflow-x-auto rounded-2xl border border-[#d8ded9] bg-white/70 p-1">{tabs.map(([id, label, Icon]) => <button key={id} ref={node => { tabRefs.current[id] = node; }} id={`analysis-tab-${id}`} type="button" role="tab" aria-selected={tab === id} aria-controls={`analysis-panel-${id}`} tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)} onKeyDown={event => onTabKeyDown(event, id)} className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-bold ${tab === id ? "bg-[#164f3b] text-white" : "text-[#52605b]"}`}><Icon className="h-3.5 w-3.5"/>{label}</button>)}</div>
    {tab === "table" && <div id="analysis-panel-table" role="tabpanel" aria-labelledby="analysis-tab-table" tabIndex={0} className="mt-4"><div className="hidden overflow-x-auto rounded-2xl border bg-white md:block"><table className="min-w-[1450px] w-full text-left text-xs"><thead><tr className="bg-[#f1f3ee]"><th className="p-3">論文</th>{columns.map(([, label]) => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{matrix.map(row => { const paper = paperById.get(row.paper_id); const map = new Map((row.cells ?? []).map(cell => [cell.key, cell])); return <tr key={row.paper_id} className="border-t align-top"><td className="w-40 p-3 font-semibold">{paper?.title || row.paper_id}</td>{columns.map(([key]) => { const cell = map.get(key); return <td key={key} className="w-48 p-3 leading-5">{textForCell(cell)}<EvidenceChips paper={paper} evidence={evidenceList(cell?.evidence)} openEvidence={openEvidence}/></td>; })}</tr>; })}</tbody></table></div><div className="grid gap-3 md:hidden">{matrix.map(row => { const paper = paperById.get(row.paper_id); const map = new Map((row.cells ?? []).map(cell => [cell.key, cell])); return <article key={row.paper_id} className="rounded-2xl border bg-white p-4"><h3 className="font-semibold">{paper?.title || row.paper_id}</h3>{columns.map(([key, label]) => { const cell = map.get(key); return <section key={key} className="mt-3"><h4 className="text-[10px] font-bold text-[#68736f]">{label}</h4><p className="mt-1 text-xs leading-5">{textForCell(cell)}</p><EvidenceChips paper={paper} evidence={evidenceList(cell?.evidence)} openEvidence={openEvidence}/></section>; })}</article>; })}</div></div>}
    {tab === "results" && <div id="analysis-panel-results" role="tabpanel" aria-labelledby="analysis-tab-results" tabIndex={0} className="mt-4 grid gap-3 lg:grid-cols-2">{matrix.map(row => { const paper = paperById.get(row.paper_id); const profile = profileByPaper.get(row.paper_id); const observations = evidenceList(profile?.observations); const interpretations = evidenceList(profile?.author_interpretations); return <article key={row.paper_id} className="rounded-2xl border bg-white p-4"><h3 className="font-semibold">{paper?.title || row.paper_id}</h3><section className="mt-3 rounded-xl bg-[#eef7f1] p-3"><h4 className="text-xs font-bold text-[#24523e]">報告された結果</h4>{observations.length ? observations.map((item,index) => <div key={index} className="mt-2 text-xs leading-5"><p>{item.text || item.locator?.quote || "未判定"}</p>{item.measurements?.length ? <p className="mt-1 font-semibold">数値: {item.measurements.map(value => value.raw).filter(Boolean).join(" / ")}</p> : null}<EvidenceChips paper={paper} evidence={[item]} openEvidence={openEvidence}/></div>) : <p className="mt-2 text-xs">未判定</p>}</section><section className="mt-3 rounded-xl bg-[#fff3e4] p-3"><h4 className="text-xs font-bold text-[#8a571c]">著者の考察</h4>{interpretations.length ? interpretations.map((item,index) => <div key={index} className="mt-2 text-xs leading-5"><p>{item.text || item.locator?.quote || "未判定"}</p><EvidenceChips paper={paper} evidence={[item]} openEvidence={openEvidence}/></div>) : <p className="mt-2 text-xs">未判定</p>}</section></article>; })}</div>}
    {tab === "figures" && <div id="analysis-panel-figures" role="tabpanel" aria-labelledby="analysis-tab-figures" tabIndex={0} className="mt-4 grid gap-3 lg:grid-cols-2">{profiles.flatMap(profile => { const paper = paperById.get(profile.paper_id); const refs=profile.figure_table_refs ?? []; return refs.map((item,index) => item.relation === "caption_for" && item.confidence === "high" && typeof item.target_element_id === "string" ? <FigureThumbnail key={`${profile.paper_id}:${index}`} paper={paper} refItem={{...item}} openEvidence={openEvidence}/> : <SamePageReference key={`${profile.paper_id}:same-page:${index}`} paper={paper} refItem={{...item}} openEvidence={openEvidence}/>); })}{!profiles.some(profile => (profile.figure_table_refs ?? []).length) && <p className="rounded-2xl bg-white p-5 text-sm text-[#68736f]">抽出済みの図表はありません。確信を持って切り出せない場合は、図として断定せず原ページだけを案内します。</p>}</div>}
    {tab === "gaps" && <div id="analysis-panel-gaps" role="tabpanel" aria-labelledby="analysis-tab-gaps" tabIndex={0} className="mt-4 grid gap-3 lg:grid-cols-2">{gaps.map((gap,index) => <article key={`${gap.paper_id}:${index}`} className="rounded-2xl border border-[#ead7bc] bg-[#fffdf8] p-4"><p className="text-[10px] font-bold text-[#a06a28]">候補 {index + 1}</p><p className="mt-2 text-sm font-semibold">{gap.gap}</p><button type="button" onClick={() => openEvidence({ paperId:gap.paper_id, paperTitle:gap.paper_title, page:Number.parseInt(gap.page, 10) || 1 })} className="mt-3 rounded-full border px-3 py-1.5 text-[11px] font-semibold text-[#164f3b]">p.{gap.page} の原文</button></article>)}</div>}
  </section>;
}

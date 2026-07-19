"use client";

import {
  Bars3Icon, CheckCircleIcon, ChevronRightIcon, ClockIcon, CpuChipIcon,
  CircleStackIcon, DocumentTextIcon, PlusIcon, SparklesIcon, StopIcon, XMarkIcon,
} from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import type { EvidenceTarget } from "@/components/evidence-viewer";
import {
  cancelResearchRun, createIdea, createResearchConversation, createResearchRun, exportConversationGraphDrafts, getLLMStatus, getResearchConversation, importGraphSource, listGraphIdeaCandidates, listResearchConversations, previewSearch,
  type AnswerClaim, type Citation, type GraphIdeaCandidate, type LLMStatus, type Paper, type ResearchConversation, type ResearchConversationDetail, type ResearchMessage, type SearchRequest,
} from "@/lib/api/client";
import { apiErrorMessage, toApiError } from "@/lib/api/error";
import { normalizeResearchMarkdown } from "@/lib/markdown";
import { SEARCH_STAGES, streamSearch, type SearchStage, type SearchStreamMeta } from "@/lib/api/search-stream";
import { remarkCitationLinks } from "@/lib/remark-citations.mjs";

type Replay = { query: string; paperIds: string[]; revision: number; graphSeed?: { nodeId: string; content: string; intent: "explore" | "challenge" | "design" } } | null;
type Phase = "idle" | "planning" | "answering" | "syncing";
type EditorInteractionMode = Exclude<SearchRequest["interaction_mode"], "evidence">;
type ClaimClassification = "evidence_backed" | "inference" | "general_knowledge" | "hypothesis" | "unverified";
const SOURCE_STORAGE_PREFIX = "paperpilot.project-sources.";
const DRAWER_FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";

const EDITOR_INTERACTION_MODES: ReadonlyArray<{
  id: EditorInteractionMode;
  label: string;
  description: string;
  example: string;
}> = [
  { id:"synthesis", label:"統合する", description:"論文の結果・条件差・限界をつないで整理します。", example:"例: 3本の論文で一致する結果と条件差をまとめて" },
  { id:"explore", label:"発想を広げる", description:"異なるメカニズムや次の問いの候補を発散します。", example:"例: この知見から異なるメカニズムの研究仮説を3案出して" },
  { id:"challenge", label:"反証を探す", description:"競合理論と、仮説を崩しうる根拠を探します。", example:"例: この仮説が誤りだと示す競合理論と最強の反証を挙げて" },
  { id:"design", label:"検証を設計する", description:"競合する説明を区別できる実験案を組み立てます。", example:"例: 競合仮説を区別する次の実験を設計して" },
  { id:"update", label:"判断を更新する", description:"新しい根拠で、現在の仮説や判断を見直します。", example:"例: 新しい論文を踏まえて、現在の仮説を支持・反証・保留に更新して" },
];

const INTERACTION_MODE_LABEL: Record<SearchRequest["interaction_mode"], string> = {
  evidence:"根拠のみ", synthesis:"統合", explore:"発想", challenge:"反証", design:"実験設計", update:"判断更新",
};
const CLAIM_CLASSIFICATION_LABEL: Record<ClaimClassification, string> = {
  evidence_backed:"根拠あり", inference:"推論", general_knowledge:"一般知識", hypothesis:"仮説", unverified:"未検証",
};
const CLAIM_CLASSIFICATIONS: readonly ClaimClassification[] = ["evidence_backed", "inference", "general_knowledge", "hypothesis", "unverified"];

function isClaimClassification(value: unknown): value is ClaimClassification {
  return typeof value === "string" && (CLAIM_CLASSIFICATIONS as readonly string[]).includes(value);
}

function isIdeaCandidateClaim(claim: AnswerClaim) {
  return claim.classification === "hypothesis" || claim.classification === "unverified" || claim.classification === "inference";
}

function ideaKindForClaim(claim: AnswerClaim) {
  return claim.classification === "inference" ? "interpretation" as const : "hypothesis" as const;
}

function ideaClaimKey(messageId: string, claimId: string) {
  return `${messageId}:${claimId}`;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

const MEMORY_KIND_LABEL: Record<GraphIdeaCandidate["kind"], string> = {
  hypothesis:"仮説", assumption:"前提", unresolved_question:"未解決点", planned_test:"検証案",
};
const STAGE_LABELS: Record<SearchStage, string> = {
  accepted: "質問を受け付けました",
  embedding: "質問と論文をベクトル化しています",
  retrieving: "プロジェクト知識ベースから根拠を検索しています",
  planning: "検索結果を評価し、回答方針を組み立てています",
  generating: "論文根拠とLLM知識を統合しています",
  auditing: "主張と引用元を照合しています",
  saving: "回答と研究メモリを保存しています",
};

const FALLBACK_LABELS: Record<string, string> = {
  api_key_missing: "APIキーがバックエンドに反映されていません",
  dependency_missing: "Agentic RAGの依存関係が不足しています",
  no_evidence: "検索できる論文根拠がありません",
  authentication_failed: "APIキーの認証に失敗しました",
  permission_denied: "モデルの利用権限がありません",
  model_not_found: "指定モデルを利用できません",
  model_unavailable: "指定モデルを利用できません",
  rate_limited: "APIの利用上限に達しました",
  api_timeout: "LLM応答がタイムアウトしました",
  model_timeout: "LLM応答がタイムアウトしました",
  deadline_exceeded: "根拠検証が制限時間を超えました",
  network_error: "OpenAI APIへ接続できません",
  provider_unavailable: "OpenAI APIが一時的に利用できません",
  citation_validation_failed: "引用番号の検証を通過しませんでした",
  grounding_audit_failed: "根拠監査を通過しませんでした",
  verification_skipped_timeout: "追加の根拠監査が制限時間内に完了しませんでした",
  model_call_failed: "LLMの追加処理を完了できませんでした",
  repair_failed: "引用の修復を完了できませんでした",
  grounding_failed: "根拠監査を通過しませんでした",
};

function fallbackLabel(code?: string | null) {
  return code ? (FALLBACK_LABELS[code] ?? "LLM生成を完了できませんでした") : "";
}

function isGraphCitation(citation: Citation) {
  return citation.source_kind === "graph_node" || citation.source_kind === "graph_edge";
}

function isNegativeCitation(citation: Citation) {
  return citation.retrieval_stance === "negative" || citation.evidence_role === "contradicts";
}

function canOpenPaperEvidence(citation: Citation) {
  return citation.paper_id.trim().length > 0 && citation.chunk_id.trim().length > 0
    && Number.isInteger(citation.page) && citation.page >= 1;
}

function citationKey(citation: Citation) {
  return `${citation.source_kind || "paper_chunk"}:${citation.source_span_id || citation.chunk_id}:${citation.index}`;
}

function CitationBadges({ citation }: { citation: Citation }) {
  const negative = isNegativeCitation(citation);
  return <span className="inline-flex flex-wrap items-center gap-1">
    {isGraphCitation(citation) && <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[9px] font-bold text-sky-800">知識グラフ由来</span>}
    {negative && <span className="rounded-full bg-red-50 px-2 py-0.5 text-[9px] font-bold text-red-800">反証根拠</span>}
    {!negative && citation.retrieval_stance === "positive" && isGraphCitation(citation) && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-bold text-emerald-800">支持根拠</span>}
    {!negative && citation.retrieval_stance === "neutral" && isGraphCitation(citation) && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[9px] font-bold text-slate-700">中立根拠</span>}
  </span>;
}

function CitationProvenance({ citation }: { citation: Citation }) {
  if (!isGraphCitation(citation)) return null;
  const channels = citation.retrieval_channels ?? [];
  return <div className="mt-2 rounded-lg bg-[#f2f6f4] px-2.5 py-2 text-[10px] leading-4 text-[#52605b]">
    {citation.source_quote && <p><span className="font-bold">原典引用:</span> {citation.source_quote}</p>}
    <p className={citation.source_quote ? "mt-1" : ""}>
      <span className="font-bold">Provenance:</span> {citation.source_kind === "graph_edge" ? "graph edge" : "graph node"}
      {citation.retrieval_stance ? ` · stance ${citation.retrieval_stance}` : ""}
      {channels.length ? ` · ${channels.join(" / ")}` : ""}
      {citation.extraction_quality ? ` · 抽出品質 ${citation.extraction_quality}` : ""}
    </p>
    {citation.retrieval_reason && <p className="mt-1">取得理由: {citation.retrieval_reason}</p>}
  </div>;
}

function CitationCard({ citation, openEvidence, compact = false }: {
  citation: Citation; openEvidence: (target: EvidenceTarget) => void; compact?: boolean;
}) {
  const openable = canOpenPaperEvidence(citation);
  const content = <>
    <div className="flex items-start gap-2">
      <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#164f3b] text-[9px] font-bold text-white">{citation.index}</span>
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-1"><p className="min-w-0 flex-1 truncate text-xs font-semibold text-[#26342e]">{citation.paper_title}</p><CitationBadges citation={citation}/></div><p className="mt-0.5 text-[10px] font-semibold text-[#a06a28]">抽出箇所: {citation.section} · p. {citation.page}</p></div>
      {openable && <ChevronRightIcon className="mt-1 h-3.5 w-3.5 shrink-0 text-[#35634f]"/>}
    </div>
    <p className="mt-2 text-[10px] font-bold text-[#68736f]">{citation.source_quote ? "検索に使用した抜粋" : "原文抜粋"}</p>
    <p className={`mt-1 text-[11px] leading-5 text-[#52605b] ${compact ? "line-clamp-4" : "line-clamp-3"}`}>{citation.excerpt}</p>
    <CitationProvenance citation={citation}/>
    {openable && <span className="mt-2 inline-flex items-center gap-1 text-[10px] font-bold text-[#35634f]">対応する原文ページを確認<ChevronRightIcon className="h-3 w-3"/></span>}
  </>;
  const className = `block w-full rounded-xl border bg-white p-3 text-left ${isNegativeCitation(citation) ? "border-red-200" : "border-[#d8ded9]"}`;
  if (!openable) return <div className={className}>{content}</div>;
  return <button type="button" onClick={() => openEvidence({ paperId:citation.paper_id, paperTitle:citation.paper_title, page:citation.page, chunkId:citation.chunk_id })} aria-label={`引用${citation.index}: ${citation.paper_title} ${citation.page}ページの原文を確認`} className={`${className} transition hover:border-[#6f9d86] hover:bg-[#fafffb]`}>{content}</button>;
}

function AnswerWithCitations({ text, citations, openEvidence }: { text: string; citations: Citation[]; openEvidence: (target: EvidenceTarget) => void }) {
  return <div className="research-markdown min-w-0 text-[15px] leading-8">
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath, remarkCitationLinks]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "warn" }]]}
      components={{
        h1: ({ children }) => <h2 className="mb-3 mt-5 font-serif text-xl font-semibold text-[#17201d] first:mt-0">{children}</h2>,
        h2: ({ children }) => <h3 className="mb-2 mt-5 font-serif text-lg font-semibold text-[#17201d] first:mt-0">{children}</h3>,
        h3: ({ children }) => <h4 className="mb-2 mt-4 text-base font-bold text-[#24352e]">{children}</h4>,
        p: ({ children }) => <p className="my-2 leading-8 text-[#26342e]">{children}</p>,
        ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>,
        ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>,
        li: ({ children }) => <li className="pl-1 leading-7">{children}</li>,
        blockquote: ({ children }) => <blockquote className="my-3 border-l-4 border-[#9db9aa] bg-[#eef4f0] px-4 py-2 text-[#40534a]">{children}</blockquote>,
        pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-xl bg-[#10231b] p-4 text-xs leading-6 text-[#e5f0ea]">{children}</pre>,
        code: ({ children, className }) => className
          ? <code className={className}>{children}</code>
          : <code className="rounded bg-[#e7ebe7] px-1.5 py-0.5 font-mono text-[.9em] text-[#234538]">{children}</code>,
        table: ({ children }) => <div className="my-4 overflow-x-auto"><table className="min-w-full border-collapse text-sm">{children}</table></div>,
        th: ({ children }) => <th className="border border-[#ccd5cf] bg-[#eaf0ec] px-3 py-2 text-left font-bold">{children}</th>,
        td: ({ children }) => <td className="border border-[#d8ddd9] px-3 py-2 align-top">{children}</td>,
        img: ({ alt }) => <span role="img" aria-label={alt || "外部画像"} className="inline-flex rounded bg-[#f1eee8] px-2 py-1 text-xs text-[#6d6459]">画像は安全のため表示していません{alt ? `: ${alt}` : ""}</span>,
        a: ({ href, children }) => {
          const citationMatch = href?.match(/^#paperpilot-citation-(\d+)$/);
          const citation = citationMatch
            ? citations.find(item => item.index === Number(citationMatch[1]))
            : undefined;
          if (citation) {
            const graph = isGraphCitation(citation); const contradictory = isNegativeCitation(citation);
            const className = `mx-0.5 inline-flex items-center gap-1 rounded px-1.5 py-0.5 align-baseline text-xs font-bold focus-visible:outline focus-visible:outline-2 ${contradictory ? "bg-red-100 text-red-800 focus-visible:outline-red-700" : "bg-[#dfeee6] text-[#164f3b] focus-visible:outline-[#164f3b]"}`;
            const label = <>{children}{graph && <span className="text-[8px] uppercase">graph</span>}{contradictory && <span className="rounded bg-white/70 px-1 text-[8px]">反証</span>}</>;
            if (!canOpenPaperEvidence(citation)) return <span className={className} title="対応する原文ページがないグラフ根拠です">{label}</span>;
            return <button type="button" onClick={() => openEvidence({ paperId:citation.paper_id, paperTitle:citation.paper_title, page:citation.page, chunkId:citation.chunk_id })} aria-label={`引用${citation.index}: ${citation.paper_title} ${citation.page}ページを開く`} className={`${className} hover:brightness-95`} title={`${citation.paper_title} p.${citation.page}`}>{label}</button>;
          }
          return <a href={href} target="_blank" rel="noreferrer noopener" className="font-semibold text-[#176143] underline decoration-[#8cb9a4] underline-offset-4">{children}</a>;
        },
      }}
    >{normalizeResearchMarkdown(text)}</ReactMarkdown>
  </div>;
}

function AnswerClassificationBadges({ interactionMode, draft, claims }: {
  interactionMode?: SearchRequest["interaction_mode"] | null; draft?: boolean | null; claims?: AnswerClaim[];
}) {
  const counts = CLAIM_CLASSIFICATIONS.map(classification => ({
    classification,
    count: claims?.filter(claim => isClaimClassification(claim.classification) && claim.classification === classification).length ?? 0,
  })).filter(item => item.count > 0);
  const summary = counts.map(item => `${CLAIM_CLASSIFICATION_LABEL[item.classification]} ${item.count}件`).join("、");
  if (!interactionMode && !draft && !counts.length) return null;
  return <div className="mb-3 flex flex-wrap items-center gap-1.5" aria-label={`回答の主張区分${summary ? `: ${summary}` : ""}`}>
    {interactionMode && <span className="rounded-full bg-[#e7f0eb] px-2 py-1 text-[10px] font-bold text-[#35634f]">{INTERACTION_MODE_LABEL[interactionMode]}</span>}
    {draft && <span className="rounded-full bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-950">下書き・要確認</span>}
    {counts.map(item => <span key={item.classification} className="rounded-full bg-[#eef1ef] px-2 py-1 text-[10px] font-semibold text-[#52605b]">{CLAIM_CLASSIFICATION_LABEL[item.classification]} {item.count}件</span>)}
    {draft && <p className="basis-full text-[10px] leading-4 text-amber-900">提案は未検証です。原文・引用を確認し、人間が採否を判断してください。</p>}
  </div>;
}

function AssistantResponse({ text, citations, openEvidence, interactionMode, draft, claims, loading = false }: {
  text: string; citations: Citation[]; openEvidence: (target: EvidenceTarget) => void;
  interactionMode?: SearchRequest["interaction_mode"] | null; draft?: boolean | null; claims?: AnswerClaim[]; loading?: boolean;
}) {
  return <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-3"><div className="grid h-8 w-8 place-items-center rounded-full bg-[#164f3b] text-white"><SparklesIcon className={`h-4 w-4 ${loading ? "animate-pulse" : ""}`}/></div><div className="min-w-0"><AnswerClassificationBadges interactionMode={interactionMode} draft={draft} claims={claims}/>{text ? <AnswerWithCitations text={text} citations={citations} openEvidence={openEvidence}/> : <div className="space-y-3 pt-2"><div className="h-3 w-10/12 animate-pulse rounded bg-[#dfe3dd]"/><div className="h-3 w-full animate-pulse rounded bg-[#dfe3dd]"/><div className="h-3 w-7/12 animate-pulse rounded bg-[#dfe3dd]"/></div>}</div></div>;
}

function IdeaInboxActions({ message, canWrite, saveStates, saveErrors, saveClaim }: {
  message: ResearchMessage; canWrite: boolean; saveStates: Record<string, "saving" | "saved" | "error">; saveErrors: Record<string, string>;
  saveClaim: (message: ResearchMessage, claim: AnswerClaim) => Promise<void>;
}) {
  if (!canWrite || !message.research_run_id) return null;
  const candidates = (message.claims ?? []).filter(isIdeaCandidateClaim);
  if (!candidates.length) return null;
  return <section className="ml-11 mt-3 rounded-2xl border border-[#d9e5dd] bg-[#f7faf7] p-3" aria-label="Idea Inbox候補">
    <p className="text-[11px] font-bold text-[#35634f]">未検証の主張を Idea Inbox へ</p>
    <p className="mt-1 text-[10px] leading-4 text-[#68736f]">候補は未検証のまま保存されます。根拠・反証・検証方法を確認してから昇格してください。</p>
    <div className="mt-2 space-y-2">{candidates.map(claim => {
      const key = ideaClaimKey(message.id, claim.claim_id);
      const state = saveStates[key];
      return <div key={claim.claim_id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-white px-3 py-2"><p className="min-w-0 flex-1 text-[11px] leading-5 text-[#40534a]">{claim.text}</p><div className="shrink-0"><button type="button" disabled={state === "saving" || state === "saved"} onClick={() => void saveClaim(message, claim)} className="rounded-full border border-[#9ab7a7] px-3 py-1.5 text-[10px] font-bold text-[#24523e] hover:bg-[#edf5f0] disabled:cursor-not-allowed disabled:opacity-55">{state === "saving" ? "保存中…" : state === "saved" ? "Inboxへ保存済み" : "Idea Inboxへ"}</button>{state === "error" && <p role="alert" className="mt-1 max-w-44 text-[10px] leading-4 text-red-700">{saveErrors[key] || "保存に失敗しました。再試行できます。"}</p>}{state === "saved" && <p role="status" className="mt-1 text-[10px] text-[#35634f]">Idea Inboxへ保存しました。</p>}</div></div>;
    })}</div>
  </section>;
}

function relativeDate(value: string) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const elapsed = Date.now() - timestamp;
  if (elapsed < 60_000) return "たった今";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}分前`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}時間前`;
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)}日前`;
  return new Date(value).toLocaleDateString("ja-JP", { month:"short", day:"numeric" });
}

function SearchProgress({ label, stage, stageIndex }: { label: string; stage: SearchStage | null; stageIndex: number }) {
  if (!label) return null;
  return <div role="status" aria-live="polite" className="border-b border-[#cfe0d7] bg-[#edf6f1] px-4 py-2 md:px-6"><div className="flex items-center justify-between gap-3"><span className="flex min-w-0 items-center gap-2 truncate text-[11px] font-semibold text-[#23513e]"><span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-emerald-500"/><span className="truncate">{label}</span></span>{stage && <span className="shrink-0 text-[10px] font-bold tabular-nums text-[#688277]">{stageIndex + 1} / {SEARCH_STAGES.length}</span>}</div>{stage && <div className="mt-1.5 grid grid-cols-7 gap-1" aria-hidden="true">{SEARCH_STAGES.map((item, index) => <span key={item} className={`h-1 rounded-full transition-colors ${index <= stageIndex ? "bg-[#3d8062]" : "bg-[#cbdad2]"}`}/>)}</div>}</div>;
}

function CitationEvidencePanel({ evidence, grounded, openEvidence }: {
  evidence: Citation[]; grounded: boolean; openEvidence: (target: EvidenceTarget) => void;
}) {
  return <aside className="hidden min-h-0 overflow-y-auto border-l border-[#deddd5] bg-[#f3f3ef] p-4 xl:block" aria-label="最新回答の根拠"><div className="mb-4 flex items-center justify-between"><h2 className="text-xs font-bold uppercase tracking-[.14em] text-[#52605b]">Evidence</h2><span className="rounded-full bg-white px-2 py-1 text-[10px] text-[#7a837f]">{evidence.length}件</span></div><div className="space-y-3">{evidence.map(citation => <CitationCard key={citationKey(citation)} citation={citation} openEvidence={openEvidence} compact/>)}{!evidence.length && <div className="rounded-2xl border border-dashed border-[#ccd1cc] p-5 text-center"><DocumentTextIcon className="mx-auto h-5 w-5 text-[#89918e]"/><p className="mt-2 text-xs leading-5 text-[#7a837f]">回答に引用が付くと、根拠の出所と原文がここに表示されます。</p></div>}</div>{grounded && <div className="mt-4 flex items-center gap-2 rounded-xl bg-[#e1eee7] p-3 text-xs text-[#23513e]"><CheckCircleIcon className="h-4 w-4"/>根拠参照の整合性を検証済み</div>}</aside>;
}

function AnswerEvidenceList({ citations, openEvidence }: { citations: Citation[]; openEvidence: (target: EvidenceTarget) => void }) {
  return <section className="ml-11 mt-4 rounded-2xl border border-[#d8ded9] bg-[#f7faf7] p-3" aria-label="この回答でRAGが使用した根拠">
    <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2 text-xs font-bold text-[#35634f]"><DocumentTextIcon className="h-4 w-4"/>この回答でRAGが使用した根拠</div><span className="shrink-0 rounded-full bg-white px-2 py-1 text-[10px] font-semibold text-[#68736f]">{citations.length}件</span></div>
    {citations.length ? <div className="mt-3 grid gap-2 lg:grid-cols-2">{citations.map(citation => <CitationCard key={citationKey(citation)} citation={citation} openEvidence={openEvidence}/>)}</div> : <div className="mt-3 rounded-xl border border-dashed border-[#cbd3cc] bg-white/70 p-3 text-xs leading-5 text-[#68736f]">この回答には、RAGが使用した根拠はありません。一般知識またはローカル回答として扱い、原典の根拠にはしないでください。</div>}
  </section>;
}

export function AskWorkspace({ workspaceId, papers, selected, setSelected, openEvidence, replay, onReplayConsumed, canWrite }: {
  workspaceId: string;
  papers: Paper[]; selected: string[]; setSelected: (ids: string[]) => void;
  openEvidence: (target: EvidenceTarget) => void; replay: Replay; onReplayConsumed?: () => void; canWrite: boolean;
}) {
  const readyPapers = useMemo(() => papers.filter(paper => paper.status === "ready"), [papers]);
  const readyIds = useMemo(() => new Set(readyPapers.map(paper => paper.id)), [readyPapers]);
  const [query, setQuery] = useState("");
  const [graphSeed, setGraphSeed] = useState<NonNullable<Replay>["graphSeed"] | null>(null);
  const [interactionMode, setInteractionMode] = useState<EditorInteractionMode>("synthesis");
  const [conversations, setConversations] = useState<ResearchConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const [detail, setDetail] = useState<ResearchConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [liveQuestion, setLiveQuestion] = useState("");
  const [liveAnswer, setLiveAnswer] = useState("");
  const [liveCitations, setLiveCitations] = useState<Citation[]>([]);
  const [lastMeta, setLastMeta] = useState<SearchStreamMeta | null>(null);
  const [llmStatus, setLLMStatus] = useState<LLMStatus | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [searchStage, setSearchStage] = useState<SearchStage | null>(null);
  const [interruptionSyncing, setInterruptionSyncing] = useState(false);
  const [error, setError] = useState("");
  const [syncNotice, setSyncNotice] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [graphMessage, setGraphMessage] = useState<ResearchMessage | null>(null);
  const [graphCandidates, setGraphCandidates] = useState<GraphIdeaCandidate[]>([]);
  const [selectedGraphCandidateIds, setSelectedGraphCandidateIds] = useState<string[]>([]);
  const [graphDraft, setGraphDraft] = useState("");
  // This is a writing aid only. Manually authored text is always exported as
  // `manual` and classified as unverified by the server.
  const [graphDraftKind, setGraphDraftKind] = useState<"idea" | "hypothesis" | "constraint" | "experiment">("idea");
  const [graphCandidatesLoading, setGraphCandidatesLoading] = useState(false);
  const [graphSaving, setGraphSaving] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [graphNotice, setGraphNotice] = useState("");
  const [ideaSaveStates, setIdeaSaveStates] = useState<Record<string, "saving" | "saved" | "error">>({});
  const [ideaSaveErrors, setIdeaSaveErrors] = useState<Record<string, string>>({});
  const sourceSelectionRestoredRef = useRef(false);
  const streamAbortRef = useRef<AbortController | null>(null);
  const interruptionAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const historyButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const graphExportingRef = useRef(false);
  const graphSavedMemoryRef = useRef(new Set<string>());
  const busy = phase !== "idle" || interruptionSyncing || detailLoading;

  const replaceQuery = (nextQuery: string) => {
    setQuery(nextQuery);
    setGraphSeed(null);
  };

  const selectConversation = (conversationId: string) => {
    if (busy || conversationId === activeIdRef.current) return;
    activeIdRef.current = conversationId;
    setActiveId(conversationId);
    setLastMeta(null);
    setGraphSeed(null);
    setLiveQuestion(""); setLiveAnswer(""); setLiveCitations([]);
    setHistoryOpen(false); setError(""); setSyncNotice("");
  };

  const refreshList = async (preferred?: string, signal?: AbortSignal) => {
    const items = await listResearchConversations(signal);
    if (signal?.aborted) return;
    setConversations(items);
    const nextId = preferred ?? activeIdRef.current ?? items[0]?.id ?? null;
    if (nextId !== activeIdRef.current) {
      activeIdRef.current = nextId;
      setActiveId(nextId);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    refreshList(undefined, controller.signal).catch(requestError => {
      const normalized = toApiError(requestError, "研究対話を読み込めませんでした");
      if (normalized.code !== "aborted") setError(normalized.message);
    });
    return () => { controller.abort(); detailAbortRef.current?.abort(); streamAbortRef.current?.abort(); interruptionAbortRef.current?.abort(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getLLMStatus(controller.signal).then(setLLMStatus).catch(() => setLLMStatus(null));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    detailAbortRef.current?.abort();
    if (!activeId) { setDetail(null); setDetailLoading(false); return; }
    const requestedId = activeId;
    const controller = new AbortController(); detailAbortRef.current = controller;
    setDetail(null); setDetailLoading(true); setError(""); setLastMeta(null);
    getResearchConversation(requestedId, controller.signal)
      .then(nextDetail => {
        if (!controller.signal.aborted && activeIdRef.current === requestedId) setDetail(nextDetail);
      })
      .catch(requestError => {
        const normalized = toApiError(requestError, "研究対話を開けませんでした");
        if (!controller.signal.aborted && normalized.code !== "aborted") setError(normalized.message);
      })
      .finally(() => { if (!controller.signal.aborted && activeIdRef.current === requestedId) setDetailLoading(false); });
    return () => controller.abort();
  }, [activeId]);

  useEffect(() => {
    if (sourceSelectionRestoredRef.current || papers.length === 0) return;
    let restored: string[] = replay ? replay.paperIds.filter(id => readyIds.has(id)) : [];
    if (!replay) try {
      const stored: unknown = JSON.parse(window.localStorage.getItem(`${SOURCE_STORAGE_PREFIX}${workspaceId}`) ?? "[]");
      if (Array.isArray(stored)) restored = stored.filter((id): id is string => typeof id === "string" && readyIds.has(id));
    } catch { /* malformed or unavailable browser storage falls back to all ready papers */ }
    sourceSelectionRestoredRef.current = true;
    setSelected(restored);
  }, [papers.length, readyIds, replay, setSelected, workspaceId]);
  useEffect(() => {
    if (!sourceSelectionRestoredRef.current) return;
    const normalized = selected.filter(id => readyIds.has(id));
    if (normalized.length !== selected.length) { setSelected(normalized); return; }
    try { window.localStorage.setItem(`${SOURCE_STORAGE_PREFIX}${workspaceId}`, JSON.stringify(normalized)); } catch { /* no-op */ }
  }, [readyIds, selected, setSelected, workspaceId]);

  useEffect(() => {
    if (!historyOpen) return;
    const previousOverflow = document.body.style.overflow;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => drawerRef.current?.querySelector<HTMLElement>(DRAWER_FOCUSABLE)?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setHistoryOpen(false); return; }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(drawerRef.current.querySelectorAll<HTMLElement>(DRAWER_FOCUSABLE));
      if (!focusable.length) { event.preventDefault(); return; }
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      (previouslyFocused ?? historyButtonRef.current)?.focus();
    };
  }, [historyOpen]);
  useEffect(() => { if (replay) { setQuery(replay.query); setGraphSeed(replay.graphSeed ?? null); if (replay.graphSeed) setInteractionMode(replay.graphSeed.intent); onReplayConsumed?.(); } }, [onReplayConsumed, replay?.revision]);
  useEffect(() => { messageEndRef.current?.scrollIntoView({ block:"end", behavior:"smooth" }); }, [detail?.messages?.length, liveAnswer, liveQuestion]);

  const startNew = () => {
    if (!canWrite || busy) return;
    // A blank draft must not become a persisted conversation.  The first
    // submitted question below creates it, allowing the server to derive a
    // useful title from the actual research topic.
    detailAbortRef.current?.abort();
    activeIdRef.current = null;
    setActiveId(null); setDetail(null); setQuery(""); setGraphSeed(null); setLastMeta(null);
    setLiveQuestion(""); setLiveAnswer(""); setLiveCitations([]);
    setError(""); setSyncNotice(""); setHistoryOpen(false);
  };

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const prompt = query.trim();
    if (busy) return;
    if (Array.from(prompt).length < 2) { setError("質問は2文字以上で入力してください。"); return; }
    if (!canWrite) {
      setError(""); setSyncNotice(""); setLiveQuestion(prompt); setLiveAnswer(""); setLiveCitations([]);
      setPhase("planning");
      try {
        const result = await previewSearch({ query:prompt, paper_ids:selected, limit:10, interaction_mode:"evidence" });
        setLiveCitations(result.citations);
        setSyncNotice(result.citations.length ? "原文根拠を表示しています。viewer権限ではLLM回答は生成されません。" : "一致する原文根拠は見つかりませんでした。");
        setQuery("");
      } catch (requestError) {
        setError(apiErrorMessage(requestError, "論文内を検索できませんでした"));
      } finally {
        setPhase("idle");
      }
      return;
    }
    detailAbortRef.current?.abort(); setDetailLoading(false);
    streamAbortRef.current?.abort(); setPhase("planning"); setSearchStage("accepted"); setError(""); setSyncNotice("");
    setLiveQuestion(prompt); setLiveAnswer(""); setLiveCitations([]); setLastMeta(null);
    const controller = new AbortController(); streamAbortRef.current = controller;
    let conversationId = activeIdRef.current;
    let researchRunId: string | null = null;
    let streamCompleted = false;
    let streamCancelled = false;
    const runGraphSeed = graphSeed;
    try {
      const researchRun = await createResearchRun({
        source_paper_ids:selected.length ? selected : readyPapers.map(paper => paper.id),
        purpose:prompt,
        success_criteria:"質問に対する根拠付き回答を記録する",
        plan:{ origin:"ask_workspace", interaction_mode:interactionMode, ...(runGraphSeed ? { graph_seed:{ node_id:runGraphSeed.nodeId, content:runGraphSeed.content, intent:runGraphSeed.intent } } : {}) },
        model:llmStatus?.model ?? "",
        prompt_version:"ask-workspace-v1",
      }, controller.signal);
      researchRunId = researchRun.id;
      if (!conversationId) {
        try {
          const created = await createResearchConversation(prompt.slice(0, 80), controller.signal);
          conversationId = created.id; activeIdRef.current = created.id; setActiveId(created.id);
          setConversations(current => [created, ...current]);
        } catch (conversationError) {
          void cancelResearchRun(researchRun.id).catch(() => { /* best-effort cleanup preserves the conversation error */ });
          throw conversationError;
        }
      }
      for await (const streamEvent of streamSearch({ query:prompt, paper_ids:selected, limit:10, conversation_id:conversationId, research_run_id:researchRun.id, interaction_mode:interactionMode }, controller.signal)) {
        if (streamEvent.type === "token") { setPhase("answering"); setLiveAnswer(current => current + streamEvent.value); }
        if (streamEvent.type === "citations") setLiveCitations(streamEvent.value);
        if (streamEvent.type === "stage") setSearchStage(streamEvent.value);
        if (streamEvent.type === "meta") setLastMeta(streamEvent.value);
        if (streamEvent.type === "done") streamCompleted = true;
        if (streamEvent.type === "cancelled") { streamCompleted = true; streamCancelled = true; }
      }
      if (streamCancelled) setSyncNotice("回答表示を中断しました。保存済みの会話を同期しています。");
      setQuery(""); setGraphSeed(null); setPhase("syncing"); setSearchStage("saving");
      try {
        detailAbortRef.current?.abort();
        const refreshed = await getResearchConversation(conversationId, controller.signal);
        if (activeIdRef.current === conversationId) {
          setDetail(refreshed); setLiveQuestion(""); setLiveAnswer(""); setLiveCitations([]);
        }
        await refreshList(conversationId, controller.signal);
      } catch (syncError) {
        const normalized = toApiError(syncError, "保存済みの会話を再読み込みできませんでした");
        if (normalized.code !== "aborted") setSyncNotice("回答は完了しましたが、会話履歴との同期に失敗しました。画面を切り替えると再取得できます。");
      }
    } catch (requestError) {
      if (!streamCompleted && researchRunId) {
        void cancelResearchRun(researchRunId).catch(() => { /* best-effort cleanup preserves the original request error */ });
      }
      const normalized = toApiError(requestError, "回答を生成できませんでした");
      if (normalized.code === "aborted") {
        setSyncNotice("回答表示を中断しました。現在のAPIではサーバー側で完了した回答が履歴に保存される場合があります。");
      } else if (!streamCompleted) setError(normalized.message);
    } finally {
      if (streamAbortRef.current === controller) { streamAbortRef.current = null; setPhase("idle"); setSearchStage(null); }
    }
  };

  const stopDisplay = () => {
    const conversationId = activeIdRef.current;
    const baselineMessageCount = detail?.message_count ?? 0;
    streamAbortRef.current?.abort();
    if (!conversationId || interruptionSyncing) return;
    interruptionAbortRef.current?.abort();
    const controller = new AbortController(); interruptionAbortRef.current = controller;
    setInterruptionSyncing(true); setSyncNotice("回答表示を中断しました。サーバー処理との同期が終わるまで再送を待機します。");
    void (async () => {
      const deadline = Date.now() + 45_000;
      try {
        while (!controller.signal.aborted && Date.now() < deadline) {
          await new Promise<void>((resolve, reject) => {
            const timer = window.setTimeout(resolve, 1_500);
            controller.signal.addEventListener("abort", () => {
              window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError"));
            }, { once:true });
          });
          const refreshed = await getResearchConversation(conversationId, controller.signal);
          if (refreshed.message_count > baselineMessageCount) {
            if (activeIdRef.current === conversationId) {
              setDetail(refreshed); setLiveQuestion(""); setLiveAnswer(""); setLiveCitations([]);
            }
            await refreshList(conversationId, controller.signal);
            setSyncNotice("中断後に完了した回答を会話履歴へ同期しました。");
            return;
          }
        }
        if (!controller.signal.aborted) setSyncNotice("サーバー処理の完了を確認できませんでした。再送前に会話履歴を切り替えて確認してください。");
      } catch (syncError) {
        const normalized = toApiError(syncError, "中断後の会話を同期できませんでした");
        if (normalized.code !== "aborted") setSyncNotice("中断後の会話を同期できませんでした。再送前に会話履歴を確認してください。");
      } finally {
        if (interruptionAbortRef.current === controller) {
          interruptionAbortRef.current = null; setInterruptionSyncing(false);
        }
      }
    })();
  };

  const openGraphCandidates = async (message: ResearchMessage) => {
    if (!canWrite || !activeIdRef.current || graphCandidatesLoading || graphSaving) return;
    setGraphMessage(message); setGraphCandidates([]); setSelectedGraphCandidateIds([]); setGraphDraft(""); setGraphError(""); setGraphNotice(""); setGraphCandidatesLoading(true);
    const controller = new AbortController();
    try {
      const candidates = await listGraphIdeaCandidates(activeIdRef.current, message.id, controller.signal);
      setGraphCandidates(candidates);
      setSelectedGraphCandidateIds(candidates.filter(item => !graphSavedMemoryRef.current.has(item.id)).map(item => item.id));
    } catch (requestError) {
      setGraphError(apiErrorMessage(requestError, "研究メモリ候補を取得できませんでした"));
    } finally { setGraphCandidatesLoading(false); }
  };

  const closeGraphCandidates = () => {
    if (graphSaving) return;
    setGraphMessage(null); setGraphCandidates([]); setSelectedGraphCandidateIds([]); setGraphDraft(""); setGraphError(""); setGraphNotice("");
  };

  const saveClaimToIdeaInbox = async (message: ResearchMessage, claim: AnswerClaim) => {
    const researchRunId = message.research_run_id;
    const key = ideaClaimKey(message.id, claim.claim_id);
    if (!canWrite || !researchRunId || !isIdeaCandidateClaim(claim) || ideaSaveStates[key] === "saving" || ideaSaveStates[key] === "saved") return;
    setIdeaSaveStates(current => ({ ...current, [key]:"saving" }));
    setIdeaSaveErrors(current => { const { [key]: _removed, ...next } = current; return next; });
    try {
      const created = await createIdea({
        kind:ideaKindForClaim(claim), content:claim.text, research_run_id:researchRunId, claim_id:claim.claim_id,
        checklist:{ evidence:false, falsifier:false, test:false, captured_from:"ask_workspace" },
      });
      setIdeaSaveStates(current => ({ ...current, [key]:"saved" }));
      window.dispatchEvent(new CustomEvent("paperpilot:idea-created", { detail:{ ideaId:created.id } }));
    } catch (requestError) {
      setIdeaSaveStates(current => ({ ...current, [key]:"error" }));
      setIdeaSaveErrors(current => ({ ...current, [key]:apiErrorMessage(requestError, "アイデアを保存できませんでした") }));
    }
  };

  const saveGraphCandidates = async () => {
    const conversationId = activeIdRef.current;
    if (!canWrite || !graphMessage || !conversationId || graphSaving || graphExportingRef.current) return;
    const candidates = graphCandidates.filter(item => selectedGraphCandidateIds.includes(item.id) && !graphSavedMemoryRef.current.has(item.id));
    const draft = graphDraft.trim();
    if (!candidates.length && !draft) return;
    graphExportingRef.current = true; setGraphSaving(true); setGraphError(""); setGraphNotice("");
    try {
      // Preserve the assistant turn as an immutable chat Source. It is provenance for
      // an idea, never a substitute for the cited paper evidence in that turn.
      const sourceContent = JSON.stringify([{ role:"assistant", content:graphMessage.content, timestamp:graphMessage.created_at }]);
      const imported = await importGraphSource({
        kind:"chat", locator:`chat://conversation/${conversationId}/message/${graphMessage.id}`,
        content:sourceContent, content_hash:await sha256Hex(sourceContent),
        metadata:{ conversation_id:conversationId, message_id:graphMessage.id, source:"research_conversation" },
      });
      const evidenceSpan = imported.spans?.[0];
      if (!evidenceSpan) throw new Error("会話の根拠Spanを作成できませんでした");
      const drafts = [
        ...candidates.map(candidate => ({ candidate_id:candidate.id, content:candidate.content, kind:candidate.kind, derived_from_memory:candidate.derived_from_memory })),
        ...(draft ? [{ candidate_id:`manual:${await sha256Hex(draft)}`, content:draft, kind:"manual" as const, derived_from_memory:false }] : []),
      ];
      await exportConversationGraphDrafts(conversationId, graphMessage.id, { source_span_id:evidenceSpan.id, drafts });
      candidates.forEach(candidate => graphSavedMemoryRef.current.add(candidate.id));
      setSelectedGraphCandidateIds([]);
      setGraphDraft("");
      setGraphNotice(`${candidates.length + (draft ? 1 : 0)}件をレビュー待ちとして知識グラフへ保存しました。会話由来の未検証メモであり、論文事実としては扱われません。`);
    } catch (requestError) {
      setGraphError(apiErrorMessage(requestError, "知識グラフへ保存できませんでした"));
    } finally { graphExportingRef.current = false; setGraphSaving(false); }
  };

  const toggleGraphCandidate = (id: string) => setSelectedGraphCandidateIds(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]);

  const messages = detail?.messages ?? [];
  const evidence = liveCitations.length
    ? liveCitations
    : messages.at(-1)?.role === "assistant" ? (messages.at(-1)?.citations ?? []) : [];
  const memoryText = detail?.summary?.trim() ?? "";
  const phaseLabel = interruptionSyncing ? "中断後のサーバー処理と会話履歴を同期しています" : searchStage ? STAGE_LABELS[searchStage] : phase === "planning" ? "質問を分析し、検索計画を立てています" : phase === "answering" ? "論文を照合しながら回答しています" : phase === "syncing" ? "会話と記憶を保存しています" : "";
  const stageIndex = searchStage ? SEARCH_STAGES.indexOf(searchStage) : -1;
  const sourceScopeLabel = selected.length ? `指定した${selected.length}件を検索` : `準備完了の全${readyPapers.length}件を検索`;
  const llmFailure = lastMeta?.fallback_reason
    ? fallbackLabel(lastMeta.fallback_reason)
    : llmStatus?.last_failure_code ? fallbackLabel(llmStatus.last_failure_code) : "";
  const llmHealthy = lastMeta
    ? lastMeta.grounded
    : Boolean(llmStatus?.configured && llmStatus.agentic_dependencies_available && !llmFailure);
  const llmLabel = lastMeta
    ? lastMeta.grounded
      ? `${lastMeta.model ?? "LLM"} · 引用検証済み`
      : `${lastMeta.llm_succeeded ? (lastMeta.fallback_reason ? "LLM使用 · 根拠監査で保留" : "LLM使用 · 引用形式チェック済み") : "ローカル回答"}${llmFailure ? ` · ${llmFailure}` : ""}`
    : llmStatus
      ? !llmStatus.configured ? "LLM未接続 · APIキー未反映"
        : !llmStatus.agentic_dependencies_available ? "LLM未接続 · 依存関係不足"
          : llmFailure ? `LLMエラー · ${llmFailure}` : `${llmStatus.model} · 接続設定済み`
      : "LLM状態を確認中";
  const fallbackNotice = lastMeta && !lastMeta.grounded && lastMeta.fallback_reason
    ? `${fallbackLabel(lastMeta.fallback_reason)}。${lastMeta.generation_mode === "local_fallback" ? "論文から抽出したローカル回答を表示しています。" : "回答は根拠検証を通過していません。"}`
    : "";

  return <section className="rise -mx-5 -my-8 min-h-[calc(100vh-5.5rem)] lg:-mx-10 lg:-my-12">
    <div className="grid min-h-[calc(100vh-5.5rem)] bg-[#f7f6f1] lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className={`${historyOpen ? "fixed inset-0 z-[90] isolate flex" : "hidden"} min-h-0 lg:static lg:flex lg:flex-col`} aria-label="研究ナビゲーション">
        {historyOpen && <button type="button" aria-label="会話履歴を閉じる" className="absolute inset-0 bg-[#07110d]/80 backdrop-blur-[3px] lg:hidden" onClick={() => setHistoryOpen(false)}/>}
        <div ref={drawerRef} role={historyOpen ? "dialog" : undefined} aria-modal={historyOpen ? true : undefined} aria-label={historyOpen ? "研究対話履歴とプロジェクト知識ベース" : undefined} className="relative z-[1] flex h-[100dvh] min-h-0 w-[88vw] max-w-[340px] flex-col overflow-hidden border-r border-white/10 bg-[#10231b] text-white shadow-[28px_0_70px_rgba(0,0,0,.42)] lg:h-[calc(100vh-5.5rem)] lg:w-auto lg:max-w-none lg:shadow-none">
          <div className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-4"><div><p className="text-[10px] font-bold uppercase tracking-[.2em] text-[#91ad9f]">Research cockpit</p><p className="mt-1 text-sm font-semibold text-white">研究ナビゲーション</p></div><button type="button" onClick={() => setHistoryOpen(false)} aria-label="研究ナビゲーションを閉じる" className="grid h-9 w-9 place-items-center rounded-full border border-white/15 text-[#d8e6df] hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white lg:hidden"><XMarkIcon className="h-5 w-5"/></button></div>
          <div className="flex min-h-0 flex-1 flex-col p-4">
            <button type="button" onClick={startNew} disabled={!canWrite || busy} className="flex w-full shrink-0 items-center justify-center gap-2 rounded-xl border border-[#78a58f]/60 bg-[#e8f3ed] px-4 py-3 text-sm font-semibold text-[#123d2d] shadow-lg shadow-black/15 disabled:opacity-40"><PlusIcon className="h-4 w-4"/>新しい研究対話</button>
            <div className="mt-5 flex shrink-0 items-center justify-between px-2"><p className="text-[10px] font-bold uppercase tracking-[.18em] text-[#91ad9f]">Conversation history</p><span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-[#b9cec3]">{conversations.length}件</span></div>
            <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto overscroll-contain pr-1 [scrollbar-color:#5f7f70_transparent]" aria-label="会話履歴の一覧" tabIndex={0}>
              {conversations.map(item => <button type="button" key={item.id} disabled={busy} onClick={() => selectConversation(item.id)} aria-current={activeId === item.id ? "page" : undefined} className={`w-full rounded-xl border px-3 py-3 text-left transition disabled:cursor-not-allowed ${activeId === item.id ? "border-[#739d89]/60 bg-[#254438] text-white shadow-lg shadow-black/15" : "border-transparent text-[#c3d2ca] hover:border-white/10 hover:bg-white/[.07]"}`}><span className="block truncate text-sm font-semibold">{item.title}</span><span className={`mt-1 flex items-center gap-1 text-[10px] ${activeId === item.id ? "text-[#a9cbbb]" : "text-[#789387]"}`}><ClockIcon className="h-3 w-3"/>{relativeDate(item.updated_at)}</span></button>)}
              {!conversations.length && <p className="px-3 py-6 text-center text-xs leading-5 text-[#91ad9f]">研究対話はまだありません。<br/>問いを送ると自動で作成されます。</p>}
            </div>
            <div className="mt-3 grid shrink-0 gap-2 border-t border-white/10 pt-3">
              <div className="rounded-xl border border-white/10 bg-white/[.06] p-3"><div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2 text-xs font-semibold text-[#cce5d9]"><CpuChipIcon className="h-4 w-4 text-[#86b39d]"/>研究メモリ</div><span className={`h-2 w-2 rounded-full ${memoryText ? "bg-emerald-400 shadow-[0_0_10px_#34d399]" : "bg-[#5f746a]"}`}/></div><p className="mt-2 line-clamp-3 text-[11px] leading-5 text-[#9eb5aa]">{memoryText || "仮説・合意・未解決点を対話から蓄積します。"}</p>{memoryText && <p className="mt-1 text-[10px] text-[#718b7f]">{memoryText.length.toLocaleString()}文字を次の対話へ引き継ぎ</p>}</div>
              <details className="group rounded-xl border border-[#527665]/70 bg-[#183229]"><summary className="cursor-pointer list-none p-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"><div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2 text-xs font-semibold text-white"><CircleStackIcon className="h-4 w-4 text-[#8bc2a7]"/>プロジェクト知識ベース</div><ChevronRightIcon className="h-3.5 w-3.5 text-[#91ad9f] transition group-open:rotate-90"/></div><p className="mt-2 text-[11px] leading-5 text-[#9eb5aa]">{sourceScopeLabel}</p><p className="mt-1 text-[10px] text-[#718b7f]">この端末では会話を切り替えても維持</p></summary><div className="max-h-36 space-y-1 overflow-y-auto border-t border-white/10 p-2 overscroll-contain">{selected.length > 0 && <button type="button" onClick={() => setSelected([])} className="w-full rounded-lg border border-[#729482]/50 px-2 py-1.5 text-left text-[10px] font-semibold text-[#b9ddcb] hover:bg-white/5">選択を解除して全ready論文を検索</button>}{readyPapers.map(paper => { const checked = selected.includes(paper.id); return <button type="button" key={paper.id} aria-pressed={checked} onClick={() => setSelected(checked ? selected.filter(id => id !== paper.id) : [...selected, paper.id])} className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[10px] ${checked ? "bg-[#315747] text-white" : "text-[#9eb5aa] hover:bg-white/5"}`}><span className={`grid h-4 w-4 shrink-0 place-items-center rounded border ${checked ? "border-[#8fc4aa] bg-[#8fc4aa] text-[#10231b]" : "border-[#607d70]"}`}>{checked && <CheckCircleIcon className="h-3 w-3"/>}</span><span className="truncate">{paper.title}</span></button>;})}{!readyPapers.length && <p className="p-2 text-[10px] leading-5 text-[#91ad9f]">解析が完了した論文はまだありません。</p>}</div></details>
            </div>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-[#deddd5] bg-[#fffefa]/95 px-4 py-3 shadow-sm backdrop-blur md:px-6">
          <div className="flex min-w-0 items-center gap-3"><button ref={historyButtonRef} type="button" onClick={() => setHistoryOpen(true)} aria-label="研究対話履歴とプロジェクト知識ベースを開く" aria-expanded={historyOpen} className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-[#cfd5d0] bg-white shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b] lg:hidden"><Bars3Icon className="h-5 w-5"/></button><div className="min-w-0"><div className="flex min-w-0 items-center gap-2"><span className={`h-2 w-2 shrink-0 rounded-full ${llmHealthy ? "bg-emerald-500" : "bg-amber-500"}`}/><p className="truncate text-sm font-semibold">{detail?.title || (detailLoading ? "会話を読み込んでいます…" : "新しい研究対話")}</p></div><p className="mt-0.5 truncate text-[10px] text-[#7a837f]">プロジェクト知識ベース · {sourceScopeLabel}</p><p className={`mt-0.5 truncate text-[10px] font-semibold md:hidden ${llmHealthy ? "text-[#35634f]" : "text-amber-800"}`}>{llmLabel}</p></div></div>
          <div data-llm-status-slot className="hidden items-center gap-2 md:flex" aria-label="プロジェクトと回答生成の状態"><span className="rounded-full bg-[#e9efeb] px-2.5 py-1 text-[10px] font-bold text-[#35634f]"><CircleStackIcon className="mr-1 inline h-3 w-3"/>{selected.length ? `${selected.length} sources` : `all ${readyPapers.length} sources`}</span><span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${memoryText ? "bg-[#e1eee7] text-[#164f3b]" : "bg-[#eceeea] text-[#68736f]"}`}>{memoryText ? "記憶を使用中" : "記憶はまだ空です"}</span><span title={llmLabel} className={`max-w-80 truncate rounded-full px-2.5 py-1 text-[10px] font-bold ${llmHealthy ? "bg-[#dfeee6] text-[#164f3b]" : "bg-amber-50 text-amber-800"}`}>{llmLabel}</span></div>
        </header>
        <SearchProgress label={phaseLabel} stage={searchStage} stageIndex={stageIndex}/>

        <div className="grid min-h-0 flex-1 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="flex min-h-0 flex-col">
            <div role="log" aria-label="研究対話" className="min-h-0 flex-1 overflow-y-auto px-4 py-7 md:px-8">
              <div className="mx-auto max-w-3xl space-y-7">
                {!messages.length && !liveQuestion && !detailLoading && <div className="grid min-h-[42vh] place-items-center text-center"><div><div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-[#e1eee7] text-[#164f3b]"><SparklesIcon className="h-6 w-6"/></div><h1 className="serif mt-5 text-3xl font-semibold">何を一緒に考えますか？</h1><p className="mx-auto mt-3 max-w-xl text-sm leading-7 text-[#68736f]">論文を横断して詳しくまとめ、LLMの知識で補足します。根拠にした原文は引用番号から直接確認できます。</p></div></div>}
                {detailLoading && <div role="status" className="py-20 text-center text-sm text-[#68736f]">会話履歴を読み込んでいます…</div>}
                {messages.map(message => <article key={message.id} aria-label={message.role === "user" ? "あなた" : "PaperPilot"} className={message.role === "user" ? "ml-auto max-w-[88%] rounded-3xl rounded-br-md bg-[#e6e8e3] px-5 py-3 text-sm leading-7 text-[#26312c]" : "max-w-full"}>{message.role === "assistant" ? <><AssistantResponse text={message.content} citations={message.citations ?? []} openEvidence={openEvidence} interactionMode={message.interaction_mode} draft={message.draft} claims={message.claims}/><IdeaInboxActions message={message} canWrite={canWrite} saveStates={ideaSaveStates} saveErrors={ideaSaveErrors} saveClaim={saveClaimToIdeaInbox}/><AnswerEvidenceList citations={message.citations ?? []} openEvidence={openEvidence}/><div className="ml-11 mt-3"><button type="button" disabled={!canWrite || busy || graphSaving} onClick={() => void openGraphCandidates(message)} className="rounded-full border border-[#9ab7a7] px-3 py-1.5 text-[11px] font-semibold text-[#24523e] hover:bg-[#edf5f0] disabled:cursor-not-allowed disabled:opacity-45">研究アイデアをグラフへ</button></div></> : <p className="whitespace-pre-wrap">{message.content}</p>}</article>)}
                {liveQuestion && <article aria-label="あなた" className="ml-auto max-w-[88%] rounded-3xl rounded-br-md bg-[#e6e8e3] px-5 py-3 text-sm leading-7 text-[#26312c]"><p className="whitespace-pre-wrap">{liveQuestion}</p></article>}
                {(liveAnswer || busy) && <article aria-label="PaperPilotの回答"><AssistantResponse text={liveAnswer} citations={liveCitations} openEvidence={openEvidence} interactionMode={lastMeta?.interaction_mode ?? interactionMode} draft={lastMeta?.draft} claims={lastMeta?.claims} loading={!liveAnswer}/></article>}
                <div ref={messageEndRef}/>
              </div>
            </div>

            <div className="shrink-0 px-3 pb-4 md:px-8 md:pb-6"><div className="mx-auto max-w-3xl">
              <div className="sr-only" role="status" aria-live="polite">{syncNotice || error}</div>
              {error && <div role="alert" className="mb-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
              {syncNotice && <div role="status" className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900">{syncNotice}</div>}
              {fallbackNotice && <div role="status" className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-medium leading-5 text-amber-950"><span className="font-bold">{lastMeta?.generation_mode === "agentic_rag" ? "根拠監査の注意:" : "LLMフォールバック:"}</span> {fallbackNotice}</div>}
              {graphSeed && <div role="status" className="mb-3 rounded-xl border border-[#b9d4c5] bg-[#edf7f1] px-4 py-3 text-xs leading-5 text-[#23513e]">グラフの選択ノードから派生した質問です。ノード内容は Research Run に記録され、回答は原文・引用で確認してください。</div>}
              <form onSubmit={ask} className="rounded-3xl border border-[#bfc9c2] bg-white p-2 shadow-[0_16px_50px_rgba(28,45,37,.13)]">
                {canWrite && <fieldset disabled={busy} className="mb-2 rounded-2xl bg-[#f5f8f5] p-3">
                  <legend className="px-1 text-xs font-bold text-[#294638]">今回の目的</legend>
                  <p className="mt-1 px-1 text-[11px] leading-5 text-[#52605b]">目的を選ぶと、検索結果に追加する検討フレームを切り替えます。</p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {EDITOR_INTERACTION_MODES.map(mode => <label key={mode.id} className={`cursor-pointer rounded-xl border p-3 transition has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[#164f3b] ${interactionMode === mode.id ? "border-[#4f8a6d] bg-[#e9f4ed]" : "border-[#d5ded8] bg-white hover:border-[#8db49f]"}`}>
                      <input type="radio" name="interaction-mode" value={mode.id} checked={interactionMode === mode.id} onChange={() => setInteractionMode(mode.id)} className="sr-only"/>
                      <span className="block text-xs font-bold text-[#26342e]">{mode.label}</span>
                      <span className="mt-1 block text-[11px] leading-5 text-[#52605b]">{mode.description}</span>
                      <span className="mt-2 block border-t border-[#d9e6de] pt-2 text-[10px] leading-4 text-[#35634f]">{mode.example}</span>
                    </label>)}
                  </div>
                  {interactionMode !== "synthesis" && <p role="status" className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-950">発想・反証・実験設計・判断更新の出力は draft / unverified です。論文原文と引用を確認し、人間が採否を判断してください。</p>}
                </fieldset>}
                <textarea aria-label="研究について質問" disabled={busy} maxLength={4000} value={query} onChange={event => replaceQuery(event.target.value)} rows={3} placeholder={canWrite ? "論文をまとめる、仮説を反証する、次の実験を設計する…" : "論文の原文根拠を検索…（LLM回答は編集者のみ）"} className="min-h-20 w-full resize-none rounded-2xl bg-transparent px-4 py-3 text-base outline-none placeholder:text-[#9ba19e]"/>
                <div className="flex items-center justify-between gap-3 px-2 pb-1"><span className="flex min-w-0 items-center gap-1.5 truncate text-[11px] font-medium text-[#52605b]"><CircleStackIcon className="h-3.5 w-3.5 shrink-0 text-[#35634f]"/><span className="truncate">プロジェクト共通 · {sourceScopeLabel}</span></span>{busy ? <button type="button" onClick={stopDisplay} className="inline-flex shrink-0 items-center gap-2 rounded-full border border-[#b8bfba] px-4 py-2 text-xs font-semibold"><StopIcon className="h-4 w-4"/>表示を中断</button> : <button disabled={Array.from(query.trim()).length < 2} className="shrink-0 rounded-full bg-[#164f3b] px-5 py-2.5 text-xs font-semibold text-white disabled:opacity-40">{canWrite ? "質問する" : "原文を検索"}</button>}</div>
              </form>
              <div className="mt-3 flex gap-2 overflow-x-auto pb-1">{["論文全体から詳しくまとめて", "前提の弱い部分を反証して", "次の検証実験を設計して"].map(text => <button type="button" key={text} disabled={busy || !canWrite} onClick={() => replaceQuery(text)} className="shrink-0 rounded-full border border-[#d5d8d2] bg-white/70 px-3 py-1.5 text-xs text-[#52605b] disabled:opacity-40">{text}</button>)}</div>
            </div></div>
          </div>

          <CitationEvidencePanel evidence={evidence} grounded={Boolean(lastMeta?.grounded)} openEvidence={openEvidence}/>
        </div>
      </div>
      {graphMessage && <div role="dialog" aria-modal="true" aria-labelledby="graph-candidates-title" className="fixed inset-0 z-[100] flex items-end justify-center bg-[#07110d]/65 p-3 backdrop-blur-sm sm:items-center sm:p-6">
        <div className="max-h-[min(44rem,calc(100dvh-1.5rem))] w-full max-w-xl overflow-y-auto rounded-3xl border border-[#cbd8d0] bg-[#fffefa] p-5 shadow-2xl sm:p-6">
          <div className="flex items-start justify-between gap-4"><div><p className="text-[10px] font-bold uppercase tracking-[.16em] text-[#35634f]">Research memory → graph</p><h2 id="graph-candidates-title" className="serif mt-1 text-2xl font-semibold">レビュー候補を選ぶ</h2><p className="mt-2 text-xs leading-5 text-[#68736f]">候補は回答から作った<span className="font-bold text-[#a06a28]">未検証の研究案</span>です。会話の保存先は残しますが、論文根拠・検証済み知識・引用の支持を意味しません。</p></div><button type="button" onClick={closeGraphCandidates} disabled={graphSaving} aria-label="候補選択を閉じる" className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-[#d5d8d2] text-[#52605b] disabled:opacity-40"><XMarkIcon className="h-4 w-4"/></button></div>
          {graphCandidatesLoading ? <p role="status" className="py-10 text-center text-sm text-[#68736f]">研究メモリ候補を読み込んでいます…</p> : <div className="mt-5 space-y-3">{graphCandidates.map(candidate => { const saved = graphSavedMemoryRef.current.has(candidate.id); const checked = selectedGraphCandidateIds.includes(candidate.id); return <label key={candidate.id} className={`block rounded-2xl border p-4 ${saved ? "border-[#b8d6c4] bg-[#edf6f0]" : checked ? "border-[#5d9878] bg-[#f1f8f4]" : "border-[#deddd5] bg-white"}`}><div className="flex items-start gap-3"><input type="checkbox" checked={checked} disabled={saved || graphSaving} onChange={() => toggleGraphCandidate(candidate.id)} className="mt-1 h-4 w-4 accent-[#164f3b]"/><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><span className="rounded-full bg-[#e7f0eb] px-2 py-0.5 text-[10px] font-bold text-[#35634f]">{MEMORY_KIND_LABEL[candidate.kind]}</span>{saved && <span className="text-[10px] font-semibold text-[#35634f]">この画面で保存済み</span>}</div><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#26342e]">{candidate.content}</p></div></div></label>; })}{!graphCandidates.length && <p className="rounded-2xl border border-dashed border-[#cbd3cc] p-4 text-center text-sm leading-6 text-[#68736f]">自動候補はありません。下に、回答から検討したい仮説・未解決点を自分で記述して保存できます。</p>}<section className="rounded-2xl border border-[#c9ddd0] bg-[#f4faf6] p-4"><div className="flex flex-wrap items-center justify-between gap-2"><label htmlFor="graph-draft" className="text-xs font-bold text-[#294638]">自分でレビュー候補を追加</label><select aria-label="グラフ候補の種別" value={graphDraftKind} disabled={graphSaving} onChange={event => setGraphDraftKind(event.target.value as typeof graphDraftKind)} className="rounded-lg border border-[#d5d8d2] bg-white px-2 py-1 text-[11px]"><option value="idea">アイデア</option><option value="hypothesis">仮説</option><option value="constraint">制約・反証</option><option value="experiment">実験案</option></select></div><textarea id="graph-draft" value={graphDraft} disabled={graphSaving} onChange={event => setGraphDraft(event.target.value)} maxLength={100000} rows={3} placeholder="例: この効果は対象集団Aに限られる可能性がある。Bとの比較実験で反証する。" className="mt-2 w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm leading-5 disabled:opacity-50"/><p className="mt-2 text-[10px] leading-4 text-[#526b5d]">回答と引用情報を会話由来の根拠として残します。論文の事実・検証済み知識にはなりません。</p></section></div>}
          {graphError && <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-800">{graphError}</p>}{graphNotice && <p role="status" className="mt-4 rounded-xl border border-[#b8d6c4] bg-[#edf6f0] p-3 text-xs leading-5 text-[#24523e]">{graphNotice}</p>}
          <div className="mt-6 flex justify-end gap-2"><button type="button" onClick={closeGraphCandidates} disabled={graphSaving} className="rounded-full px-4 py-2 text-xs font-semibold text-[#52605b] disabled:opacity-40">閉じる</button><button type="button" onClick={() => void saveGraphCandidates()} disabled={graphCandidatesLoading || graphSaving || (!selectedGraphCandidateIds.length && !graphDraft.trim())} className="rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">{graphSaving ? "保存中…" : `レビュー待ちで${selectedGraphCandidateIds.length + (graphDraft.trim() ? 1 : 0)}件を保存`}</button></div>
        </div>
      </div>}
    </div>
  </section>;
}

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
  createResearchConversation, getResearchConversation, listResearchConversations,
  getLLMStatus, type Citation, type LLMStatus, type Paper, type ResearchConversation, type ResearchConversationDetail,
} from "@/lib/api/client";
import { apiErrorMessage, toApiError } from "@/lib/api/error";
import { SEARCH_STAGES, streamSearch, type SearchStage, type SearchStreamMeta } from "@/lib/api/search-stream";
import { remarkCitationLinks } from "@/lib/remark-citations.mjs";

type Replay = { query: string; paperIds: string[]; revision: number } | null;
type Phase = "idle" | "planning" | "answering" | "syncing";
const SOURCE_STORAGE_PREFIX = "paperpilot.project-sources.";
const DRAWER_FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";
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
            return <button type="button" onClick={() => openEvidence({ paperId:citation.paper_id, paperTitle:citation.paper_title, page:citation.page, chunkId:citation.chunk_id })} aria-label={`引用${citation.index}: ${citation.paper_title} ${citation.page}ページを開く`} className="mx-0.5 inline-flex rounded bg-[#dfeee6] px-1.5 py-0.5 align-baseline text-xs font-bold text-[#164f3b] hover:bg-[#c9e2d5] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]" title={`${citation.paper_title} p.${citation.page}`}>{children}</button>;
          }
          return <a href={href} target="_blank" rel="noreferrer noopener" className="font-semibold text-[#176143] underline decoration-[#8cb9a4] underline-offset-4">{children}</a>;
        },
      }}
    >{text}</ReactMarkdown>
  </div>;
}

function AssistantResponse({ text, citations, openEvidence, loading = false }: {
  text: string; citations: Citation[]; openEvidence: (target: EvidenceTarget) => void; loading?: boolean;
}) {
  return <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-3"><div className="grid h-8 w-8 place-items-center rounded-full bg-[#164f3b] text-white"><SparklesIcon className={`h-4 w-4 ${loading ? "animate-pulse" : ""}`}/></div>{text ? <AnswerWithCitations text={text} citations={citations} openEvidence={openEvidence}/> : <div className="space-y-3 pt-2"><div className="h-3 w-10/12 animate-pulse rounded bg-[#dfe3dd]"/><div className="h-3 w-full animate-pulse rounded bg-[#dfe3dd]"/><div className="h-3 w-7/12 animate-pulse rounded bg-[#dfe3dd]"/></div>}</div>;
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
  return <aside className="hidden min-h-0 overflow-y-auto border-l border-[#deddd5] bg-[#f3f3ef] p-4 xl:block" aria-label="最新回答の論文根拠"><div className="mb-4 flex items-center justify-between"><h2 className="text-xs font-bold uppercase tracking-[.14em] text-[#52605b]">Evidence</h2><span className="rounded-full bg-white px-2 py-1 text-[10px] text-[#7a837f]">{evidence.length}件</span></div><div className="space-y-3">{evidence.map(citation => <button type="button" key={`${citation.chunk_id}-${citation.index}`} onClick={() => openEvidence({ paperId:citation.paper_id, paperTitle:citation.paper_title, page:citation.page, chunkId:citation.chunk_id })} aria-label={`引用${citation.index}: ${citation.paper_title} ${citation.page}ページを開く`} className="block w-full rounded-2xl border border-[#d8dad4] bg-white p-4 text-left hover:border-[#6f9d86] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><div className="mb-2 flex items-center gap-2"><span className="grid h-6 w-6 place-items-center rounded-full bg-[#164f3b] text-[10px] font-bold text-white">{citation.index}</span><span className="text-xs font-semibold text-[#a06a28]">p. {citation.page} · {citation.section}</span></div><p className="line-clamp-2 text-xs font-semibold leading-5">{citation.paper_title}</p><p className="mt-2 line-clamp-4 text-xs leading-5 text-[#68736f]">{citation.excerpt}</p><span className="mt-3 inline-flex items-center gap-1 text-[10px] font-bold text-[#35634f]">根拠ページを確認<ChevronRightIcon className="h-3 w-3"/></span></button>)}{!evidence.length && <div className="rounded-2xl border border-dashed border-[#ccd1cc] p-5 text-center"><DocumentTextIcon className="mx-auto h-5 w-5 text-[#89918e]"/><p className="mt-2 text-xs leading-5 text-[#7a837f]">回答に引用が付くと、根拠のページと原文がここに表示されます。</p></div>}</div>{grounded && <div className="mt-4 flex items-center gap-2 rounded-xl bg-[#e1eee7] p-3 text-xs text-[#23513e]"><CheckCircleIcon className="h-4 w-4"/>引用番号を検証済み</div>}</aside>;
}

export function AskWorkspace({ workspaceId, papers, selected, setSelected, openEvidence, replay, canWrite }: {
  workspaceId: string;
  papers: Paper[]; selected: string[]; setSelected: (ids: string[]) => void;
  openEvidence: (target: EvidenceTarget) => void; replay: Replay; canWrite: boolean;
}) {
  const readyPapers = useMemo(() => papers.filter(paper => paper.status === "ready"), [papers]);
  const readyIds = useMemo(() => new Set(readyPapers.map(paper => paper.id)), [readyPapers]);
  const [query, setQuery] = useState("");
  const [conversations, setConversations] = useState<ResearchConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const [detail, setDetail] = useState<ResearchConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [creating, setCreating] = useState(false);
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
  const sourceSelectionRestoredRef = useRef(false);
  const streamAbortRef = useRef<AbortController | null>(null);
  const createAbortRef = useRef<AbortController | null>(null);
  const interruptionAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const historyButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const busy = phase !== "idle" || interruptionSyncing || detailLoading;

  const selectConversation = (conversationId: string) => {
    if (busy || conversationId === activeIdRef.current) return;
    activeIdRef.current = conversationId;
    setActiveId(conversationId);
    setLastMeta(null);
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
    return () => { controller.abort(); createAbortRef.current?.abort(); detailAbortRef.current?.abort(); streamAbortRef.current?.abort(); interruptionAbortRef.current?.abort(); };
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
  useEffect(() => { if (replay) setQuery(replay.query); }, [replay?.revision]);
  useEffect(() => { messageEndRef.current?.scrollIntoView({ block:"end", behavior:"smooth" }); }, [detail?.messages?.length, liveAnswer, liveQuestion]);

  const startNew = async () => {
    if (!canWrite || busy || creating) return;
    createAbortRef.current?.abort();
    const controller = new AbortController(); createAbortRef.current = controller;
    setCreating(true); setError(""); setSyncNotice("");
    try {
      const created = await createResearchConversation("新しい研究対話", controller.signal);
      activeIdRef.current = created.id;
      setDetail({ ...created, messages:[] }); setActiveId(created.id); setQuery(""); setLastMeta(null);
      await refreshList(created.id, controller.signal);
      setHistoryOpen(false);
    } catch (requestError) {
      const normalized = toApiError(requestError, "研究対話を作成できませんでした");
      if (normalized.code !== "aborted") setError(apiErrorMessage(normalized));
    } finally {
      if (createAbortRef.current === controller) { createAbortRef.current = null; setCreating(false); }
    }
  };

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const prompt = query.trim();
    if (!canWrite || busy) return;
    if (Array.from(prompt).length < 2) { setError("質問は2文字以上で入力してください。"); return; }
    detailAbortRef.current?.abort(); setDetailLoading(false);
    streamAbortRef.current?.abort(); setPhase("planning"); setSearchStage("accepted"); setError(""); setSyncNotice("");
    setLiveQuestion(prompt); setLiveAnswer(""); setLiveCitations([]); setLastMeta(null);
    const controller = new AbortController(); streamAbortRef.current = controller;
    let conversationId = activeIdRef.current;
    let streamCompleted = false;
    try {
      if (!conversationId) {
        const created = await createResearchConversation(prompt.slice(0, 80), controller.signal);
        conversationId = created.id; activeIdRef.current = created.id; setActiveId(created.id);
        setConversations(current => [created, ...current]);
      }
      for await (const streamEvent of streamSearch({ query:prompt, paper_ids:selected, limit:10, conversation_id:conversationId }, controller.signal)) {
        if (streamEvent.type === "token") { setPhase("answering"); setLiveAnswer(current => current + streamEvent.value); }
        if (streamEvent.type === "citations") setLiveCitations(streamEvent.value);
        if (streamEvent.type === "stage") setSearchStage(streamEvent.value);
        if (streamEvent.type === "meta") setLastMeta(streamEvent.value);
        if (streamEvent.type === "done") streamCompleted = true;
      }
      setQuery(""); setPhase("syncing"); setSearchStage("saving");
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
      <aside className={`${historyOpen ? "fixed inset-0 z-[90] isolate flex" : "hidden"} lg:static lg:flex lg:flex-col`} aria-label="研究ナビゲーション">
        {historyOpen && <button type="button" aria-label="会話履歴を閉じる" className="absolute inset-0 bg-[#07110d]/80 backdrop-blur-[3px] lg:hidden" onClick={() => setHistoryOpen(false)}/>}
        <div ref={drawerRef} role={historyOpen ? "dialog" : undefined} aria-modal={historyOpen ? true : undefined} aria-label={historyOpen ? "研究対話履歴とプロジェクト知識ベース" : undefined} className="relative z-[1] flex h-[100dvh] w-[88vw] max-w-[340px] flex-col overflow-hidden border-r border-white/10 bg-[#10231b] text-white shadow-[28px_0_70px_rgba(0,0,0,.42)] lg:h-auto lg:min-h-[calc(100vh-5.5rem)] lg:w-auto lg:max-w-none lg:shadow-none">
          <div className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-4"><div><p className="text-[10px] font-bold uppercase tracking-[.2em] text-[#91ad9f]">Research cockpit</p><p className="mt-1 text-sm font-semibold text-white">研究ナビゲーション</p></div><button type="button" onClick={() => setHistoryOpen(false)} aria-label="研究ナビゲーションを閉じる" className="grid h-9 w-9 place-items-center rounded-full border border-white/15 text-[#d8e6df] hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white lg:hidden"><XMarkIcon className="h-5 w-5"/></button></div>
          <div className="flex min-h-0 flex-1 flex-col p-4">
            <button type="button" onClick={startNew} disabled={!canWrite || busy || creating} className="flex w-full shrink-0 items-center justify-center gap-2 rounded-xl border border-[#78a58f]/60 bg-[#e8f3ed] px-4 py-3 text-sm font-semibold text-[#123d2d] shadow-lg shadow-black/15 disabled:opacity-40"><PlusIcon className="h-4 w-4"/>{creating ? "作成中…" : "新しい研究対話"}</button>
            <div className="mt-5 flex shrink-0 items-center justify-between px-2"><p className="text-[10px] font-bold uppercase tracking-[.18em] text-[#91ad9f]">Conversation history</p><span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-[#b9cec3]">{conversations.length}件</span></div>
            <div className="mt-2 min-h-28 flex-1 space-y-1 overflow-y-auto overscroll-contain pr-1 [scrollbar-color:#5f7f70_transparent]">
              {conversations.map(item => <button type="button" key={item.id} disabled={busy} onClick={() => selectConversation(item.id)} aria-current={activeId === item.id ? "page" : undefined} className={`w-full rounded-xl border px-3 py-3 text-left transition disabled:cursor-not-allowed ${activeId === item.id ? "border-[#739d89]/60 bg-[#254438] text-white shadow-lg shadow-black/15" : "border-transparent text-[#c3d2ca] hover:border-white/10 hover:bg-white/[.07]"}`}><span className="block truncate text-sm font-semibold">{item.title}</span><span className={`mt-1 flex items-center gap-1 text-[10px] ${activeId === item.id ? "text-[#a9cbbb]" : "text-[#789387]"}`}><ClockIcon className="h-3 w-3"/>{relativeDate(item.updated_at)}</span></button>)}
              {!conversations.length && !creating && <p className="px-3 py-6 text-center text-xs leading-5 text-[#91ad9f]">研究対話はまだありません。<br/>問いを送ると自動で作成されます。</p>}
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
                {messages.map(message => <article key={message.id} aria-label={message.role === "user" ? "あなた" : "PaperPilot"} className={message.role === "user" ? "ml-auto max-w-[88%] rounded-3xl rounded-br-md bg-[#e6e8e3] px-5 py-3 text-sm leading-7 text-[#26312c]" : "max-w-full"}>{message.role === "assistant" ? <AssistantResponse text={message.content} citations={message.citations ?? []} openEvidence={openEvidence}/> : <p className="whitespace-pre-wrap">{message.content}</p>}</article>)}
                {liveQuestion && <article aria-label="あなた" className="ml-auto max-w-[88%] rounded-3xl rounded-br-md bg-[#e6e8e3] px-5 py-3 text-sm leading-7 text-[#26312c]"><p className="whitespace-pre-wrap">{liveQuestion}</p></article>}
                {(liveAnswer || busy) && <article aria-label="PaperPilotの回答"><AssistantResponse text={liveAnswer} citations={liveCitations} openEvidence={openEvidence} loading={!liveAnswer}/></article>}
                <div ref={messageEndRef}/>
              </div>
            </div>

            <div className="shrink-0 px-3 pb-4 md:px-8 md:pb-6"><div className="mx-auto max-w-3xl">
              <div className="sr-only" role="status" aria-live="polite">{syncNotice || error}</div>
              {error && <div role="alert" className="mb-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
              {syncNotice && <div role="status" className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900">{syncNotice}</div>}
              {fallbackNotice && <div role="status" className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-medium leading-5 text-amber-950"><span className="font-bold">{lastMeta?.generation_mode === "agentic_rag" ? "根拠監査の注意:" : "LLMフォールバック:"}</span> {fallbackNotice}</div>}
              <form onSubmit={ask} className="rounded-3xl border border-[#bfc9c2] bg-white p-2 shadow-[0_16px_50px_rgba(28,45,37,.13)]"><textarea aria-label="研究について質問" disabled={!canWrite || busy} maxLength={4000} value={query} onChange={event => setQuery(event.target.value)} rows={3} placeholder={canWrite ? "論文をまとめる、仮説を反証する、次の実験を設計する…" : "viewer権限では研究対話へ追記できません"} className="min-h-20 w-full resize-none rounded-2xl bg-transparent px-4 py-3 text-base outline-none placeholder:text-[#9ba19e]"/><div className="flex items-center justify-between gap-3 px-2 pb-1"><span className="flex min-w-0 items-center gap-1.5 truncate text-[11px] font-medium text-[#52605b]"><CircleStackIcon className="h-3.5 w-3.5 shrink-0 text-[#35634f]"/><span className="truncate">プロジェクト共通 · {sourceScopeLabel}</span></span>{busy ? <button type="button" onClick={stopDisplay} className="inline-flex shrink-0 items-center gap-2 rounded-full border border-[#b8bfba] px-4 py-2 text-xs font-semibold"><StopIcon className="h-4 w-4"/>表示を中断</button> : <button disabled={!canWrite || Array.from(query.trim()).length < 2} className="shrink-0 rounded-full bg-[#164f3b] px-5 py-2.5 text-xs font-semibold text-white disabled:opacity-40">質問する</button>}</div></form>
              <div className="mt-3 flex gap-2 overflow-x-auto pb-1">{["論文全体から詳しくまとめて", "前提の弱い部分を反証して", "次の検証実験を設計して"].map(text => <button type="button" key={text} disabled={busy || !canWrite} onClick={() => setQuery(text)} className="shrink-0 rounded-full border border-[#d5d8d2] bg-white/70 px-3 py-1.5 text-xs text-[#52605b] disabled:opacity-40">{text}</button>)}</div>
            </div></div>
          </div>

          <CitationEvidencePanel evidence={evidence} grounded={Boolean(lastMeta?.grounded)} openEvidence={openEvidence}/>
        </div>
      </div>
    </div>
  </section>;
}

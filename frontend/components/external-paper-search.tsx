"use client";

import { CheckIcon, MagnifyingGlassIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { GenerationModelSelector } from "@/components/generation-model-selector";
import type { DiscoveryImportRequest, DiscoveryImportResponse, DiscoverySearchRequest, DiscoverySearchResponse, GenerationOverride } from "@/lib/api/client";
import { apiErrorMessage, toApiError } from "@/lib/api/error";
import { safeExternalHttpsUrl } from "@/lib/api/safe-external-url";
import { UI_COPY } from "@/lib/copy";

type Candidate = NonNullable<DiscoverySearchResponse["items"]>[number];
type SearchContext = NonNullable<DiscoveryImportRequest["search_context"]>;
export type ExternalPaperSearchRequest = DiscoverySearchRequest & GenerationOverride & { search_mode?: "question" | "keyword" };
export type ExternalPaperSearchPage = DiscoverySearchResponse;
export type ExternalPaperImportRequest = DiscoveryImportRequest;
export type ExternalPaperImportResult = DiscoveryImportResponse;
type Props = { canWrite: boolean; canManageDefaults?: boolean; onSearch: (request: ExternalPaperSearchRequest, signal: AbortSignal) => Promise<ExternalPaperSearchPage>; onAddAbstracts: (request: ExternalPaperImportRequest, signal: AbortSignal) => Promise<ExternalPaperImportResult>; onAddSession?: (sessionId: string, candidateIds: string[], signal: AbortSignal) => Promise<ExternalPaperImportResult>; onAdded?: () => void | Promise<void> };
const PAGE_SIZE = 20;
const stages = ["検索語を整理中", "国内外の論文DBを検索中", "結果を整理中"] as const;
const DISCOVERY_PROVIDERS = [
  ["semantic_scholar", "Semantic Scholar"], ["openalex", "OpenAlex"], ["cinii", "CiNii Research"], ["jstage", "J-STAGE"],
] as const;

function candidateKey(item: Candidate) { return item.candidate_id || item.provider_paper_id; }
function errorMessage(error: unknown, fallback: string) {
  const normalized = toApiError(error, fallback);
  if (normalized.code === "discovery_session_not_found") return "検索結果の保存期限が切れました。検索をやり直してください。";
  return normalized.status === 429 || normalized.code === "external_provider_rate_limited" || normalized.code === "rate_limited" ? "外部データベースの利用上限に達しました。少し時間をおいてから再試行してください。" : apiErrorMessage(normalized, fallback);
}

function isConnectionError(error: unknown): boolean {
  const normalized = toApiError(error, "外部論文を検索できませんでした。");
  return normalized.code === "network_error";
}

export function ExternalPaperSearch({ canWrite, canManageDefaults = false, onSearch, onAddAbstracts, onAddSession, onAdded }: Props) {
  const [query, setQuery] = useState(""); const [yearFrom, setYearFrom] = useState(""); const [yearTo, setYearTo] = useState("");
  const [sort, setSort] = useState<ExternalPaperSearchRequest["sort"]>("relevance"); const [override, setOverride] = useState<GenerationOverride>({});
  const [selectedProviders, setSelectedProviders] = useState<NonNullable<DiscoverySearchRequest["providers"]>>(["semantic_scholar", "openalex", "jstage"]);
  const [page, setPage] = useState<ExternalPaperSearchPage | null>(null); const [cursorStack, setCursorStack] = useState<Array<string | undefined>>([]);
  const [pageHistory, setPageHistory] = useState<ExternalPaperSearchPage[]>([]);
  const [searched, setSearched] = useState(false); const [searching, setSearching] = useState(false); const [adding, setAdding] = useState(false); const [stage, setStage] = useState(0);
  const [error, setError] = useState(""); const [connectionError, setConnectionError] = useState(false); const [sessionExpired, setSessionExpired] = useState(false); const [notice, setNotice] = useState(""); const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set()); const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const requestAbortRef = useRef<AbortController | null>(null); const addAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => { requestAbortRef.current?.abort(); addAbortRef.current?.abort(); }, []);
  useEffect(() => { if (!searching) return; const timer = window.setTimeout(() => setStage(current => Math.min(2, current + 1)), 350); return () => window.clearTimeout(timer); }, [searching, stage]);
  const searchContext = (): SearchContext => ({ query:query.trim(), year_from:yearFrom ? Number(yearFrom) : undefined, year_to:yearTo ? Number(yearTo) : undefined, sort });
  const items = page?.items ?? []; const selectable = useMemo(() => items.filter(item => !item.existing_paper_id), [items]); const selected = useMemo(() => selectable.filter(item => selectedIds.has(candidateKey(item))), [selectable, selectedIds]);
  const allSelected = Boolean(selectable.length) && selectable.every(item => selectedIds.has(candidateKey(item))); const pageNumber = cursorStack.length + 1;
  const runSearch = async (cursor?: string, nextStack: Array<string | undefined> = [], nextHistory: ExternalPaperSearchPage[] = []) => {
    setConnectionError(false); setSessionExpired(false);
    const context = searchContext(); if (!context.query) { setError("検索語を入力してください。"); return; } if (!selectedProviders.length) { setError("少なくとも1つの提供元を選択してください。"); return; } if (context.year_from && context.year_to && context.year_from > context.year_to) { setError("開始年は終了年以前にしてください。"); return; }
    requestAbortRef.current?.abort(); const controller = new AbortController(); requestAbortRef.current = controller; setSearching(true); setStage(0); setError(""); setConnectionError(false); setSessionExpired(false); setNotice("");
    try {
      const result = await onSearch({ ...context, search_mode:"question", providers:selectedProviders, cursor, ...override } as ExternalPaperSearchRequest, controller.signal);
      if (requestAbortRef.current !== controller) return;
      setStage(2);
      setPage(current => cursor && current ? {
        ...result,
        search_plan:Object.keys(result.search_plan ?? {}).length ? result.search_plan : current.search_plan,
        providers:(result.providers ?? []).length ? result.providers : current.providers,
        warnings:(result.warnings ?? []).length ? result.warnings : current.warnings,
        providers_used:(result.providers_used ?? []).length ? result.providers_used : current.providers_used,
        degraded_providers:(result.degraded_providers ?? []).length ? result.degraded_providers : current.degraded_providers,
        generation_provider:result.generation_provider || current.generation_provider,
        generation_model:result.generation_model || current.generation_model,
      } : result);
      setCursorStack(nextStack); setPageHistory(nextHistory); setSelectedIds(new Set()); setExpandedIds(new Set()); setSearched(true);
    }
    catch (requestError) { if (!controller.signal.aborted) { const normalized = toApiError(requestError, "外部論文を検索できませんでした。"); setSessionExpired(normalized.code === "discovery_session_not_found"); setConnectionError(isConnectionError(requestError)); setError(errorMessage(requestError, "外部論文を検索できませんでした。")); setSearched(true); } }
    finally { if (requestAbortRef.current === controller) { requestAbortRef.current = null; setSearching(false); } }
  };
  const showPreviousPage = () => {
    const previous = pageHistory.at(-1);
    if (!previous) return;
    setPage(previous);
    setPageHistory(current => current.slice(0, -1));
    setCursorStack(current => current.slice(0, -1));
    setSelectedIds(new Set());
    setExpandedIds(new Set());
    setError("");
    setConnectionError(false);
    setSessionExpired(false);
  };
  const addSelected = async () => {
    if (!selected.length || adding) return; const controller = new AbortController(); addAbortRef.current?.abort(); addAbortRef.current = controller; setAdding(true); setError(""); setConnectionError(false); setSessionExpired(false); setNotice("");
    try {
      const sessionId = page?.search_session_id;
      const candidateIds = selected.map(candidateKey);
      const result = sessionId && onAddSession
        ? await onAddSession(sessionId, candidateIds, controller.signal)
        : await onAddAbstracts({ provider_paper_ids:selected.map(item => item.provider_paper_id), search_context:searchContext() }, controller.signal); if (addAbortRef.current !== controller) return;
      const imported = result.items?.filter(item => item.status === "imported").length ?? 0; const failed = result.items?.filter(item => item.status === "failed").length ?? 0;
      const registered = new Map((result.items ?? []).filter(item => item.paper_id).map(item => [item.provider_paper_id, item.paper_id!]));
      setPage(current => current ? { ...current, items:(current.items ?? []).map(item => ({ ...item, existing_paper_id:registered.get(item.provider_paper_id) ?? item.existing_paper_id })) } : current);
      setSelectedIds(new Set()); setNotice(`${imported}件を要旨として追加しました${failed ? `。${failed}件は追加できませんでした` : ""}。`); await onAdded?.();
    } catch (requestError) { if (!controller.signal.aborted) { const normalized = toApiError(requestError, "要旨をライブラリへ追加できませんでした。"); setSessionExpired(normalized.code === "discovery_session_not_found"); setError(errorMessage(requestError, "要旨をライブラリへ追加できませんでした。")); } }
    finally { if (addAbortRef.current === controller) { addAbortRef.current = null; setAdding(false); } }
  };
  const providers = page?.providers ?? []; const warnings = [...(page?.warnings ?? []), ...providers.filter(item => item.status === "failed" || item.status === "disabled").map(item => item.warning || `${item.provider} の結果を一部取得できませんでした。`)];
  return <section aria-labelledby="external-paper-search-title" className="mb-8 rounded-3xl border border-[#9fc7ae] bg-[#e8f5ed] p-4 shadow-sm sm:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold tracking-[.16em] text-[#35634f]">DISCOVERY</p><h2 id="external-paper-search-title" className="serif mt-1 text-2xl font-semibold text-[#17201d]">論文を検索</h2><p className="mt-1 max-w-2xl text-xs leading-5 text-[#526b5d]">研究上の質問を日本語で入力すると、関連する英語・日本語の検索語を自動で作成します。</p></div>{!canWrite && <span className="rounded-full bg-[#fff8ed] px-3 py-1 text-xs font-semibold text-[#7a5426]">閲覧者は検索・追加できません</span>}</div>
    <form onSubmit={(event: FormEvent) => { event.preventDefault(); void runSearch(); }} className="mt-5 flex flex-col gap-3 sm:flex-row"><label className="min-w-0 flex-1"><span className="mb-1.5 block text-sm font-semibold text-[#26342e]">キーワード・検索したい内容</span><input value={query} onChange={event => setQuery(event.target.value)} disabled={!canWrite || searching} placeholder="キーワード、検索したい内容を入力" className="w-full rounded-xl border border-[#aebbb3] bg-white px-4 py-3 text-sm outline-none focus:border-[#164f3b] focus-visible:ring-2 focus-visible:ring-[#84b99b] disabled:cursor-not-allowed disabled:opacity-60" /></label><button type="submit" disabled={!canWrite || searching || !query.trim()} className="mt-auto min-h-11 rounded-full bg-[#164f3b] px-6 py-2.5 text-sm font-semibold text-white disabled:opacity-50"><MagnifyingGlassIcon className="mr-2 inline h-4 w-4"/>{searching ? "検索中…" : "論文を検索"}</button></form>
    <details className="mt-3 rounded-xl border border-[#c9ddd0] bg-white/70 p-3"><summary className="cursor-pointer text-xs font-semibold text-[#164f3b]">詳細条件</summary><div className="mt-3 grid gap-3 sm:grid-cols-3"><label className="text-xs">開始年<input type="number" value={yearFrom} onChange={event => setYearFrom(event.target.value)} disabled={!canWrite || searching} className="mt-1 w-full rounded-lg border bg-white px-3 py-2"/></label><label className="text-xs">終了年<input type="number" value={yearTo} onChange={event => setYearTo(event.target.value)} disabled={!canWrite || searching} className="mt-1 w-full rounded-lg border bg-white px-3 py-2"/></label><label className="text-xs">並び順<select value={sort} onChange={event => setSort(event.target.value as typeof sort)} disabled={!canWrite || searching} className="mt-1 w-full rounded-lg border bg-white px-3 py-2"><option value="relevance">関連度順</option><option value="newest">新着順</option><option value="citation_count">被引用数順</option></select></label></div><div className="mt-3 max-w-xs"><GenerationModelSelector scope="discovery" canOverride={canWrite} canManageDefaults={canManageDefaults} value={override} onChange={setOverride}/></div></details>
    <fieldset className="mt-3 rounded-xl border border-[#c9ddd0] bg-white/70 p-3"><legend className="px-1 text-xs font-semibold text-[#164f3b]">検索する提供元</legend><div className="mt-1 flex flex-wrap gap-x-4 gap-y-2">{DISCOVERY_PROVIDERS.map(([provider, label]) => <label key={provider} className="flex items-center gap-1.5 text-xs text-[#40534a]"><input type="checkbox" checked={selectedProviders.includes(provider)} disabled={!canWrite || searching} onChange={() => setSelectedProviders(current => current.includes(provider) ? current.filter(item => item !== provider) : [...current, provider])} className="h-3.5 w-3.5 accent-[#164f3b]"/>{label}</label>)}</div>{!selectedProviders.length && <p role="alert" className="mt-2 text-[11px] text-red-700">少なくとも1つの提供元を選択してください。</p>}</fieldset>
    {searching && <ol aria-label="論文検索の進行状況" className="mt-4 grid gap-2 sm:grid-cols-3">{stages.map((label, index) => <li key={label} role="status" className={`rounded-xl px-3 py-2 text-xs font-semibold ${index < stage ? "bg-[#dfeee6] text-[#24523e]" : index === stage ? "bg-white text-[#164f3b] ring-1 ring-[#83b49a]" : "bg-white/55 text-[#68736f]"}`}>{index < stage ? "✓ " : ""}{label}</li>)}</ol>}
    {error && <div role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800"><p>{error}</p>{connectionError && <p className="mt-1 text-xs">{UI_COPY.discovery.connectionHelp}</p>}<button type="button" onClick={() => void runSearch()} disabled={!canWrite || searching || !query.trim()} className="mt-2 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-xs font-semibold disabled:opacity-50">{UI_COPY.discovery.retrySearch}</button></div>}{notice && <p role="status" className="mt-4 rounded-xl border border-[#b9d4c5] bg-white p-3 text-sm text-[#23513e]">{notice}</p>}
    {warnings.length > 0 && <div role="status" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"><strong>一部の提供元の結果を表示できません</strong><ul className="mt-1 list-disc pl-5">{warnings.map((warning, index) => <li key={`${warning}:${index}`}>{warning}</li>)}</ul></div>}
    {providers.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{providers.map(item => <span key={item.provider} className="rounded-full bg-white px-2.5 py-1 text-[10px] font-semibold text-[#52605b]">{item.provider}: {item.status}{typeof item.item_count === "number" ? ` ${item.item_count}件` : ""}</span>)}</div>}
    {page && <><div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-y border-[#c9ddd0] py-3 text-xs text-[#526b5d]"><span>{page.total_estimate ? `約${page.total_estimate}件・` : ""}{pageNumber}ページ目（{PAGE_SIZE}件ずつ）{page.expires_at ? `・検索結果の保存期限 ${new Date(page.expires_at).toLocaleTimeString("ja-JP", { hour:"2-digit", minute:"2-digit" })}` : ""}</span><div className="flex gap-2"><button type="button" onClick={() => setSelectedIds(allSelected ? new Set() : new Set(selectable.map(candidateKey)))} disabled={!canWrite || !selectable.length || adding} className="rounded-lg border bg-white px-3 py-1.5 font-semibold text-[#164f3b] disabled:opacity-50">{allSelected ? "全選択を解除" : "このページを全選択"}</button><button type="button" onClick={() => void addSelected()} disabled={!canWrite || !selected.length || adding} className="rounded-lg bg-[#164f3b] px-3 py-1.5 font-semibold text-white disabled:opacity-50">{adding ? "要旨を追加中…" : `選択した${selected.length}件を要旨として追加`}</button></div></div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">{items.map(item => { const key = candidateKey(item); const selectedNow = selectedIds.has(key); const expanded = expandedIds.has(key); const existing = Boolean(item.existing_paper_id); const sourceUrl = safeExternalHttpsUrl(item.source_url); return <article key={key} className="rounded-2xl border border-[#d6e2da] bg-white p-4 shadow-sm"><div className="flex gap-3"><button type="button" aria-label={existing ? `${item.title}は登録済みです` : `${item.title}を${selectedNow ? "選択解除" : "選択"}`} aria-pressed={selectedNow} disabled={!canWrite || adding || existing} onClick={() => setSelectedIds(current => { const next = new Set(current); next.has(key) ? next.delete(key) : next.add(key); return next; })} className={`mt-1 grid h-5 w-5 shrink-0 place-items-center rounded border ${selectedNow ? "border-[#164f3b] bg-[#164f3b] text-white" : "border-[#8aa396]"}`}>{selectedNow && <CheckIcon className="h-3.5 w-3.5"/>}</button><div className="min-w-0 flex-1"><div className="flex flex-wrap gap-2 text-[10px]"><span className="rounded-full bg-[#e7f0eb] px-2 py-0.5 font-bold text-[#35634f]">要旨のみ</span>{item.language && <span>{item.language}</span>}{(item.source_providers ?? []).map(provider => <span key={provider} className="rounded-full bg-slate-100 px-2 py-0.5">{provider}</span>)}{existing && <span className="rounded-full bg-[#f1f3ee] px-2 py-0.5">登録済み</span>}</div><h3 className="serif mt-2 text-lg font-semibold">{item.title}</h3><p className="mt-1 text-xs text-[#68736f]">{item.authors?.length ? item.authors.join(", ") : "著者情報なし"}</p><p className="mt-1 text-[11px] leading-5 text-[#52605b]">{item.year ?? "年不明"}{item.venue ? ` · ${item.venue}` : ""} · 被引用 {item.citation_count ?? 0}件{item.language ? ` · ${item.language}` : ""}{item.source_providers?.length ? ` · 提供元 ${item.source_providers.join(" / ")}` : ""}{item.provider_ids && Object.keys(item.provider_ids).length ? ` · ID ${Object.entries(item.provider_ids).map(([provider, id]) => `${provider}:${id}`).join(" / ")}` : ""}</p>{item.match_reasons?.length ? <p className="mt-2 text-[11px] text-[#35634f]">一致理由: {item.match_reasons.join(" / ")}</p> : null}{item.possible_duplicate_of && <p className="mt-2 text-[11px] text-amber-800">登録済み論文との重複候補があります。</p>}<button type="button" onClick={() => setExpandedIds(current => { const next = new Set(current); next.has(key) ? next.delete(key) : next.add(key); return next; })} className="mt-3 text-xs font-semibold text-[#164f3b] underline">{expanded ? "要旨を閉じる" : "要旨を開く"}</button>{expanded && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#52605b]">{item.abstract || "要旨は提供されていません。"}</p>}{sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer noopener" className="mt-3 inline-block text-xs font-semibold text-[#164f3b] underline">提供元で開く<span className="sr-only">（新しいタブ）</span></a>}</div></div></article>; })}</div>
      {!items.length && <p className="mt-5 rounded-2xl bg-white/70 p-6 text-center text-sm text-[#68736f]">条件に一致する論文は見つかりませんでした。</p>}<nav aria-label="外部論文検索のページ送り" className="mt-5 flex items-center justify-end gap-3 text-sm"><button type="button" disabled={!canWrite || searching || !pageHistory.length} onClick={showPreviousPage} className="rounded-lg border bg-white px-3 py-2 disabled:opacity-50">前へ</button><span>{pageNumber}ページ目</span><button type="button" disabled={!canWrite || searching || !page.next_cursor} onClick={() => page.next_cursor && void runSearch(page.next_cursor, [...cursorStack, page.next_cursor], [...pageHistory, page])} className="rounded-lg border bg-white px-3 py-2 disabled:opacity-50">次へ</button></nav></>}
    {page?.search_plan && (() => { const plan=page.search_plan as { queries?: unknown; expanded?: unknown }; const queries=Array.isArray(plan.queries) ? plan.queries.filter((item): item is string => typeof item === "string") : []; return <details className="mt-4 rounded-xl border border-[#c9ddd0] bg-white/70 p-3"><summary className="cursor-pointer text-xs font-semibold text-[#164f3b]">実際に使用した検索語</summary>{plan.expanded === false && <p className="mt-2 text-[11px] text-[#68736f]">検索語の自動展開なし</p>}{queries.length ? <ul className="mt-2 list-disc pl-5 text-xs text-[#52605b]">{queries.map((plannedQuery, index) => <li key={`${plannedQuery}:${index}`}>{plannedQuery}</li>)}</ul> : <p className="mt-2 text-xs text-[#68736f]">検索語の記録はありません。</p>}</details>; })()}
  </section>;
}

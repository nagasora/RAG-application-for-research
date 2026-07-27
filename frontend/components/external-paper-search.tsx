"use client";

import { CheckIcon, MagnifyingGlassIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { apiErrorMessage, toApiError } from "@/lib/api/error";
import type { DiscoveryImportRequest, DiscoveryImportResponse, DiscoverySearchRequest, DiscoverySearchResponse } from "@/lib/api/client";

export type ExternalPaperSearchRequest = DiscoverySearchRequest;
export type ExternalPaperSearchPage = DiscoverySearchResponse;
export type ExternalPaperImportRequest = DiscoveryImportRequest;
export type ExternalPaperImportResult = DiscoveryImportResponse;

type Props = {
  canWrite: boolean;
  onSearch: (request: ExternalPaperSearchRequest, signal: AbortSignal) => Promise<ExternalPaperSearchPage>;
  onAddAbstracts: (request: ExternalPaperImportRequest, signal: AbortSignal) => Promise<ExternalPaperImportResult>;
  onAdded?: () => void | Promise<void>;
};

const PAGE_SIZE = 20;

function errorMessage(error: unknown, fallback: string) {
  const normalized = toApiError(error, fallback);
  if (normalized.status === 429 || normalized.code === "external_provider_rate_limited" || normalized.code === "rate_limited") return "外部データベースの利用上限に達しました。少し時間をおいてから再試行してください。";
  return apiErrorMessage(normalized, fallback);
}

export function ExternalPaperSearch({ canWrite, onSearch, onAddAbstracts, onAdded }: Props) {
  const [query, setQuery] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [sort, setSort] = useState<ExternalPaperSearchRequest["sort"]>("relevance");
  const [page, setPage] = useState<ExternalPaperSearchPage | null>(null);
  const [cursorStack, setCursorStack] = useState<Array<string | undefined>>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const requestAbortRef = useRef<AbortController | null>(null);
  const addAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => { requestAbortRef.current?.abort(); addAbortRef.current?.abort(); }, []);

  const searchContext = (): ExternalPaperImportRequest["search_context"] => ({
    query: query.trim(),
    year_from: yearFrom ? Number(yearFrom) : undefined,
    year_to: yearTo ? Number(yearTo) : undefined,
    sort,
  });
  const pageItems = page?.items ?? [];
  const selectablePapers = useMemo(() => pageItems.filter((item) => !item.existing_paper_id), [pageItems]);
  const selectedPapers = useMemo(() => selectablePapers.filter((item) => selectedIds.has(item.provider_paper_id)), [selectablePapers, selectedIds]);
  const allOnPageSelected = Boolean(selectablePapers.length) && selectablePapers.every((item) => selectedIds.has(item.provider_paper_id));
  const pageNumber = cursorStack.length + 1;

  const runSearch = async (cursor: string | undefined, nextCursorStack: Array<string | undefined>) => {
    const context = searchContext();
    if (!context.query) { setError("検索語を入力してください。"); return; }
    if (context.year_from && context.year_to && context.year_from > context.year_to) { setError("開始年は終了年以前にしてください。"); return; }
    requestAbortRef.current?.abort();
    const controller = new AbortController();
    requestAbortRef.current = controller;
    setSearching(true); setError(""); setNotice("");
    try {
      const next = await onSearch({ ...context, cursor }, controller.signal);
      if (requestAbortRef.current !== controller) return;
      setPage(next); setCursorStack(nextCursorStack); setSearched(true); setSelectedIds(new Set()); setExpandedIds(new Set());
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setError(errorMessage(requestError, "外部論文を検索できませんでした。")); setSearched(true); setPage(null);
    } finally {
      if (requestAbortRef.current === controller) { requestAbortRef.current = null; setSearching(false); }
    }
  };

  const submit = (event: FormEvent) => { event.preventDefault(); void runSearch(undefined, []); };
  const toggleAll = () => { if (page) setSelectedIds(allOnPageSelected ? new Set() : new Set(selectablePapers.map((item) => item.provider_paper_id))); };
  const clearConditions = () => {
    requestAbortRef.current?.abort();
    setQuery(""); setYearFrom(""); setYearTo(""); setSort("relevance"); setPage(null); setCursorStack([]); setSearched(false); setSelectedIds(new Set()); setExpandedIds(new Set()); setError(""); setNotice("");
  };
  const addSelected = async () => {
    if (!selectedPapers.length || adding) return;
    addAbortRef.current?.abort(); const controller = new AbortController(); addAbortRef.current = controller;
    setAdding(true); setError(""); setNotice("");
    try {
      const result = await onAddAbstracts({ provider_paper_ids: selectedPapers.map((item) => item.provider_paper_id), search_context: searchContext() }, controller.signal);
      if (addAbortRef.current !== controller) return;
      const importItems = result.items ?? [];
      const imported = importItems.filter((item) => item.status === "imported").length;
      const duplicates = importItems.filter((item) => item.status === "duplicate").length;
      const failed = importItems.filter((item) => item.status === "failed").length;
      const registeredPaperIds = new Map(
        importItems
          .filter((item) => item.status !== "failed" && item.paper_id)
          .map((item) => [item.provider_paper_id, item.paper_id!]),
      );
      setPage((current) => current ? {
        ...current,
        items: (current.items ?? []).map((item) => ({
          ...item,
          existing_paper_id: registeredPaperIds.get(item.provider_paper_id) ?? item.existing_paper_id,
        })),
      } : current);
      setNotice(`${imported}件を要旨として追加しました${duplicates ? `。${duplicates}件は登録済みです` : ""}${failed ? `。${failed}件は追加できませんでした` : ""}。`);
      setSelectedIds(new Set()); await onAdded?.();
    } catch (requestError) {
      if (!controller.signal.aborted) setError(errorMessage(requestError, "要旨をライブラリへ追加できませんでした。"));
    } finally { if (addAbortRef.current === controller) { addAbortRef.current = null; setAdding(false); } }
  };

  return <section aria-labelledby="external-paper-search-title" className="mb-8 rounded-3xl border border-[#c9ddd0] bg-[#eef6f0] p-4 sm:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold tracking-[.16em] text-[#42705b]">外部論文サーチ</p><h2 id="external-paper-search-title" className="serif mt-1 text-2xl font-semibold text-[#17201d]">見つけた論文を、要旨から検討する。</h2><p className="mt-1 max-w-2xl text-xs leading-5 text-[#526b5d]">検索結果は20件ずつ表示します。追加後も要旨のみで、PDF・原文操作は表示しません。</p></div>{!canWrite && <span className="rounded-full bg-[#fff8ed] px-3 py-1 text-xs font-semibold text-[#7a5426]">閲覧者は検索・追加できません</span>}</div>
    <form onSubmit={submit} className="mt-5 grid gap-3 lg:grid-cols-[minmax(15rem,1fr)_8rem_8rem_9rem_auto_auto]"><label className="min-w-0"><span className="sr-only">検索語</span><input value={query} onChange={(event) => setQuery(event.target.value)} disabled={!canWrite || searching} placeholder="研究テーマ、著者、キーワード" className="w-full rounded-xl border border-[#aebbb3] bg-white px-4 py-2.5 text-sm outline-none focus:border-[#164f3b] disabled:cursor-not-allowed disabled:opacity-60" /></label><label><span className="sr-only">開始年</span><input type="number" inputMode="numeric" min="1000" max="9999" value={yearFrom} onChange={(event) => setYearFrom(event.target.value)} disabled={!canWrite || searching} placeholder="開始年" className="w-full rounded-xl border border-[#aebbb3] bg-white px-3 py-2.5 text-sm outline-none focus:border-[#164f3b] disabled:cursor-not-allowed disabled:opacity-60" /></label><label><span className="sr-only">終了年</span><input type="number" inputMode="numeric" min="1000" max="9999" value={yearTo} onChange={(event) => setYearTo(event.target.value)} disabled={!canWrite || searching} placeholder="終了年" className="w-full rounded-xl border border-[#aebbb3] bg-white px-3 py-2.5 text-sm outline-none focus:border-[#164f3b] disabled:cursor-not-allowed disabled:opacity-60" /></label><label><span className="sr-only">並び順</span><select value={sort} onChange={(event) => setSort(event.target.value as ExternalPaperSearchRequest["sort"])} disabled={!canWrite || searching} className="w-full rounded-xl border border-[#aebbb3] bg-white px-3 py-2.5 text-sm outline-none focus:border-[#164f3b] disabled:cursor-not-allowed disabled:opacity-60"><option value="relevance">関連度順</option><option value="newest">新着順</option><option value="citation_count">被引用数順</option></select></label><button type="submit" disabled={!canWrite || searching || !query.trim()} className="inline-flex items-center justify-center gap-2 rounded-full bg-[#164f3b] px-5 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"><MagnifyingGlassIcon className="h-4 w-4" />{searching ? "検索中…" : "検索"}</button><button type="button" onClick={clearConditions} disabled={!canWrite || searching} className="rounded-full border border-[#9ab6a6] bg-white px-4 py-2.5 text-sm font-semibold text-[#164f3b] disabled:cursor-not-allowed disabled:opacity-50">クリア</button></form>
    {error && <div role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}{notice && <div role="status" aria-live="polite" className="mt-4 rounded-xl border border-[#b9d4c5] bg-white px-4 py-3 text-sm text-[#23513e]">{notice}</div>}
    {page && <><div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-y border-[#c9ddd0] py-3 text-xs text-[#526b5d]"><span>{page.total_estimate ? `約${page.total_estimate}件・${pageNumber}ページ目（20件ずつ）` : `${pageNumber}ページ目（20件ずつ）`}</span><div className="flex flex-wrap items-center gap-2"><button type="button" onClick={toggleAll} disabled={!canWrite || !selectablePapers.length || adding} className="rounded-lg border border-[#9ab6a6] bg-white px-3 py-1.5 font-semibold text-[#164f3b] disabled:cursor-not-allowed disabled:opacity-50">{allOnPageSelected ? "全選択を解除" : "このページを全選択"}</button><button type="button" onClick={() => void addSelected()} disabled={!canWrite || !selectedPapers.length || adding} className="rounded-lg bg-[#164f3b] px-3 py-1.5 font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{adding ? "要旨を追加中…" : `選択した${selectedPapers.length}件を要旨として追加`}</button></div></div>
      {pageItems.length ? <div className="mt-4 grid gap-3 md:grid-cols-2"><div className="sr-only" aria-live="polite">{`${pageItems.length}件の検索結果`}</div>{pageItems.map((item) => { const selected = selectedIds.has(item.provider_paper_id); const expanded = expandedIds.has(item.provider_paper_id); const existing = Boolean(item.existing_paper_id); return <article key={item.provider_paper_id} className="rounded-2xl border border-[#d6e2da] bg-white p-4 shadow-sm"><div className="flex gap-3"><button type="button" aria-label={existing ? `${item.title}は登録済みです` : `${item.title}を${selected ? "選択解除" : "選択"}`} aria-pressed={selected} disabled={!canWrite || adding || existing} onClick={() => setSelectedIds((current) => { const next = new Set(current); if (next.has(item.provider_paper_id)) next.delete(item.provider_paper_id); else next.add(item.provider_paper_id); return next; })} className={`mt-1 grid h-5 w-5 shrink-0 place-items-center rounded border disabled:cursor-not-allowed disabled:opacity-50 ${selected ? "border-[#164f3b] bg-[#164f3b] text-white" : "border-[#8aa396]"}`}>{selected && <CheckIcon className="h-3.5 w-3.5" />}</button><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-[#e7f0eb] px-2 py-0.5 text-[10px] font-bold text-[#35634f]">要旨のみ</span>{item.year && <span className="text-xs text-[#68736f]">{item.year}</span>}<span className="text-xs text-[#68736f]">被引用 {item.citation_count}</span>{existing && <span className="rounded-full bg-[#f1f3ee] px-2 py-0.5 text-[10px] font-semibold text-[#52605b]">登録済み</span>}</div><h3 className="serif mt-2 text-lg font-semibold leading-snug text-[#17201d]">{item.title}</h3><p className="mt-2 text-xs text-[#68736f]">{item.authors?.length ? item.authors.join(", ") : "著者情報なし"}</p>{(item.venue || item.publication_date) && <p className="mt-1 text-xs text-[#68736f]">{[item.venue, item.publication_date].filter(Boolean).join(" · ")}</p>}<button type="button" onClick={() => setExpandedIds((current) => { const next = new Set(current); if (next.has(item.provider_paper_id)) next.delete(item.provider_paper_id); else next.add(item.provider_paper_id); return next; })} className="mt-3 text-xs font-semibold text-[#164f3b] underline underline-offset-2">{expanded ? "要旨を閉じる" : "要旨を開く"}</button>{expanded && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#52605b]">{item.abstract || "要旨は提供されていません。"}</p>}{item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-xs font-semibold text-[#164f3b] underline underline-offset-2">Semantic Scholarで開く<span className="sr-only">（新しいタブ）</span></a>}</div></div></article>; })}</div> : <p className="mt-5 rounded-2xl bg-white/70 p-6 text-center text-sm text-[#68736f]">条件に一致する論文は見つかりませんでした。検索語や年の範囲を変えてください。</p>}
      <nav aria-label="外部論文検索のページ送り" className="mt-5 flex items-center justify-end gap-3 text-sm"><button type="button" onClick={() => void runSearch(cursorStack.length > 1 ? cursorStack[cursorStack.length - 2] : undefined, cursorStack.slice(0, -1))} disabled={!canWrite || searching || !cursorStack.length} className="rounded-lg border border-[#9ab6a6] bg-white px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50">前へ</button><span aria-current="page">{pageNumber}ページ目</span><button type="button" onClick={() => page.next_cursor && void runSearch(page.next_cursor, [...cursorStack, page.next_cursor])} disabled={!canWrite || searching || !page.next_cursor} className="rounded-lg border border-[#9ab6a6] bg-white px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50">次へ</button></nav>
    </>}{!page && !searching && searched && !error && <p className="mt-5 rounded-2xl bg-white/70 p-6 text-center text-sm text-[#68736f]">検索結果を表示できませんでした。もう一度お試しください。</p>}
  </section>;
}

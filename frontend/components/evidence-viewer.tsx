"use client";

import {
  ArrowLeftIcon, ArrowRightIcon, DocumentTextIcon, SparklesIcon, XMarkIcon,
} from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import {
  createNote, deleteNote, generatePaperSummary, getAssetFile, getPaperChunk, getPaperDetail, getPaperFile, getPaperPage, listAssets, listNotes, updateNote,
  type Chunk, type Citation, type DocumentElement, type Note, type PaperDetail, type PaperMarkdownSummary, type PaperPage,
} from "@/lib/api/client";
import { apiErrorMessage, toApiError } from "@/lib/api/error";
import { normalizeResearchMarkdown } from "@/lib/markdown";

export type EvidenceTarget = {
  paperId: string;
  paperTitle?: string;
  page?: number;
  chunkId?: string;
};

type EvidenceViewerProps = {
  target: EvidenceTarget;
  canWrite: boolean;
  onClose: () => void;
};

const FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), iframe, [tabindex]:not([tabindex='-1'])";

export function EvidenceViewer({ target, canWrite, onClose }: EvidenceViewerProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [paperPage, setPaperPage] = useState<PaperPage | null>(null);
  const [focusedChunk, setFocusedChunk] = useState<Chunk | null>(null);
  const [page, setPage] = useState(Math.max(1, target.page ?? 1));
  const [chunkId, setChunkId] = useState(target.chunkId);
  const [loadingDetail, setLoadingDetail] = useState(true);
  const [loadingPage, setLoadingPage] = useState(true);
  const [loadingFile, setLoadingFile] = useState(false);
  const [fileBlobUrl, setFileBlobUrl] = useState<string | null>(null);
  const [fileError, setFileError] = useState("");
  const [error, setError] = useState("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [notesLoading, setNotesLoading] = useState(true);
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteError, setNoteError] = useState("");
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null);
  const [assets, setAssets] = useState<DocumentElement[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [assetError, setAssetError] = useState("");
  const [paperSummary, setPaperSummary] = useState<PaperMarkdownSummary | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryError, setSummaryError] = useState("");
  const summaryAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setPage(Math.max(1, target.page ?? 1));
    setChunkId(target.chunkId);
  }, [target]);

  useEffect(() => {
    summaryAbortRef.current?.abort();
    setPaperSummary(null); setSummaryBusy(false); setSummaryError("");
  }, [target.paperId]);

  useEffect(() => () => summaryAbortRef.current?.abort(), []);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!focusable.length) { event.preventDefault(); return; }
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    const move = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "ArrowLeft" && page > 1) { event.preventDefault(); movePage(page - 1); }
      if (event.key === "ArrowRight" && detail && page < detail.page_count) { event.preventDefault(); movePage(page + 1); }
    };
    document.addEventListener("keydown", move); return () => document.removeEventListener("keydown", move);
  }, [page, detail]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingDetail(true); setDetail(null); setError("");
    getPaperDetail(target.paperId, controller.signal)
      .then(setDetail)
      .catch(requestError => {
        const normalized = toApiError(requestError, "論文詳細を取得できませんでした");
        if (normalized.code !== "aborted") setError(`原典または抽出spanを開けませんでした: ${normalized.message}。原本が削除・移動された場合は、取り込み履歴を確認してください。`);
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingDetail(false); });
    return () => controller.abort();
  }, [target.paperId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingPage(true); setPaperPage(null); setFocusedChunk(null); setError("");
    const requests: [Promise<PaperPage>, Promise<Chunk | null>] = [
      getPaperPage(target.paperId, page, controller.signal),
      chunkId ? getPaperChunk(target.paperId, chunkId, controller.signal) : Promise.resolve(null),
    ];
    Promise.all(requests)
      .then(([nextPage, nextChunk]) => { setPaperPage(nextPage); setFocusedChunk(nextChunk); })
      .catch(requestError => {
        const normalized = toApiError(requestError, "ページの根拠を取得できませんでした");
        if (normalized.code !== "aborted") setError(normalized.message);
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingPage(false); });
    return () => controller.abort();
  }, [target.paperId, page, chunkId]);

  useEffect(() => {
    if (!detail?.storage_key || !detail.mime_type) return;
    const controller = new AbortController(); let objectUrl: string | null = null;
    setLoadingFile(true); setFileBlobUrl(null); setFileError("");
    getPaperFile(target.paperId, controller.signal)
      .then(blob => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob); setFileBlobUrl(objectUrl);
      })
      .catch(requestError => {
        const normalized = toApiError(requestError, "原本ファイルを取得できませんでした");
        if (normalized.code !== "aborted") setFileError(normalized.message);
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingFile(false); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [detail?.storage_key, detail?.mime_type, target.paperId]);

  useEffect(() => {
    const controller = new AbortController(); setNotesLoading(true); setNoteError("");
    listNotes(target.paperId, controller.signal).then(setNotes).catch(requestError => {
      if (!controller.signal.aborted) setNoteError(apiErrorMessage(requestError, "ノートを取得できませんでした"));
    }).finally(() => { if (!controller.signal.aborted) setNotesLoading(false); });
    return () => controller.abort();
  }, [target.paperId]);

  useEffect(() => {
    const controller = new AbortController(); setAssetsLoading(true); setAssets([]); setAssetError("");
    listAssets(target.paperId, controller.signal).then(setAssets).catch(requestError => {
      const normalized = toApiError(requestError, "文書要素を取得できませんでした");
      if (normalized.code !== "aborted") setAssetError(normalized.message);
    }).finally(() => { if (!controller.signal.aborted) setAssetsLoading(false); });
    return () => controller.abort();
  }, [target.paperId]);

  const isPdf = detail?.mime_type === "application/pdf";
  const hasOriginal = Boolean(detail?.storage_key && detail?.mime_type);
  const fileUrl = useMemo(() => fileBlobUrl ? `${fileBlobUrl}#page=${page}` : null, [fileBlobUrl, page]);
  const title = detail?.title || target.paperTitle || "論文の根拠";
  const chunks = paperPage?.chunks ?? [];
  const extractedText = paperPage?.text || chunks.map(item => item.text).join("\n\n");
  const pageElements = (paperPage?.elements?.length ? paperPage.elements : assets.filter(item => item.page === page));
  const visibleElements = pageElements.filter(item => item.kind === "table" || item.kind === "figure").slice(0, 50);
  const summaryCitations = paperSummary?.citations ?? [];

  function movePage(nextPage: number) {
    setChunkId(undefined);
    setPage(nextPage);
  }

  const generateSummary = async () => {
    if (!canWrite || summaryBusy) return;
    summaryAbortRef.current?.abort();
    const controller = new AbortController(); summaryAbortRef.current = controller;
    setSummaryBusy(true); setSummaryError("");
    try {
      setPaperSummary(await generatePaperSummary(target.paperId, controller.signal));
    } catch (requestError) {
      const normalized = toApiError(requestError, "論文要約を生成できませんでした");
      if (normalized.code !== "aborted") setSummaryError(normalized.message);
    } finally {
      if (summaryAbortRef.current === controller) summaryAbortRef.current = null;
      if (!controller.signal.aborted) setSummaryBusy(false);
    }
  };

  const openSummaryCitation = (citation: Citation) => {
    setChunkId(citation.chunk_id);
    setPage(citation.page);
  };

  const saveNote = async (event: FormEvent) => {
    event.preventDefault(); if (!canWrite || !noteTitle.trim()) return; setNoteBusy(true); setNoteError("");
    try {
      if (editingNoteId) {
        const updated = await updateNote(editingNoteId, noteTitle.trim(), noteContent);
        setNotes(current => current.map(note => note.id === updated.id ? updated : note));
      } else {
        const created = await createNote(target.paperId, noteTitle.trim(), noteContent); setNotes(current => [created, ...current]);
      }
      setEditingNoteId(null); setNoteTitle(""); setNoteContent("");
    } catch (requestError) { setNoteError(apiErrorMessage(requestError, "ノートを保存できませんでした")); }
    finally { setNoteBusy(false); }
  };

  const removeNote = async (noteId: string) => {
    if (!canWrite) return; setNoteBusy(true); setNoteError("");
    try { await deleteNote(noteId); setNotes(current => current.filter(note => note.id !== noteId)); if (editingNoteId === noteId) { setEditingNoteId(null); setNoteTitle(""); setNoteContent(""); } }
    catch (requestError) { setNoteError(apiErrorMessage(requestError, "ノートを削除できませんでした")); }
    finally { setNoteBusy(false); }
  };

  return <div className="fixed inset-0 z-[105] isolate flex justify-end bg-[#06100c]/80 backdrop-blur-[3px]" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="evidence-viewer-title" className="flex h-[100dvh] w-full max-w-[1320px] flex-col overflow-hidden border-l border-white/20 bg-[#f6f4ee] shadow-[-30px_0_90px_rgba(0,0,0,.48)] md:w-[94vw] md:rounded-l-3xl">
      <header className="flex shrink-0 items-start justify-between gap-4 border-b border-[#cdd3ce] bg-[#fffefa] px-4 py-4 shadow-sm md:px-6">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[.2em] text-[#35634f]">Right-side PDF / text viewer · p. {page}</p>
          <h2 id="evidence-viewer-title" className="serif mt-1 truncate text-xl font-semibold md:text-2xl">{title}</h2>
          <p className="mt-1 text-[10px] font-medium text-[#68736f]">左でPDF原本、右で検索に使われた抽出本文と引用箇所を確認できます</p>
        </div>
        <button ref={closeRef} onClick={onClose} aria-label="根拠ビューアを閉じる" className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-[#d8dad4] bg-white text-[#52605b] hover:bg-[#edf0eb] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><XMarkIcon className="h-5 w-5" /></button>
      </header>

      {error ? <div className="m-5 rounded-2xl border border-red-200 bg-red-50 p-5 text-sm text-red-800" role="alert"><p>{error}</p><button onClick={onClose} className="mt-4 rounded-full border border-red-300 px-4 py-2 font-semibold">閉じる</button></div> :
      <div className={`grid min-h-0 flex-1 ${isPdf && hasOriginal ? "lg:grid-cols-[minmax(0,1fr)_260px_minmax(340px,.65fr)]" : "grid-cols-1"}`}>
        {isPdf && hasOriginal && <section aria-label={`PDF原本 ${page}ページ`} className="relative min-h-[42vh] border-b border-[#d9dbd4] bg-[#343c38] lg:min-h-0 lg:border-b-0 lg:border-r">
          <div className="pointer-events-none absolute left-3 top-3 z-10 rounded-full bg-[#10231b] px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-white shadow-lg">PDF原本 · スクロール可能</div>
          {loadingDetail || loadingFile ? <Loading label="認証済みPDFを準備しています" /> : fileUrl ? <iframe key={fileUrl} src={fileUrl} title={`${title} ${page}ページ`} className="h-full min-h-[42vh] w-full bg-white lg:min-h-0" /> : <div role="alert" className="grid h-full min-h-[42vh] place-items-center p-8 text-center text-sm text-red-100">{fileError || "PDFを表示できませんでした"}</div>}
        </section>}
        {isPdf && hasOriginal && <aside aria-label="引用一覧" className="hidden min-h-0 overflow-y-auto border-r border-[#d9dbd4] bg-[#edf0eb] p-3 lg:block"><h3 className="text-xs font-bold uppercase tracking-wider text-[#52605b]">Citation / span</h3><p className="mt-1 text-[10px] leading-4 text-[#68736f]">引用を選ぶと、右の抽出原文へ移動します。</p><div className="mt-3 space-y-2">{chunks.map((item, index) => <button type="button" key={item.id} onClick={() => setFocusedChunk(item)} className={`w-full rounded-xl border p-3 text-left text-xs ${item.id === focusedChunk?.id ? "border-[#5d9878] bg-[#e3f1e8]" : "border-[#d4dbd5] bg-white"}`}><span className="font-bold text-[#a06a28]">[{index + 1}] {item.section}</span><p className="mt-1 line-clamp-4 leading-5 text-[#52605b]">{item.text}</p></button>)}{!chunks.length && <p className="rounded-xl border border-dashed p-3 text-xs text-[#68736f]">このページに引用可能な抽出spanはありません。</p>}</div></aside>}
        <section aria-label="抽出された根拠テキスト" className="min-h-0 overflow-y-auto overscroll-contain p-5 md:p-7">
          <div className="sticky top-0 z-10 -mx-5 -mt-5 mb-5 flex items-center justify-between gap-3 border-b border-[#d9dbd4] bg-[#f6f4ee]/95 px-5 py-4 shadow-sm backdrop-blur md:-mx-7 md:-mt-7 md:px-7">
            <div><div className="flex items-center gap-2"><DocumentTextIcon className="h-5 w-5 text-[#164f3b]" /><h3 className="font-semibold">抽出テキスト</h3></div>{paperPage && <p className="mt-1 text-[10px] text-[#68736f]">{paperPage.text_source === "ocr" ? "OCR" : paperPage.text_source === "native" ? "原文抽出" : "テキストなし"} · 品質 {Math.round(paperPage.quality * 100)}%</p>}</div>
            <div className="flex items-center gap-2">
              <button onClick={() => movePage(page - 1)} disabled={page <= 1 || loadingPage} aria-label="前のページ" className="grid h-9 w-9 place-items-center rounded-full border border-[#d5d8d2] bg-white disabled:opacity-35"><ArrowLeftIcon className="h-4 w-4" /></button>
              <span className="min-w-16 text-center text-xs text-[#68736f]">{page} / {detail?.page_count || "–"}</span>
              <button onClick={() => movePage(page + 1)} disabled={loadingPage || !detail || page >= detail.page_count} aria-label="次のページ" className="grid h-9 w-9 place-items-center rounded-full border border-[#d5d8d2] bg-white disabled:opacity-35"><ArrowRightIcon className="h-4 w-4" /></button>
            </div>
          </div>

          {loadingPage ? <Loading label="ページの根拠を読み込んでいます" /> : <>
            {paperPage?.text_source === "none" && <p role="alert" className="mb-4 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-900">抽出テキストがありません。原本PDFを確認するか、OCR設定・取り込み結果を運用者に確認してください。</p>}
            {paperPage?.text_source === "ocr" && paperPage.quality < 0.7 && <p role="alert" className="mb-4 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-900">低品質OCR（{Math.round(paperPage.quality * 100)}%）です。引用・数値・数式は必ず原本PDFと照合してください。</p>}
            {focusedChunk && <div className="mb-5 rounded-2xl border-2 border-[#6f9d86] bg-[#eaf3ee] p-4" aria-label="引用された箇所"><p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-[#35634f]">Selected evidence · {focusedChunk.section}</p><p className="whitespace-pre-wrap text-sm leading-7">{focusedChunk.text}</p></div>}
            {chunks.length ? <div className="space-y-4">{chunks.map(item => <article key={item.id} className={`rounded-2xl border p-4 ${item.id === focusedChunk?.id ? "border-[#6f9d86] bg-[#eef6f1]" : "border-[#deddd5] bg-white/70"}`}><p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-[#7a837f]">{item.section}</p><p className="whitespace-pre-wrap text-sm leading-7 text-[#38443f]">{item.text}</p></article>)}</div> : extractedText ? <p className="whitespace-pre-wrap text-sm leading-7">{extractedText}</p> : <p className="rounded-2xl bg-white/65 p-5 text-sm text-[#68736f]">このページから抽出されたテキストはありません。</p>}
            <section aria-labelledby="document-elements-title" className="mt-8"><h3 id="document-elements-title" className="serif text-xl font-semibold">表・図版</h3>{assetsLoading ? <p role="status" className="mt-3 text-xs text-[#68736f]">文書要素を読み込んでいます…</p> : assetError ? <p role="alert" className="mt-3 text-xs text-red-700">{assetError}</p> : visibleElements.length ? <div className="mt-4 space-y-5">{visibleElements.map(element => element.kind === "table" ? <SafeTable key={element.id} element={element}/> : <AssetFigure key={element.id} paperId={target.paperId} element={element} caption={captionFor(pageElements, element)}/>)}</div> : <p className="mt-3 text-xs text-[#68736f]">このページに抽出済みの表・図版はありません。</p>}{pageElements.filter(item => item.kind === "table" || item.kind === "figure").length > 50 && <p className="mt-3 text-xs text-amber-800">表示上限50件まで表示しています。</p>}{assets.length > 100 && <p className="mt-2 text-xs text-amber-800">文書全体の要素が多いため、現在ページのみを優先表示しています。</p>}</section>
            {!isPdf && hasOriginal && (loadingFile ? <p className="mt-5 text-xs text-[#68736f]">認証済み原本を準備しています…</p> : fileUrl ? <a href={fileUrl} target="_blank" rel="noreferrer" className="mt-5 inline-flex rounded-full border border-[#164f3b] px-4 py-2 text-xs font-semibold text-[#164f3b]">原本ファイルを開く</a> : fileError && <p role="alert" className="mt-5 text-xs text-red-700">{fileError}</p>)}
          </>}
          <section aria-labelledby="paper-summary-title" className="mt-8 border-t border-[#d8dad4] pt-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><SparklesIcon className="h-5 w-5 text-[#35634f]"/><h3 id="paper-summary-title" className="serif text-xl font-semibold">論文の日本語要約</h3></div><p className="mt-1 text-xs leading-5 text-[#68736f]">本文から日本語で要点を整理します。数式はLaTeX、表はMarkdownで読みやすく表示します。</p></div>{canWrite && <button type="button" onClick={() => void generateSummary()} disabled={summaryBusy} className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white hover:bg-[#245b45] disabled:cursor-wait disabled:opacity-55"><SparklesIcon className={`h-3.5 w-3.5 ${summaryBusy ? "animate-pulse" : ""}`}/>{summaryBusy ? "要約を作成中…" : paperSummary ? "もう一度要約" : "AIで日本語要約"}</button>}</div>{!canWrite && <p className="mt-3 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-800">viewer権限では要約生成を実行できません。</p>}
            {summaryBusy && <div role="status" className="mt-4 rounded-2xl border border-[#d8e7dd] bg-[#f1f7f3] p-4 text-sm text-[#35634f]">本文と抽出箇所を読み込み、要約を生成しています。論文が長い場合は少し時間がかかります。</div>}
            {summaryError && <div role="alert" className="mt-4 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-800"><p>{summaryError}</p><button type="button" onClick={() => void generateSummary()} disabled={!canWrite || summaryBusy} className="mt-3 rounded-full border border-red-300 px-3 py-1.5 text-xs font-semibold disabled:opacity-40">再試行</button></div>}
            {paperSummary && <div className="mt-4 rounded-2xl border border-[#d8ded9] bg-white/80 p-4 md:p-5"><div className="flex flex-wrap items-center gap-2 border-b border-[#e2e5e0] pb-3 text-[10px]"><span className={`rounded-full px-2.5 py-1 font-bold ${paperSummary.generation_mode === "llm" ? "bg-[#dfeee6] text-[#164f3b]" : "bg-amber-50 text-amber-800"}`}>{paperSummary.generation_mode === "llm" ? "LLMによる日本語要約" : "ローカル要約（LLM未使用）"}</span>{paperSummary.model && <span className="text-[#68736f]">モデル: {paperSummary.model}</span>}</div>{paperSummary.generation_mode === "local_fallback" && <p className="mt-3 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-900">LLMを利用できなかったため、抽出済みの本文から要約を作成しました。{paperSummary.fallback_reason ? ` 理由: ${paperSummary.fallback_reason}` : ""}</p>}<PaperSummaryMarkdown content={paperSummary.summary}/>{summaryCitations.length > 0 && <div className="mt-5 border-t border-[#e2e5e0] pt-4"><p className="text-xs font-bold text-[#35634f]">要約の根拠箇所</p><div className="mt-2 grid gap-2 sm:grid-cols-2">{summaryCitations.map(citation => <button type="button" key={`${citation.chunk_id}-${citation.index}`} onClick={() => openSummaryCitation(citation)} className="rounded-xl border border-[#d8ded9] bg-[#fbfcfa] p-3 text-left hover:border-[#6f9d86]"><p className="text-[10px] font-bold text-[#a06a28]">{citation.section} · p. {citation.page}</p><p className="mt-1 line-clamp-3 text-xs leading-5 text-[#52605b]">{citation.excerpt}</p><span className="mt-2 inline-block text-[10px] font-bold text-[#35634f]">本文の該当箇所を開く</span></button>)}</div></div>}</div>}
          </section>
          <section aria-labelledby="paper-notes-title" className="mt-8 border-t border-[#d8dad4] pt-6"><h3 id="paper-notes-title" className="serif text-xl font-semibold">論文ノート</h3>{!canWrite && <p className="mt-2 text-xs text-amber-800">viewer権限ではノートを編集できません。</p>}
            {canWrite && <form onSubmit={saveNote} className="mt-4 space-y-2"><label htmlFor="note-title" className="sr-only">ノートタイトル</label><input id="note-title" maxLength={255} value={noteTitle} onChange={event => setNoteTitle(event.target.value)} placeholder="ノートタイトル" className="w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/><label htmlFor="note-content" className="sr-only">ノート本文</label><textarea id="note-content" maxLength={100000} rows={4} value={noteContent} onChange={event => setNoteContent(event.target.value)} placeholder="考察や再現メモを入力" className="w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/><div className="flex gap-2"><button disabled={noteBusy || !noteTitle.trim()} className="rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">{editingNoteId ? "更新" : "ノートを追加"}</button>{editingNoteId && <button type="button" onClick={() => { setEditingNoteId(null); setNoteTitle(""); setNoteContent(""); }} className="text-xs text-[#68736f]">取消</button>}</div></form>}
            {noteError && <p role="alert" className="mt-3 text-xs text-red-700">{noteError}</p>}
            <div className="mt-4 space-y-3">{notesLoading ? <p role="status" className="text-xs text-[#68736f]">ノートを読み込んでいます…</p> : notes.length ? notes.map(note => <article key={note.id} className="rounded-2xl border border-[#deddd5] bg-white/70 p-4"><div className="flex items-start justify-between gap-3"><div><h4 className="text-sm font-semibold">{note.title}</h4><p className="mt-1 text-[10px] text-[#89918e]">更新 {new Date(note.updated_at).toLocaleString("ja-JP")}</p></div>{canWrite && <div className="flex gap-2"><button onClick={() => { setEditingNoteId(note.id); setNoteTitle(note.title); setNoteContent(note.content); }} className="text-xs text-[#164f3b]">編集</button><button disabled={noteBusy} onClick={() => removeNote(note.id)} className="text-xs text-red-700">削除</button></div>}</div><p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-[#52605b]">{note.content}</p></article>) : <p className="text-xs text-[#68736f]">この論文のノートはまだありません。</p>}</div>
          </section>
        </section>
      </div>}
    </div>
  </div>;
}

function Loading({ label }: { label: string }) {
  return <div role="status" className="grid h-full min-h-48 place-items-center p-8 text-center text-sm text-[#68736f]"><div><div className="mx-auto mb-3 h-7 w-7 animate-spin rounded-full border-2 border-[#b9c8c0] border-t-[#164f3b]"/><span>{label}</span></div></div>;
}

function PaperSummaryMarkdown({ content }: { content: string }) {
  return <div className="research-markdown mt-4 min-w-0 text-sm leading-7 text-[#26342e]">
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError:false, strict:"warn" }]]}
      components={{
        h1: ({ children }) => <h4 className="serif mb-2 mt-5 text-lg font-semibold first:mt-0">{children}</h4>,
        h2: ({ children }) => <h5 className="serif mb-2 mt-5 text-base font-semibold first:mt-0">{children}</h5>,
        h3: ({ children }) => <h6 className="mb-2 mt-4 text-sm font-bold first:mt-0">{children}</h6>,
        p: ({ children }) => <p className="my-2">{children}</p>,
        ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-5">{children}</ul>,
        ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-5">{children}</ol>,
        blockquote: ({ children }) => <blockquote className="my-3 border-l-4 border-[#9db9aa] bg-[#eef4f0] px-4 py-2 text-[#40534a]">{children}</blockquote>,
        pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-xl bg-[#10231b] p-4 text-xs leading-6 text-[#e5f0ea]">{children}</pre>,
        code: ({ children, className }) => className ? <code className={className}>{children}</code> : <code className="rounded bg-[#e7ebe7] px-1.5 py-0.5 font-mono text-[.9em] text-[#234538]">{children}</code>,
        table: ({ children }) => <div className="my-4 overflow-x-auto"><table className="min-w-full border-collapse text-left text-xs">{children}</table></div>,
        th: ({ children }) => <th className="border border-[#ccd5cf] bg-[#eaf0ec] px-3 py-2 font-bold">{children}</th>,
        td: ({ children }) => <td className="border border-[#d8ddd9] px-3 py-2 align-top">{children}</td>,
        img: ({ alt }) => <span role="img" aria-label={alt || "外部画像"} className="inline-flex rounded bg-[#f1eee8] px-2 py-1 text-xs text-[#6d6459]">画像は安全のため表示していません{alt ? `: ${alt}` : ""}</span>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer noopener" className="font-semibold text-[#176143] underline decoration-[#8cb9a4] underline-offset-4">{children}</a>,
      }}
    >{normalizeResearchMarkdown(content)}</ReactMarkdown>
  </div>;
}

function captionFor(elements: DocumentElement[], figure: DocumentElement): string {
  const index = elements.findIndex(item => item.id === figure.id);
  const nearby = elements.slice(Math.max(0, index - 1), index + 3).find(item => item.kind === "caption");
  return nearby?.text || figure.text;
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value).slice(0, 500);
  try { return JSON.stringify(value).slice(0, 500); } catch { return "[表示できない値]"; }
}

function tableData(element: DocumentElement): { headers: string[]; rows: string[][]; truncated: boolean } {
  const data = element.structured_data;
  let headers: string[] = []; let rows: string[][] = [];
  if (Array.isArray(data)) {
    if (data.every(row => Array.isArray(row))) rows = data.map(row => (row as unknown[]).map(cellText));
    else if (data.every(row => row && typeof row === "object" && !Array.isArray(row))) {
      headers = Array.from(new Set(data.flatMap(row => Object.keys(row as Record<string, unknown>))));
      rows = data.map(row => headers.map(key => cellText((row as Record<string, unknown>)[key])));
    }
  } else if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    if (Array.isArray(record.headers) && Array.isArray(record.rows)) {
      headers = record.headers.map(cellText);
      rows = record.rows.filter(Array.isArray).map(row => (row as unknown[]).map(cellText));
    } else {
      headers = Object.keys(record); rows = [headers.map(key => cellText(record[key]))];
    }
  }
  if (!rows.length && element.text.includes("|")) {
    const markdownRows = element.text.split(/\r?\n/).map(line => line.trim()).filter(line => line.includes("|")).map(line => line.replace(/^\||\|$/g, "").split("|").map(cell => cell.trim()));
    const filtered = markdownRows.filter(row => !row.every(cell => /^:?-{3,}:?$/.test(cell)));
    if (filtered.length > 1) { headers = filtered[0]; rows = filtered.slice(1); } else rows = filtered;
  }
  const truncated = rows.length > 100 || headers.length > 20 || rows.some(row => row.length > 20);
  return { headers:headers.slice(0,20), rows:rows.slice(0,100).map(row => row.slice(0,20)), truncated };
}

function SafeTable({ element }: { element: DocumentElement }) {
  const data = tableData(element);
  return <figure className="overflow-hidden rounded-2xl border border-[#d8dad4] bg-white/75"><div className="overflow-x-auto">{data.rows.length ? <table className="w-full min-w-[420px] border-collapse text-left text-xs">{data.headers.length > 0 && <thead><tr className="bg-[#eef1ec]">{data.headers.map((header,index) => <th key={index} scope="col" className="border-b border-[#d8dad4] p-3 font-semibold">{header}</th>)}</tr></thead>}<tbody>{data.rows.map((row,rowIndex) => <tr key={rowIndex} className="border-b border-[#e7e6df] last:border-0">{row.map((cell,cellIndex) => <td key={cellIndex} className="max-w-72 p-3 align-top leading-5">{cell}</td>)}</tr>)}</tbody></table> : <p className="p-4 whitespace-pre-wrap text-xs leading-6 text-[#52605b]">{element.text || "表データは空です。"}</p>}</div>{data.truncated && <figcaption className="border-t border-[#d8dad4] p-3 text-[10px] text-amber-800">安全な表示上限（100行・20列）まで表示しています。</figcaption>}</figure>;
}

function AssetFigure({ paperId, element, caption }: { paperId: string; element: DocumentElement; caption: string }) {
  const [url, setUrl] = useState<string | null>(null); const [error, setError] = useState("");
  useEffect(() => {
    if (!element.asset_key) return;
    const controller = new AbortController(); let objectUrl: string | null = null; setUrl(null); setError("");
    getAssetFile(paperId, element.id, controller.signal).then(blob => {
      if (controller.signal.aborted) return; objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(requestError => {
      const normalized = toApiError(requestError, "図版を取得できませんでした"); if (normalized.code !== "aborted") setError(normalized.message);
    });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [paperId, element.id, element.asset_key]);
  return <figure className="rounded-2xl border border-[#d8dad4] bg-white/75 p-4">{url ? <img src={url} alt={caption || `論文${element.page}ページの抽出図版`} className="mx-auto max-h-[520px] max-w-full object-contain"/> : error ? <p role="alert" className="text-xs text-red-700">{error}</p> : element.asset_key ? <p role="status" className="text-xs text-[#68736f]">図版を読み込んでいます…</p> : <p className="text-xs text-[#68736f]">図版ファイルはありません。</p>}{caption && <figcaption className="mt-3 text-xs leading-6 text-[#52605b]">{caption}</figcaption>}</figure>;
}

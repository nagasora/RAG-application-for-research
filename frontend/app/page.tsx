"use client";

import {
  ArrowUpTrayIcon, BeakerIcon, BookOpenIcon, CheckIcon, ChevronRightIcon,
  DocumentMagnifyingGlassIcon, LightBulbIcon, MagnifyingGlassIcon, SparklesIcon,
  TrashIcon, XMarkIcon,
} from "@heroicons/react/24/outline";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addExternalPaper, comparePapers, deletePaper, findResearchGaps, listPapers, pollIngestionJob, saveComparison, uploadPapers,
  type Citation, type CompareRow, type Gap, type Paper, type UploadResult,
} from "@/lib/api/client";
import { apiErrorMessage, toApiError, type ApiError } from "@/lib/api/error";
import { streamSearch } from "@/lib/api/search-stream";
import { EvidenceViewer, type EvidenceTarget } from "@/components/evidence-viewer";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { useWorkspaceSession } from "@/lib/session/workspace-session";
import { ResearchWorkspace } from "@/components/research-workspace";
import { LatestRequestCoordinator } from "@/lib/api/request-coordinator.mjs";

type View = "library" | "ask" | "analysis" | "research";
type SearchReplay = { query: string; paperIds: string[]; revision: number };

function Logo() {
  return <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-full bg-[#164f3b] text-white"><BookOpenIcon className="h-5 w-5" /></div><div><div className="serif text-xl font-bold tracking-tight">PaperPilot</div><div className="text-[10px] uppercase tracking-[.22em] text-[#7a837f]">Research, grounded</div></div></div>;
}

function Empty({ icon: Icon, title, body }: { icon: typeof BookOpenIcon; title: string; body: string }) {
  return <div className="paper-card flex min-h-72 flex-col items-center justify-center rounded-3xl p-10 text-center"><div className="mb-5 rounded-full bg-[#e5eee8] p-4 text-[#164f3b]"><Icon className="h-7 w-7" /></div><h3 className="serif text-2xl font-semibold">{title}</h3><p className="mt-2 max-w-md text-sm leading-7 text-[#68736f]">{body}</p></div>;
}

export default function Home() {
  const session = useWorkspaceSession();
  const [view, setView] = useState<View>("library");
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState("");
  const [externalId, setExternalId] = useState("");
  const [uploadResults, setUploadResults] = useState<UploadResult[]>([]);
  const [evidenceTarget, setEvidenceTarget] = useState<EvidenceTarget | null>(null);
  const [searchReplay, setSearchReplay] = useState<SearchReplay | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const papersRequestRef = useRef<LatestRequestCoordinator | null>(null);
  if (!papersRequestRef.current) papersRequestRef.current = new LatestRequestCoordinator();
  useEffect(() => () => uploadAbortRef.current?.abort(), []);

  const loadPapers = useCallback(async () => {
    if (session.status !== "ready") return;
    const request = papersRequestRef.current!.begin();
    try {
      const nextPapers = await listPapers(request.signal);
      if (request.isCurrent()) setPapers(nextPapers);
    } catch (error) {
      const normalized = toApiError(error, "論文一覧を取得できませんでした");
      if (request.isCurrent() && normalized.code !== "aborted") setNotice(apiErrorMessage(normalized));
    } finally { if (request.isCurrent()) setLoading(false); }
  }, [session.status, session.activeWorkspace?.id]);
  useEffect(() => {
    papersRequestRef.current!.cancel(); uploadAbortRef.current?.abort(); setUploading(false);
    if (session.status !== "ready") return () => papersRequestRef.current!.cancel();
    setLoading(true); setPapers([]); setSelected([]); setEvidenceTarget(null); setSearchReplay(null); setUploadResults([]); setNotice(""); setView("library"); loadPapers();
    return () => papersRequestRef.current!.cancel();
  }, [loadPapers, session.status, session.activeWorkspace?.id]);

  const upload = async (files: FileList | File[]) => {
    if (!files.length || uploading) return;
    const selectedFiles = Array.from(files);
    const controller = new AbortController(); uploadAbortRef.current = controller;
    setUploading(true); setNotice(""); setUploadResults([]);
    try {
      const results = await uploadPapers(selectedFiles, controller.signal);
      setUploadResults(results);
      await loadPapers();
      const completed = await Promise.all(results.map(async (result, index): Promise<UploadResult> => {
        if (result.status !== "processing" || !result.job) return result;
        try {
          const job = await pollIngestionJob(result.job.id, controller.signal, update => {
            setUploadResults(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, job:update } : item));
          });
          const next: UploadResult = job.status === "succeeded"
            ? { ...result, success:true, status:"ready", job, paper:result.paper ? { ...result.paper, status:"ready" } : result.paper }
            : { ...result, success:false, status:"failed", job, error:job.error_message || "論文解析に失敗しました" };
          setUploadResults(current => current.map((item, itemIndex) => itemIndex === index ? next : item));
          return next;
        } catch (pollError) {
          const normalized = toApiError(pollError, "解析状況を確認できませんでした");
          if (normalized.code === "aborted") throw normalized;
          const next: UploadResult = { ...result, success:false, status:"failed", error:normalized.message };
          setUploadResults(current => current.map((item, itemIndex) => itemIndex === index ? next : item));
          return next;
        }
      }));
      await loadPapers();
      const succeeded = completed.filter(result => result.success && result.status !== "processing").length;
      const failed = completed.length - succeeded;
      setNotice(`${succeeded}件の解析が完了${failed ? `、${failed}件は完了できませんでした` : "しました"}。`);
    } catch (error) {
      const normalized = toApiError(error, "アップロードに失敗しました");
      if (normalized.code !== "aborted") setNotice(apiErrorMessage(normalized));
    }
    finally {
      if (uploadAbortRef.current === controller) { uploadAbortRef.current = null; setUploading(false); }
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const remove = async (id: string) => {
    try {
      await deletePaper(id);
      setSelected(current => current.filter(item => item !== id));
      await loadPapers();
      setNotice("論文を削除しました。");
    } catch (error) { setNotice(apiErrorMessage(error, "論文を削除できませんでした")); }
  };
  const addExternal = async (event: FormEvent) => {
    event.preventDefault(); if (!externalId.trim()) return; setUploading(true); setNotice("");
    try {
      await addExternalPaper(externalId.trim());
      setExternalId(""); await loadPapers(); setNotice("外部データベースから論文メタデータを登録しました。");
    } catch (error) { setNotice(apiErrorMessage(error, "登録に失敗しました")); }
    finally { setUploading(false); }
  };
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]);

  if (session.status === "loading") return <SessionLoading />;
  if (session.status === "error" || !session.me || !session.activeWorkspace) {
    return <SessionErrorState mode={session.mode} error={session.error} onRetry={session.retry} onToken={session.useAccessToken} />;
  }

  return <div className="grain min-h-screen">
    <header className="border-b border-[#d9dbd4] bg-[#fffefa]/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1440px] items-center justify-between px-5 py-4 lg:px-10">
        <Logo />
        <nav className="hidden items-center rounded-full border border-[#d8dad4] bg-white p-1 md:flex">
          {(["library", "ask", "analysis", "research"] as View[]).map(item => <button key={item} onClick={() => setView(item)} className={`rounded-full px-4 py-2 text-sm transition ${view === item ? "bg-[#173f32] text-white shadow" : "text-[#65706c] hover:text-[#173f32]"}`}>{item === "library" ? "ライブラリ" : item === "ask" ? "論文に質問" : item === "analysis" ? "比較・発見" : "研究ワークスペース"}</button>)}
        </nav>
        <div className="flex items-center gap-3"><span className="hidden rounded-full bg-[#edf0eb] px-3 py-1.5 text-xs text-[#52605b] xl:block">{papers.length} papers</span><WorkspaceSwitcher me={session.me} workspaces={session.workspaces} activeWorkspace={session.activeWorkspace} creating={session.creating} onSelect={session.selectWorkspace} onCreate={session.createWorkspace} /></div>
      </div>
    </header>

    <main className="mx-auto max-w-[1440px] px-5 py-8 lg:px-10 lg:py-12">
      {notice && <div role="status" aria-live="polite" className="rise mb-6 flex items-center justify-between rounded-2xl border border-[#b9d4c5] bg-[#edf7f1] px-5 py-3 text-sm text-[#23513e]"><span>{notice}</span><button aria-label="通知を閉じる" onClick={() => setNotice("")}><XMarkIcon className="h-4 w-4" /></button></div>}
      {view === "library" && <section className="rise">
        <div className="mb-8 flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><p className="mb-2 text-xs font-bold uppercase tracking-[.2em] text-[#a06a28]">Knowledge library</p><h1 className="serif text-4xl font-semibold tracking-tight md:text-5xl">研究の根拠を、ひとつの場所に。</h1><p className="mt-3 max-w-2xl text-sm leading-7 text-[#68736f]">PDFを追加すると、ページ情報を保持したまま解析・分割され、すぐに検索と比較に使えます。</p></div><button onClick={() => inputRef.current?.click()} className="flex shrink-0 items-center justify-center gap-2 rounded-full bg-[#164f3b] px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-[#164f3b]/15 hover:bg-[#0f3d2e]"><ArrowUpTrayIcon className="h-4 w-4" />論文を追加</button></div>
        <div onDragOver={e => { e.preventDefault(); if (!uploading) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!uploading) upload(e.dataTransfer.files); }} onClick={() => { if (!uploading) inputRef.current?.click(); }} className={`mb-4 rounded-3xl border border-dashed p-7 transition ${uploading ? "cursor-wait opacity-70" : "cursor-pointer"} ${dragging ? "border-[#164f3b] bg-[#e7f2eb]" : "border-[#bbc4be] bg-white/45 hover:border-[#6c887a]"}`}><input ref={inputRef} className="hidden" type="file" multiple accept=".pdf,.txt,.md" onChange={e => e.target.files && upload(e.target.files)} /><div className="flex items-center gap-5"><div className="grid h-12 w-12 place-items-center rounded-2xl bg-white text-[#164f3b] shadow-sm"><ArrowUpTrayIcon className={`h-5 w-5 ${uploading ? "animate-bounce" : ""}`} /></div><div><p className="font-semibold">{uploading ? "論文を解析しています…" : "PDFをここにドロップ"}</p><p className="mt-1 text-xs text-[#7a837f]">複数ファイル対応 · PDF / TXT / Markdown</p></div></div></div>
        {uploadResults.length > 0 && <div aria-label="アップロード結果" aria-live="polite" className="mb-10 space-y-2 rounded-2xl border border-[#deddd5] bg-white/55 p-4">{uploadResults.map((result, index) => <div key={`${result.filename}-${index}`} className="flex items-start justify-between gap-4 text-xs"><div className="min-w-0 flex-1"><p className="truncate font-semibold">{result.filename}</p>{result.status === "processing" && <div className="mt-2"><div className="h-1.5 overflow-hidden rounded-full bg-[#dfe5e1]"><div className="h-full bg-[#164f3b] transition-all" style={{ width:`${Math.max(2, Math.min(100, result.job?.progress ?? 0))}%` }}/></div><p className="mt-1 text-[10px] text-[#68736f]">{result.job?.status === "running" ? "解析中" : "待機中"} · {result.job?.progress ?? 0}%</p></div>}{result.error && <p className="mt-1 text-red-700">{result.error}</p>}</div><span className={`shrink-0 rounded-full px-2.5 py-1 font-bold ${result.status === "processing" ? "bg-amber-50 text-amber-800" : result.success ? "bg-[#e7f0eb] text-[#35634f]" : "bg-red-50 text-red-700"}`}>{result.duplicate ? "登録済み" : result.status === "processing" ? "解析中" : result.success ? "完了" : "失敗"}</span></div>)}</div>}
        <form onSubmit={addExternal} className="mb-10 flex flex-col gap-3 rounded-2xl border border-[#deddd5] bg-white/55 p-4 sm:flex-row sm:items-center"><div className="shrink-0 text-xs font-bold uppercase tracking-wider text-[#68736f]">arXiv / DOI</div><input value={externalId} onChange={e => setExternalId(e.target.value)} placeholder="例：2401.12345 または 10.1000/..." className="min-w-0 flex-1 rounded-xl border border-[#d5d8d2] bg-white px-4 py-2.5 text-sm outline-none focus:border-[#6c887a]"/><button disabled={!externalId.trim() || uploading} className="rounded-full border border-[#164f3b] px-5 py-2.5 text-xs font-semibold text-[#164f3b] disabled:opacity-40">メタデータ取得</button></form>
        <div className="mb-4 flex items-center justify-between"><h2 className="serif text-2xl font-semibold">登録済み論文</h2>{selected.length > 0 && <button onClick={() => setView("analysis")} className="flex items-center gap-2 text-sm font-semibold text-[#164f3b]">選択した{selected.length}件を分析 <ChevronRightIcon className="h-4 w-4" /></button>}</div>
        {loading ? <div className="py-24 text-center text-sm text-[#68736f]">ライブラリを読み込んでいます…</div> : papers.length === 0 ? <Empty icon={DocumentMagnifyingGlassIcon} title="最初の論文を追加しましょう" body="上のエリアへ論文PDFをドロップしてください。APIキーがなくても検索・引用・比較を試せます。" /> : <div className="grid gap-4 xl:grid-cols-2">{papers.map((paper, i) => { const ready = paper.status === "ready"; return <article key={paper.id} className="paper-card group rounded-2xl p-5 transition hover:-translate-y-0.5 hover:shadow-lg" style={{ animationDelay: `${i * 45}ms` }}><div className="flex gap-4"><button disabled={!ready} onClick={() => toggle(paper.id)} aria-label={ready ? `${paper.title}を分析対象に${selected.includes(paper.id) ? "含めない" : "含める"}` : `${paper.title}は解析未完了です`} aria-pressed={selected.includes(paper.id)} className={`mt-1 grid h-5 w-5 shrink-0 place-items-center rounded border disabled:opacity-35 ${selected.includes(paper.id) ? "border-[#164f3b] bg-[#164f3b] text-white" : "border-[#aeb7b1]"}`}>{selected.includes(paper.id) && <CheckIcon className="h-3 w-3" />}</button><div className="min-w-0 flex-1"><div className="mb-3 flex flex-wrap items-center gap-2"><span className="rounded-full bg-[#e7f0eb] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-[#35634f]">{paper.source}</span>{!ready && <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${paper.status === "failed" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-800"}`}>{paper.status === "failed" ? "解析失敗" : "解析中"}</span>}<span className="text-xs text-[#8a918e]">{paper.page_count} pages · {paper.chunk_count} chunks</span></div><button disabled={!ready} onClick={() => setEvidenceTarget({ paperId:paper.id, paperTitle:paper.title, page:1 })} className="block w-full rounded text-left disabled:cursor-default focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#164f3b]"><h3 className={`serif line-clamp-2 text-xl font-semibold leading-snug underline-offset-4 ${ready ? "hover:underline" : ""}`}>{paper.title}</h3><span className="sr-only">{ready ? "論文詳細を開く" : "解析未完了"}</span></button><p className={`mt-2 line-clamp-2 text-sm leading-6 ${paper.status === "failed" ? "text-red-700" : "text-[#68736f]"}`}>{paper.error_message || paper.abstract || (ready ? "本文のメタデータを解析済みです。" : "論文を解析しています。")}</p><div className="mt-4 flex items-center justify-between"><span className="text-xs text-[#89918e]">{paper.authors.join(", ") || "著者情報なし"}{paper.year ? ` · ${paper.year}` : ""}</span><div className="flex items-center gap-1">{ready && <button onClick={() => setEvidenceTarget({ paperId:paper.id, paperTitle:paper.title, page:1 })} className="rounded-full px-3 py-2 text-xs font-semibold text-[#164f3b] hover:bg-[#e7f0eb]">詳細を見る</button>}<button onClick={() => remove(paper.id)} aria-label={`${paper.title}を削除`} className="rounded-full p-2 text-[#9ba19e] opacity-0 transition hover:bg-red-50 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"><TrashIcon className="h-4 w-4" /></button></div></div></div></div></article>; })}</div>}
      </section>}
      {view === "ask" && <AskView key={session.activeWorkspace.id} papers={papers} selected={selected} setSelected={setSelected} openEvidence={setEvidenceTarget} replay={searchReplay} />}
      {view === "analysis" && <AnalysisView key={session.activeWorkspace.id} papers={papers} selected={selected} setSelected={setSelected} openEvidence={setEvidenceTarget} canWrite={session.activeWorkspace.role !== "viewer"} />}
      {view === "research" && <ResearchWorkspace key={session.activeWorkspace.id} papers={papers} canWrite={session.activeWorkspace.role !== "viewer"} exportPaperIds={selected} onReplay={(query, paperIds) => { setSelected(paperIds.filter(id => papers.some(paper => paper.id === id))); setSearchReplay({ query, paperIds, revision:Date.now() }); setView("ask"); }} />}
    </main>
    <div className="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 rounded-full border border-[#d8dad4] bg-white/95 p-1 shadow-xl md:hidden">{(["library", "ask", "analysis", "research"] as View[]).map(item => <button key={item} onClick={() => setView(item)} className={`rounded-full px-3 py-2 text-xs ${view === item ? "bg-[#173f32] text-white" : "text-[#65706c]"}`}>{item === "library" ? "論文" : item === "ask" ? "質問" : item === "analysis" ? "分析" : "整理"}</button>)}</div>
    {evidenceTarget && <EvidenceViewer target={evidenceTarget} canWrite={session.activeWorkspace.role !== "viewer"} onClose={() => setEvidenceTarget(null)} />}
  </div>;
}

function PaperPicker({ papers, selected, setSelected }: { papers: Paper[]; selected: string[]; setSelected: (ids: string[]) => void }) {
  return <div className="flex flex-wrap gap-2">{papers.map(paper => <button key={paper.id} onClick={() => setSelected(selected.includes(paper.id) ? selected.filter(id => id !== paper.id) : [...selected, paper.id])} className={`max-w-64 truncate rounded-full border px-3 py-1.5 text-xs transition ${selected.includes(paper.id) ? "border-[#164f3b] bg-[#e1eee7] text-[#164f3b]" : "border-[#d5d8d2] bg-white text-[#68736f]"}`}>{selected.includes(paper.id) ? "✓ " : "+ "}{paper.title}</button>)}</div>;
}

function AskView({ papers, selected, setSelected, openEvidence, replay }: { papers: Paper[]; selected: string[]; setSelected: (ids: string[]) => void; openEvidence: (target: EvidenceTarget) => void; replay: SearchReplay | null }) {
  const [query, setQuery] = useState(""); const [answer, setAnswer] = useState(""); const [citations, setCitations] = useState<Citation[]>([]); const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);
  const runSearch = async (nextQuery: string, paperIds: string[]) => {
    if (!nextQuery.trim()) return; abortRef.current?.abort(); setBusy(true); setAnswer(""); setCitations([]);
    const controller = new AbortController(); abortRef.current = controller;
    try {
      for await (const event of streamSearch({ query:nextQuery.trim(), paper_ids:paperIds, limit:8 }, controller.signal)) {
        if (event.type === "token") setAnswer(current => current + event.value);
        if (event.type === "citations") setCitations(event.value);
      }
    } catch (error) {
      const normalized = toApiError(error, "回答を生成できませんでした");
      if (normalized.code !== "aborted") setAnswer(normalized.message);
    } finally {
      if (abortRef.current === controller) { abortRef.current = null; setBusy(false); }
    }
  };
  useEffect(() => { if (replay) { setQuery(replay.query); void runSearch(replay.query, selected); } }, [replay?.revision]);
  const ask = async (e: FormEvent) => { e.preventDefault(); await runSearch(query, selected); };
  return <section className="rise"><div className="mx-auto max-w-5xl"><p className="mb-2 text-xs font-bold uppercase tracking-[.2em] text-[#a06a28]">Evidence-first answer</p><h1 className="serif text-4xl font-semibold md:text-5xl">論文に、根拠から答えてもらう。</h1><p className="mt-3 text-sm text-[#68736f]">対象を選ばない場合はライブラリ全体を検索します。</p><div className="mt-6"><PaperPicker papers={papers} selected={selected} setSelected={setSelected} /></div>
    <form onSubmit={ask} className="paper-card mt-8 rounded-3xl p-3"><div className="flex items-end gap-3"><textarea value={query} onChange={e => setQuery(e.target.value)} rows={3} placeholder="例：これらの研究で精度向上に最も寄与した手法は？" className="min-h-24 flex-1 resize-none bg-transparent px-4 py-3 text-base outline-none placeholder:text-[#9ba19e]" /><button disabled={busy || !query.trim()} className="mb-1 grid h-12 w-12 shrink-0 place-items-center rounded-full bg-[#164f3b] text-white disabled:opacity-40">{busy ? <SparklesIcon className="h-5 w-5 animate-pulse" /> : <MagnifyingGlassIcon className="h-5 w-5" />}</button></div></form>
    {!answer && !busy && <div className="mt-10 grid gap-3 md:grid-cols-3">{["提案手法の違いを比較して", "実験結果と限界点を整理して", "再現研究の設計案を考えて"].map(text => <button key={text} onClick={() => setQuery(text)} className="rounded-2xl border border-[#d8dad4] bg-white/55 p-4 text-left text-sm text-[#52605b] hover:border-[#93aa9e]">{text}<ChevronRightIcon className="mt-4 h-4 w-4" /></button>)}</div>}
    {(busy || answer) && <div className="mt-10 grid gap-6 lg:grid-cols-[1fr_320px]"><article aria-live="polite" className="paper-card rounded-3xl p-7 md:p-10"><div className="mb-5 flex items-center gap-2 text-xs font-bold uppercase tracking-[.18em] text-[#42705b]"><SparklesIcon className="h-4 w-4" />Grounded synthesis</div>{answer ? <p className="whitespace-pre-wrap text-[15px] leading-8">{answer}</p> : <div className="space-y-3"><div className="h-4 w-11/12 animate-pulse rounded bg-[#e2e5df]"/><div className="h-4 w-full animate-pulse rounded bg-[#e2e5df]"/><div className="h-4 w-8/12 animate-pulse rounded bg-[#e2e5df]"/></div>}</article><aside><h2 className="mb-3 text-sm font-semibold">引用された根拠 <span className="text-[#89918e]">{citations.length}</span></h2><div className="space-y-3">{citations.map(c => <button key={c.index} onClick={() => openEvidence({ paperId:c.paper_id, paperTitle:c.paper_title, page:c.page, chunkId:c.chunk_id })} aria-label={`${c.paper_title} ${c.page}ページの引用を開く`} className="block w-full rounded-2xl border border-[#d8dad4] bg-white/65 p-4 text-left transition hover:border-[#6f9d86] hover:bg-[#f5faf7] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><div className="mb-2 flex items-center gap-2"><span className="grid h-6 w-6 place-items-center rounded-full bg-[#164f3b] text-[10px] font-bold text-white">{c.index}</span><span className="text-xs font-semibold text-[#a06a28]">p. {c.page}</span></div><p className="line-clamp-2 text-xs font-semibold leading-5">{c.paper_title}</p><p className="mt-2 line-clamp-4 text-xs leading-5 text-[#68736f]">{c.excerpt}</p><span className="mt-3 inline-flex items-center gap-1 text-[10px] font-bold text-[#35634f]">根拠を確認 <ChevronRightIcon className="h-3 w-3" /></span></button>)}</div></aside></div>}
  </div></section>;
}

function AnalysisView({ papers, selected, setSelected, openEvidence, canWrite }: { papers: Paper[]; selected: string[]; setSelected: (ids: string[]) => void; openEvidence: (target: EvidenceTarget) => void; canWrite: boolean }) {
  const [rows, setRows] = useState<CompareRow[]>([]); const [gaps, setGaps] = useState<Gap[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [saveName, setSaveName] = useState(""); const [saving, setSaving] = useState(false); const [savedNotice, setSavedNotice] = useState(""); const selectedPapers = useMemo(() => papers.filter(p => selected.includes(p.id)), [papers, selected]);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    abortRef.current?.abort(); setBusy(false); setRows([]); setGaps([]); setError("");
    return () => abortRef.current?.abort();
  }, [selected]);
  const analyze = async () => {
    if (!selected.length) return;
    const paperIds = [...selected]; const controller = new AbortController(); abortRef.current = controller; setBusy(true); setRows([]); setGaps([]); setError("");
    try {
      const [comparison, researchGaps] = await Promise.all([
        comparePapers(paperIds, controller.signal),
        findResearchGaps(paperIds, controller.signal),
      ]);
      setRows(comparison); setGaps(researchGaps);
    } catch (requestError) {
      const normalized = toApiError(requestError, "分析に失敗しました");
      if (normalized.code !== "aborted") setError(normalized.message);
    } finally {
      if (abortRef.current === controller) { abortRef.current = null; setBusy(false); }
    }
  };
  const saveCurrent = async (event: FormEvent) => {
    event.preventDefault(); if (!canWrite || !saveName.trim() || !rows.length) return; setSaving(true); setError(""); setSavedNotice("");
    try { await saveComparison(saveName.trim(), selected); setSaveName(""); setSavedNotice("比較を研究ワークスペースへ保存しました。"); }
    catch (requestError) { setError(apiErrorMessage(requestError, "比較を保存できませんでした")); }
    finally { setSaving(false); }
  };
  return <section className="rise"><div className="mb-8"><p className="mb-2 text-xs font-bold uppercase tracking-[.2em] text-[#a06a28]">Cross-paper analysis</p><h1 className="serif text-4xl font-semibold md:text-5xl">違いを並べて、次を見つける。</h1><p className="mt-3 text-sm text-[#68736f]">比較したい論文を選び、方法・結果・限界を横断して整理します。</p></div><PaperPicker papers={papers} selected={selected} setSelected={setSelected} /><button onClick={analyze} disabled={!selected.length || busy} className="mt-6 flex items-center gap-2 rounded-full bg-[#164f3b] px-6 py-3 text-sm font-semibold text-white disabled:opacity-40"><BeakerIcon className="h-4 w-4" />{busy ? "分析しています…" : `${selectedPapers.length}件を分析`}</button>
  {error && <div role="alert" className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
  {!rows.length && !busy && !error && <div className="mt-10"><Empty icon={BeakerIcon} title="比較対象を選択してください" body="2件以上の論文を選ぶと、研究目的・手法・結果・限界点を同じ軸で比較できます。" /></div>}
  {rows.length > 0 && <><form onSubmit={saveCurrent} className="mt-8 flex flex-col gap-2 rounded-2xl border border-[#d8dad4] bg-white/55 p-4 sm:flex-row"><label htmlFor="comparison-name" className="sr-only">比較の保存名</label><input id="comparison-name" disabled={!canWrite} value={saveName} onChange={event => setSaveName(event.target.value)} placeholder={canWrite ? "比較の名前" : "viewer権限では保存できません"} className="min-w-0 flex-1 rounded-xl border border-[#d5d8d2] bg-white px-4 py-2 text-sm disabled:opacity-50"/><button disabled={!canWrite || saving || !saveName.trim()} className="rounded-full bg-[#164f3b] px-5 py-2 text-xs font-semibold text-white disabled:opacity-40">{saving ? "保存中…" : "比較を保存"}</button>{savedNotice && <span role="status" className="self-center text-xs text-[#35634f]">{savedNotice}</span>}</form><div className="paper-card mt-4 overflow-x-auto rounded-3xl"><table className="min-w-[1050px] w-full border-collapse text-left"><thead><tr className="border-b border-[#deddd5] bg-[#f1f3ee]"><th className="p-5 text-xs uppercase tracking-wider text-[#68736f]">論文</th>{["目的","手法","結果","限界・課題"].map(h => <th key={h} className="p-5 text-xs uppercase tracking-wider text-[#68736f]">{h}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.paper_id} className="border-b border-[#e7e6df] align-top last:border-0"><td className="w-52 p-5 serif font-semibold"><button onClick={() => openEvidence({ paperId:row.paper_id, paperTitle:row.title, page:1 })} className="text-left underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]">{row.title}</button></td>{[row.purpose,row.method,row.results,row.limitations].map((v,i) => <td key={i} className="min-w-52 p-5 text-xs leading-6 text-[#52605b]">{v}</td>)}</tr>)}</tbody></table></div><div className="mt-12"><div className="mb-5 flex items-center gap-3"><div className="rounded-full bg-[#f5e7d4] p-2 text-[#a06a28]"><LightBulbIcon className="h-5 w-5" /></div><div><h2 className="serif text-2xl font-semibold">Research gaps</h2><p className="text-xs text-[#7a837f]">論文に明記された限界と、次の検証機会</p></div></div>{gaps.length ? <div className="grid gap-4 lg:grid-cols-2">{gaps.map((gap,i) => <article key={`${gap.paper_id}-${i}`} className="paper-card rounded-2xl p-6"><button onClick={() => openEvidence({ paperId:gap.paper_id, paperTitle:gap.paper_title, page:Number.parseInt(gap.page, 10) || 1 })} aria-label={`${gap.paper_title} ${gap.page}ページの根拠を開く`} className="block w-full rounded text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#164f3b]"><div className="mb-3 flex items-center justify-between"><span className="text-xs font-bold text-[#a06a28]">GAP {String(i+1).padStart(2,"0")}</span><span className="text-xs text-[#89918e]">p. {gap.page}</span></div><p className="text-sm font-medium leading-7">{gap.gap}</p><div className="mt-5 border-l-2 border-[#7eaa94] pl-4"><div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-[#42705b]">Opportunity</div><p className="text-xs leading-6 text-[#68736f]">{gap.opportunity}</p></div><span className="mt-4 inline-flex items-center gap-1 text-[10px] font-bold text-[#35634f]">該当ページを確認 <ChevronRightIcon className="h-3 w-3" /></span></button></article>)}</div> : <p className="rounded-2xl bg-white/55 p-6 text-sm text-[#68736f]">選択した論文から明示的なLimitations / Future workを検出できませんでした。</p>}</div></>}
  </section>;
}

function SessionLoading() {
  return <main className="grain grid min-h-screen place-items-center bg-[#f6f4ee] p-6"><div role="status" className="text-center"><div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#b9c8c0] border-t-[#164f3b]"/><p className="serif text-xl font-semibold">ワークスペースを準備しています</p><p className="mt-2 text-sm text-[#68736f]">認証情報とアクセス権を確認しています。</p></div></main>;
}

function SessionErrorState({ mode, error, onRetry, onToken }: { mode: "dev" | "oidc" | null; error: ApiError | null; onRetry: () => void; onToken: (token: string) => void }) {
  const [token, setToken] = useState("");
  const status = error?.status;
  const heading = status === 401 ? "認証が必要です" : status === 403 ? "アクセス権がありません" : status === 503 ? "サービスを利用できません" : "ワークスペースを開始できません";
  const body = status === 401
    ? "有効な認証情報を設定して、もう一度接続してください。"
    : status === 403
      ? "このワークスペースへの参加権限を管理者に確認してください。"
      : status === 503
        ? "認証設定またはデータサービスを確認してください。"
        : "APIへの接続とフロントエンド設定を確認してください。";
  const submit = (event: FormEvent) => { event.preventDefault(); if (token.trim()) onToken(token.trim()); };

  return <main className="grain grid min-h-screen place-items-center bg-[#f6f4ee] p-6"><section role="alert" className="paper-card w-full max-w-lg rounded-3xl p-8 text-center md:p-10"><div className="mx-auto mb-5 grid h-12 w-12 place-items-center rounded-full bg-red-50 text-lg font-bold text-red-700">{status || "!"}</div><h1 className="serif text-3xl font-semibold">{heading}</h1><p className="mt-3 text-sm leading-7 text-[#68736f]">{body}</p>{error?.message && <p className="mt-4 rounded-xl bg-white/70 p-3 text-xs leading-5 text-[#52605b]">{error.message}</p>}
    {mode === "oidc" && status === 401 && <form onSubmit={submit} className="mt-6 text-left"><label htmlFor="oidc-token" className="text-xs font-semibold text-[#52605b]">OIDC access token（このタブのsessionStorageに保持）</label><input id="oidc-token" type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} className="mt-2 w-full rounded-xl border border-[#d5d8d2] bg-white px-4 py-3 text-sm outline-none focus:border-[#6c887a]"/><button disabled={!token.trim()} className="mt-3 w-full rounded-full bg-[#164f3b] px-5 py-3 text-sm font-semibold text-white disabled:opacity-40">トークンを設定して接続</button></form>}
    {!(mode === "oidc" && status === 401) && <button onClick={onRetry} className="mt-6 rounded-full bg-[#164f3b] px-6 py-3 text-sm font-semibold text-white">再試行</button>}
  </section></main>;
}

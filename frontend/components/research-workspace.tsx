"use client";

import {
  ArrowDownTrayIcon, ArrowPathIcon, BookmarkIcon, ClockIcon, PencilIcon, TagIcon, TrashIcon,
} from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useState } from "react";

import {
  createTag, deleteSavedComparison, deleteSearchHistory, deleteTag, exportPapers,
  getPaperTags, listSavedComparisons, listSearchHistory, listTags, setPaperTags, updateTag,
  type ExportFormat, type Paper, type SavedComparison, type SearchHistory, type Tag,
} from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type ResearchWorkspaceProps = {
  papers: Paper[];
  canWrite: boolean;
  exportPaperIds: string[];
  onReplay: (query: string, paperIds: string[]) => void;
};

export function ResearchWorkspace({ papers, canWrite, exportPaperIds, onReplay }: ResearchWorkspaceProps) {
  const [tags, setTags] = useState<Tag[]>([]);
  const [paperTagIds, setPaperTagIds] = useState<Record<string, string[]>>({});
  const [history, setHistory] = useState<SearchHistory[]>([]);
  const [comparisons, setComparisons] = useState<SavedComparison[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [tagName, setTagName] = useState("");
  const [tagColor, setTagColor] = useState("#64748b");
  const [editingTag, setEditingTag] = useState<Tag | null>(null);

  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    Promise.all([
      listTags(controller.signal), listSearchHistory(controller.signal), listSavedComparisons(controller.signal),
      Promise.all(papers.map(async paper => [paper.id, (await getPaperTags(paper.id, controller.signal)).map(tag => tag.id)] as const)),
    ]).then(([nextTags, nextHistory, nextComparisons, assignments]) => {
      setTags(nextTags); setHistory(nextHistory); setComparisons(nextComparisons); setPaperTagIds(Object.fromEntries(assignments));
    }).catch(requestError => {
      if (!controller.signal.aborted) setError(apiErrorMessage(requestError, "研究ワークスペースを読み込めませんでした"));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [papers]);

  const submitTag = async (event: FormEvent) => {
    event.preventDefault(); if (!tagName.trim() || !canWrite) return; setBusy("tag"); setError("");
    try {
      if (editingTag) {
        const updated = await updateTag(editingTag.id, tagName.trim(), tagColor);
        setTags(current => current.map(item => item.id === updated.id ? updated : item)); setEditingTag(null);
      } else {
        const created = await createTag(tagName.trim(), tagColor); setTags(current => [...current, created]);
      }
      setTagName(""); setTagColor("#64748b");
    } catch (requestError) { setError(apiErrorMessage(requestError, "タグを保存できませんでした")); }
    finally { setBusy(""); }
  };

  const removeTag = async (tagId: string) => {
    if (!canWrite) return; setBusy(`tag-${tagId}`); setError("");
    try {
      await deleteTag(tagId); setTags(current => current.filter(item => item.id !== tagId));
      setPaperTagIds(current => Object.fromEntries(Object.entries(current).map(([paperId, ids]) => [paperId, ids.filter(id => id !== tagId)])));
    } catch (requestError) { setError(apiErrorMessage(requestError, "タグを削除できませんでした")); }
    finally { setBusy(""); }
  };

  const togglePaperTag = async (paperId: string, tagId: string) => {
    if (!canWrite) return;
    const currentIds = paperTagIds[paperId] ?? [];
    const nextIds = currentIds.includes(tagId) ? currentIds.filter(id => id !== tagId) : [...currentIds, tagId];
    setBusy(`paper-${paperId}`); setError("");
    try { const assigned = await setPaperTags(paperId, nextIds); setPaperTagIds(current => ({ ...current, [paperId]: assigned.map(tag => tag.id) })); }
    catch (requestError) { setError(apiErrorMessage(requestError, "論文タグを更新できませんでした")); }
    finally { setBusy(""); }
  };

  const removeHistory = async (id: string) => {
    if (!canWrite) return; setBusy(`history-${id}`);
    try { await deleteSearchHistory(id); setHistory(current => current.filter(item => item.id !== id)); }
    catch (requestError) { setError(apiErrorMessage(requestError, "検索履歴を削除できませんでした")); }
    finally { setBusy(""); }
  };

  const removeComparison = async (id: string) => {
    if (!canWrite) return; setBusy(`comparison-${id}`);
    try { await deleteSavedComparison(id); setComparisons(current => current.filter(item => item.id !== id)); }
    catch (requestError) { setError(apiErrorMessage(requestError, "保存済み比較を削除できませんでした")); }
    finally { setBusy(""); }
  };

  const download = async (format: ExportFormat) => {
    setBusy(`export-${format}`); setError("");
    try {
      const { blob, filename } = await exportPapers(format, exportPaperIds);
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    } catch (requestError) { setError(apiErrorMessage(requestError, "エクスポートに失敗しました")); }
    finally { setBusy(""); }
  };

  if (loading) return <div role="status" className="py-24 text-center text-sm text-[#68736f]">研究ワークスペースを読み込んでいます…</div>;

  return <section className="rise"><div className="mb-8"><p className="mb-2 text-xs font-bold uppercase tracking-[.2em] text-[#a06a28]">Research workspace</p><h1 className="serif text-4xl font-semibold md:text-5xl">調査の続きを、整理して残す。</h1><p className="mt-3 text-sm text-[#68736f]">タグ、検索履歴、保存した比較と文献エクスポートをまとめて管理します。</p>{!canWrite && <p className="mt-4 rounded-xl bg-amber-50 p-3 text-xs text-amber-800">viewer権限のため閲覧のみ可能です。</p>}</div>
    {error && <div role="alert" className="mb-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
    <div className="grid gap-6 xl:grid-cols-2">
      <section className="paper-card rounded-3xl p-6"><div className="mb-5 flex items-center gap-2"><TagIcon className="h-5 w-5 text-[#164f3b]"/><h2 className="serif text-2xl font-semibold">タグと論文</h2></div>
        <form onSubmit={submitTag} className="mb-5 flex flex-wrap gap-2"><input aria-label="タグ色" type="color" disabled={!canWrite} value={tagColor} onChange={event => setTagColor(event.target.value)} className="h-10 w-12 rounded border border-[#d5d8d2] bg-white p-1 disabled:opacity-40"/><input aria-label="タグ名" disabled={!canWrite} maxLength={100} value={tagName} onChange={event => setTagName(event.target.value)} placeholder="タグ名" className="min-w-40 flex-1 rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm disabled:opacity-40"/><button disabled={!canWrite || !tagName.trim() || busy === "tag"} className="rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">{editingTag ? "更新" : "作成"}</button>{editingTag && <button type="button" onClick={() => { setEditingTag(null); setTagName(""); }} className="text-xs text-[#68736f]">取消</button>}</form>
        <div className="mb-6 flex flex-wrap gap-2">{tags.map(tag => <span key={tag.id} className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs" style={{ borderColor:tag.color }}><span className="h-2 w-2 rounded-full" style={{ backgroundColor:tag.color }}/>{tag.name}{canWrite && <><button aria-label={`${tag.name}を編集`} onClick={() => { setEditingTag(tag); setTagName(tag.name); setTagColor(tag.color); }}><PencilIcon className="h-3 w-3"/></button><button disabled={busy === `tag-${tag.id}`} aria-label={`${tag.name}を削除`} onClick={() => removeTag(tag.id)}><TrashIcon className="h-3 w-3"/></button></>}</span>)}</div>
        <div className="max-h-[420px] space-y-3 overflow-y-auto">{papers.map(paper => <article key={paper.id} className="rounded-2xl border border-[#deddd5] bg-white/65 p-4"><h3 className="line-clamp-2 text-sm font-semibold">{paper.title}</h3><div className="mt-3 flex flex-wrap gap-2">{tags.length ? tags.map(tag => <label key={tag.id} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${!canWrite ? "opacity-70" : "cursor-pointer"}`}><input type="checkbox" disabled={!canWrite || busy === `paper-${paper.id}`} checked={(paperTagIds[paper.id] ?? []).includes(tag.id)} onChange={() => togglePaperTag(paper.id, tag.id)}/><span className="h-2 w-2 rounded-full" style={{ backgroundColor:tag.color }}/>{tag.name}</label>) : <span className="text-xs text-[#89918e]">タグを作成すると割り当てられます。</span>}</div></article>)}</div>
      </section>

      <div className="space-y-6"><section className="paper-card rounded-3xl p-6"><div className="mb-4 flex items-center gap-2"><ArrowDownTrayIcon className="h-5 w-5 text-[#164f3b]"/><h2 className="serif text-2xl font-semibold">文献エクスポート</h2></div><p className="mb-4 text-xs text-[#68736f]">{exportPaperIds.length ? `選択中の${exportPaperIds.length}件` : `ワークスペース内の全${papers.length}件`}を出力します。</p><div className="flex flex-wrap gap-2">{(["bibtex","ris","csv"] as ExportFormat[]).map(format => <button key={format} onClick={() => download(format)} disabled={busy === `export-${format}`} className="rounded-full border border-[#164f3b] px-4 py-2 text-xs font-semibold uppercase text-[#164f3b] disabled:opacity-40">{format}</button>)}</div></section>
        <section className="paper-card rounded-3xl p-6"><div className="mb-4 flex items-center gap-2"><ClockIcon className="h-5 w-5 text-[#164f3b]"/><h2 className="serif text-2xl font-semibold">検索履歴</h2></div><div className="max-h-72 space-y-3 overflow-y-auto">{history.length ? history.map(item => <article key={item.id} className="rounded-2xl border border-[#deddd5] bg-white/65 p-4"><p className="text-sm font-semibold">{item.query}</p><p className="mt-1 text-[10px] text-[#89918e]">{new Date(item.created_at).toLocaleString("ja-JP")} · {item.paper_ids.length || "全"}論文</p><div className="mt-3 flex gap-2"><button onClick={() => onReplay(item.query, item.paper_ids)} className="inline-flex items-center gap-1 text-xs font-semibold text-[#164f3b]"><ArrowPathIcon className="h-3 w-3"/>再質問</button>{canWrite && <button disabled={busy === `history-${item.id}`} onClick={() => removeHistory(item.id)} className="text-xs text-red-700">削除</button>}</div></article>) : <p className="text-sm text-[#68736f]">検索履歴はまだありません。</p>}</div></section>
        <section className="paper-card rounded-3xl p-6"><div className="mb-4 flex items-center gap-2"><BookmarkIcon className="h-5 w-5 text-[#164f3b]"/><h2 className="serif text-2xl font-semibold">保存した比較</h2></div><div className="max-h-80 space-y-3 overflow-y-auto">{comparisons.length ? comparisons.map(item => <article key={item.id} className="rounded-2xl border border-[#deddd5] bg-white/65 p-4"><div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">{item.name}</h3><p className="mt-1 text-[10px] text-[#89918e]">{item.paper_ids.length}論文 · {new Date(item.created_at).toLocaleString("ja-JP")}</p></div>{canWrite && <button aria-label={`${item.name}を削除`} disabled={busy === `comparison-${item.id}`} onClick={() => removeComparison(item.id)} className="text-red-700"><TrashIcon className="h-4 w-4"/></button>}</div><div className="mt-3 space-y-1">{item.result.slice(0,3).map((row,index) => <p key={index} className="truncate text-xs text-[#52605b]">• {typeof row.title === "string" ? row.title : `比較結果 ${index+1}`}</p>)}</div></article>) : <p className="text-sm text-[#68736f]">保存済み比較はありません。</p>}</div></section>
      </div>
    </div>
  </section>;
}

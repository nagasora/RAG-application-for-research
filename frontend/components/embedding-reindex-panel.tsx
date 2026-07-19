"use client";

import { ArrowPathIcon, LanguageIcon } from "@heroicons/react/24/outline";
import { useMemo, useState } from "react";

import { reindexEmbeddings, type Paper } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

export function EmbeddingReindexPanel({ papers, canWrite }: { papers: Paper[]; canWrite: boolean }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const readyPaperIds = useMemo(() => papers.filter(paper => paper.status === "ready").map(paper => paper.id), [papers]);

  const reindex = async () => {
    if (!canWrite || !readyPaperIds.length || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await reindexEmbeddings({ paper_ids:readyPaperIds });
      const jobs = result.jobs ?? [];
      const failed = jobs.filter(job => job.status === "failed").length;
      setNotice(failed
        ? `${jobs.length}件の再作成を依頼しました（${failed}件は失敗）。下の状態を確認してください。`
        : `${jobs.length}件の埋め込み再作成を開始しました。${result.provider === "openai" ? "多言語の意味検索に使用されます。" : "現在はローカル埋め込みです。"}`,
      );
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "埋め込みの再作成を開始できませんでした"));
    } finally { setBusy(false); }
  };

  return <section aria-labelledby="multilingual-retrieval-title" className="mb-8 rounded-3xl border border-[#c9ddd0] bg-[#eef6f0] p-5">
    <div className="flex flex-wrap items-start justify-between gap-4"><div className="flex min-w-0 gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-white text-[#164f3b]"><LanguageIcon className="h-5 w-5" /></div><div><h2 id="multilingual-retrieval-title" className="text-sm font-bold text-[#173d2e]">日本語の質問で英語・日本語論文を検索する</h2><p className="mt-1 max-w-3xl text-xs leading-5 text-[#526b5d]">多言語埋め込みへ切り替えた後は、既存の解析済み論文を再作成してください。日本語と英語の表現の近さを検索に使えるようになります。本文の引用確認は引き続き必要です。</p></div></div><span className="rounded-full bg-white/80 px-3 py-1 text-xs font-semibold text-[#35634f]">解析済み {readyPaperIds.length} 件</span></div>
    <div className="mt-4 flex flex-wrap items-center gap-3"><button type="button" onClick={() => void reindex()} disabled={!canWrite || !readyPaperIds.length || busy} className="inline-flex items-center gap-2 rounded-full bg-[#164f3b] px-4 py-2.5 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40"><ArrowPathIcon className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />{busy ? "再作成を開始しています…" : "解析済み論文を再埋め込み"}</button>{!canWrite && <p className="text-xs text-[#82642e]">viewer 権限では検索と状態の確認のみできます。再埋め込みは owner / editor に依頼してください。</p>}{canWrite && !readyPaperIds.length && <p className="text-xs text-[#82642e]">再埋め込みできる解析済み論文がありません。論文の取り込み完了後に実行できます。</p>}</div>
    {notice && <p role="status" className="mt-3 rounded-xl bg-white/75 p-3 text-xs leading-5 text-[#35634f]">{notice}</p>}
    {error && <p role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}
  </section>;
}

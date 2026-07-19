"use client";

import { CheckCircleIcon, ChevronRightIcon, SparklesIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getGraphSnapshot, listHypothesisCards, listIdeas, listSavedComparisons, listSearchHistory,
  listGraphSources, type Paper,
} from "@/lib/api/client";

type View = "library" | "ask" | "analysis" | "research" | "graph";

type Activity = {
  searched: number | null;
  ideas: number | null;
  hypotheses: number | null;
  comparisons: number | null;
  graphPending: number | null;
  graphReviewed: number | null;
  graphSources: number | null;
};

const EMPTY_ACTIVITY: Activity = { searched:null, ideas:null, hypotheses:null, comparisons:null, graphPending:null, graphReviewed:null, graphSources:null };

function storageKey(workspaceId: string) {
  return `paperpilot.first-run-tutorial.${workspaceId}`;
}

function countLabel(count: number | null, singular: string, empty: string) {
  if (count === null) return "確認中…";
  return count ? `${singular} ${count}件` : empty;
}

/** A compact, evidence-based orientation panel.  Completion is derived from saved
 * research records where possible; it never assumes that a navigation click did work. */
export function FirstRunTutorial({ workspaceId, papers, onNavigate }: {
  workspaceId: string;
  papers: Paper[];
  onNavigate: (view: View) => void;
}) {
  const [dismissed, setDismissed] = useState(false);
  const [activity, setActivity] = useState<Activity>(EMPTY_ACTIVITY);
  const [refreshing, setRefreshing] = useState(false);
  const activityAbortRef = useRef<AbortController | null>(null);
  const activityRevisionRef = useRef(0);
  const readyCount = useMemo(() => papers.filter(paper => paper.status === "ready").length, [papers]);

  useEffect(() => {
    // A workspace owns its own tutorial state and records. Do not leave the prior
    // workspace's progress visible while the new one is loading.
    activityAbortRef.current?.abort();
    activityRevisionRef.current += 1;
    setActivity(EMPTY_ACTIVITY);
    setRefreshing(false);
    try { setDismissed(window.localStorage.getItem(storageKey(workspaceId)) === "dismissed"); }
    catch { setDismissed(false); }
  }, [workspaceId]);

  const refresh = useCallback(async () => {
    activityAbortRef.current?.abort();
    const controller = new AbortController();
    activityAbortRef.current = controller;
    const revision = ++activityRevisionRef.current;
    setRefreshing(true);
    try {
      const [history, ideas, hypotheses, comparisons, snapshot, sources] = await Promise.all([
        listSearchHistory(controller.signal), listIdeas(controller.signal), listHypothesisCards(controller.signal),
        listSavedComparisons(controller.signal), getGraphSnapshot("default", controller.signal), listGraphSources(controller.signal),
      ]);
      if (controller.signal.aborted || revision !== activityRevisionRef.current) return;
      const nodes = snapshot.nodes ?? [];
      setActivity({
        searched:history.length,
        ideas:ideas.length,
        hypotheses:hypotheses.length,
        comparisons:comparisons.length,
        graphPending:nodes.filter(node => node.status === "review_pending" || node.status === "review_required").length,
        graphReviewed:nodes.filter(node => ["active", "verified", "rejected", "superseded", "pruned"].includes(node.status)).length,
        graphSources:sources.length,
      });
    } catch {
      // The individual feature pages show the actionable API error. Keep this guide
      // truthful by reporting the corresponding state as unavailable instead of complete.
      if (!controller.signal.aborted && revision === activityRevisionRef.current) setActivity(EMPTY_ACTIVITY);
    } finally {
      if (revision === activityRevisionRef.current) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!dismissed) void refresh();
    return () => activityAbortRef.current?.abort();
  }, [dismissed, refresh, workspaceId]);

  const dismiss = () => {
    try { window.localStorage.setItem(storageKey(workspaceId), "dismissed"); } catch { /* storage unavailable */ }
    setDismissed(true);
  };
  const reopen = () => {
    try { window.localStorage.removeItem(storageKey(workspaceId)); } catch { /* storage unavailable */ }
    setDismissed(false);
  };

  if (dismissed) return <button type="button" onClick={reopen} className="mb-6 inline-flex items-center gap-2 rounded-full border border-[#9cb6a7] bg-white/75 px-4 py-2 text-xs font-semibold text-[#164f3b] hover:bg-[#edf5f0]"><SparklesIcon className="h-4 w-4"/>使い方・進捗を表示</button>;

  const steps = [
    {
      title:"論文を検索可能にする", view:"library" as const,
      done:readyCount > 0,
      state:readyCount ? `解析済み ${readyCount} / ${papers.length}件` : "解析済み論文がありません",
      body:"LibraryでPDFを追加し、状態が ready になったことを確認します。既存論文は「再埋め込み」を実行すると、日本語の質問から英語・日本語論文を意味検索できます。",
      action:"ライブラリを開く",
    },
    {
      title:"日本語で論文に質問する", view:"ask" as const,
      done:(activity.searched ?? 0) > 0,
      state:countLabel(activity.searched, "保存済みの質問", "質問履歴はまだありません"),
      body:"例:「この手法の限界と反証結果は？」。回答の引用を開き、論文原文と対応していることを確認します。質問を送信した実績だけを完了として表示します。",
      action:"論文に質問する",
    },
    {
      title:"気づきをIdeaとして残す", view:"research" as const,
      done:(activity.ideas ?? 0) > 0,
      state:countLabel(activity.ideas, "Idea", "Ideaはまだありません"),
      body:"回答・比較・手元の観察から、右下の「考えを残す」または整理・履歴の「新しいIdeaを記録」で保存します。Ideaは未検証であり、論文根拠そのものではありません。",
      action:"Idea Inboxを開く",
    },
    {
      title:"反証可能な仮説へ昇格する", view:"research" as const,
      done:(activity.hypotheses ?? 0) > 0,
      state:countLabel(activity.hypotheses, "Hypothesis Card", "仮説カードはまだありません"),
      body:"Idea Inboxで「根拠を接続」「反証条件を確認」「試験方法を設計」をすべて確認してから昇格します。ここで初めて実験計画へ接続できます。",
      action:"チェックリストを開く",
    },
    {
      title:"複数論文を比較してギャップを検討する", view:"analysis" as const,
      done:(activity.comparisons ?? 0) > 0,
      state:countLabel(activity.comparisons, "保存済み比較", "保存済み比較はまだありません"),
      body:"readyの論文を2件以上選び、比較・ギャップを分析します。gap候補は結論ではありません。原文を開いてから保存・Idea化してください。",
      action:"比較・発見を開く",
    },
    {
      title:"グラフ候補を人間がレビューする", view:"graph" as const,
      done:activity.graphPending === 0 && (activity.graphReviewed ?? 0) > 0,
      state:activity.graphPending === null ? "確認中…" : `レビュー待ち ${activity.graphPending}件 · 判断済み ${activity.graphReviewed ?? 0}件 · Source ${activity.graphSources ?? 0}件`,
      body:"グラフへ保存した案は review pending のままでは完了ではありません。根拠を選び、支持・棄却などを人間が判断した記録があるときだけ完了になります。登録論文由来のSourceはタイトルで表示され、手入力Sourceは論文外の原典だけに使います。",
      action:"知識グラフを開く",
    },
  ];
  const completed = steps.filter(step => step.done).length;

  return <aside aria-label="最初の研究ワークフロー" className="rise mb-8 rounded-3xl border border-[#bdd5c5] bg-[#f0f7f2] p-5 shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-[10px] font-bold uppercase tracking-[.18em] text-[#35634f]">First research loop</p><h2 className="serif mt-1 text-2xl font-semibold text-[#21372d]">研究の一連の流れを、実データで確認する。</h2><p className="mt-2 max-w-3xl text-xs leading-5 text-[#526b5d]">各項目は保存済みの記録を読んでいます。画面を開いただけでは完了になりません。AI案・gap候補は、必ず根拠と反証を人間が確認してください。</p></div><div className="flex items-center gap-2"><button type="button" onClick={() => void refresh()} disabled={refreshing} className="rounded-full border border-[#86a895] bg-white px-3 py-1.5 text-[11px] font-semibold text-[#164f3b] disabled:opacity-50">{refreshing ? "更新中…" : "進捗を更新"}</button><button type="button" aria-label="使い方を閉じる" onClick={dismiss} className="grid h-8 w-8 place-items-center rounded-full text-[#526b5d] hover:bg-white"><XMarkIcon className="h-4 w-4"/></button></div></div>
    <div className="mt-4 flex items-center justify-between rounded-2xl bg-white/70 px-4 py-2 text-xs text-[#40594b]"><span><strong>{completed} / {steps.length}</strong> 項目で記録を確認できました</span><span>未実施は未実施のまま表示します</span></div>
    <ol className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">{steps.map((step, index) => <li key={step.title} className="flex min-h-48 flex-col rounded-2xl border border-[#d3e2d8] bg-white/80 p-4"><div className="flex items-start gap-2"><CheckCircleIcon className={`mt-0.5 h-5 w-5 shrink-0 ${step.done ? "text-[#2d7a52]" : "text-[#a6b9ae]"}`}/><div><p className="text-[10px] font-bold tracking-wider text-[#7a837f]">STEP {index + 1}</p><h3 className="mt-0.5 text-sm font-bold text-[#26342e]">{step.title}</h3><p className={`mt-1 text-[11px] font-semibold ${step.done ? "text-[#2d7a52]" : "text-[#8a5d24]"}`}>{step.done ? "記録あり · " : "未完了 · "}{step.state}</p></div></div><p className="mt-3 text-xs leading-5 text-[#52605b]">{step.body}</p><button type="button" onClick={() => onNavigate(step.view)} className="mt-auto inline-flex items-center gap-1 pt-3 text-left text-xs font-bold text-[#164f3b] underline underline-offset-2">{step.action}<ChevronRightIcon className="h-3.5 w-3.5"/></button></li>)}</ol>
  </aside>;
}

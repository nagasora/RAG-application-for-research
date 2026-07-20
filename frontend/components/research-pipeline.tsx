"use client";

import { ArrowDownTrayIcon, BeakerIcon, CheckCircleIcon, LightBulbIcon } from "@heroicons/react/24/outline";
import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  addExperimentResult, createExperimentPlan, getExperimentPlanSnapshot, listExperimentPlans,
  createResearchAction, decomposeIdeaActions, listHypothesisCards, listIdeas, listResearchActions, promoteIdea, updateIdea, updateResearchAction,
  type ExperimentPlan, type ExperimentPlanCreate, type HypothesisCard, type Idea, type ResearchAction,
} from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

const CHECKLIST = [
  ["evidence", "根拠を接続"], ["falsifier", "反証条件を確認"], ["test", "試験方法を設計"],
] as const;

const KIND_LABEL: Record<string, string> = {
  observation:"観察", interpretation:"解釈", hypothesis:"仮説", falsifier:"反証候補", todo:"TODO",
};

const EMPTY_PLAN: ExperimentPlanCreate = {
  hypothesis_card_id:null, intervention:"", measurement:"", controls:"", confounders:[], predictions:[],
  decision_threshold:"", stopping_rule:"", required_data:"", cost:"", competing_hypothesis_discrimination:"", evidence:[],
};

function commaList(value: string) {
  return value.split(/[,、\n]/).map(item => item.trim()).filter(Boolean);
}

function checklistValue(idea: Idea, key: string) {
  return idea.checklist?.[key] === true;
}

function canPromote(idea: Idea) {
  return idea.status === "unverified" && CHECKLIST.every(([key]) => checklistValue(idea, key));
}

export function ResearchPipeline({ canWrite }: { canWrite: boolean }) {
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [hypotheses, setHypotheses] = useState<HypothesisCard[]>([]);
  const [experiments, setExperiments] = useState<ExperimentPlan[]>([]);
  const [actions, setActions] = useState<ResearchAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [plan, setPlan] = useState<ExperimentPlanCreate>(EMPTY_PLAN);
  const [confounders, setConfounders] = useState("");
  const [predictions, setPredictions] = useState("");
  const [evidence, setEvidence] = useState("");
  const [results, setResults] = useState<Record<string, { outcome: string; interpretation: string }>>({});

  const load = useCallback(async (signal?: AbortSignal) => {
    const [nextIdeas, nextHypotheses, nextExperiments, nextActions] = await Promise.all([
      listIdeas(signal), listHypothesisCards(signal), listExperimentPlans(signal), listResearchActions(signal),
    ]);
    setIdeas(nextIdeas); setHypotheses(nextHypotheses); setExperiments(nextExperiments); setActions(nextActions);
  }, []);

  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void load(controller.signal).catch(requestError => {
      if (!controller.signal.aborted) setError(apiErrorMessage(requestError, "研究パイプラインを読み込めませんでした"));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const refresh = () => { void listIdeas().then(setIdeas).catch(() => undefined); };
    window.addEventListener("paperpilot:idea-created", refresh);
    return () => { controller.abort(); window.removeEventListener("paperpilot:idea-created", refresh); };
  }, [load]);

  const toggleChecklist = async (idea: Idea, key: string) => {
    if (!canWrite || idea.status !== "unverified") return;
    setBusy(`idea-${idea.id}`); setError(""); setNotice("");
    try {
      const updated = await updateIdea(idea.id, { checklist:{ ...(idea.checklist ?? {}), [key]:!checklistValue(idea, key) } });
      setIdeas(current => current.map(item => item.id === updated.id ? updated : item));
    } catch (requestError) { setError(apiErrorMessage(requestError, "チェックリストを更新できませんでした")); }
    finally { setBusy(""); }
  };

  const promote = async (idea: Idea) => {
    if (!canWrite || !canPromote(idea)) return;
    setBusy(`idea-${idea.id}`); setError(""); setNotice("");
    try {
      const updated = await promoteIdea(idea.id);
      setIdeas(current => current.map(item => item.id === updated.id ? updated : item));
      setHypotheses(await listHypothesisCards());
      setNotice("Ideaをdraft HypothesisCardへ昇格しました。アンカー情報はsnapshotとして保持されます。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "Ideaを昇格できませんでした")); }
    finally { setBusy(""); }
  };

  const decompose = async (idea: Idea) => {
    if (!canWrite) return;
    setBusy(`decompose-${idea.id}`); setError(""); setNotice("");
    try {
      const created = await decomposeIdeaActions(idea.id);
      setActions(current => [...created, ...current.filter(action => !created.some(item => item.id === action.id))]);
      setNotice("Ideaを、根拠確認・反証検索・試験設計の3つの未検証Actionへ分解しました。各Actionを人間が採否判断してください。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "Ideaを研究Actionへ分解できませんでした")); }
    finally { setBusy(""); }
  };

  const updateAction = async (action: ResearchAction, patch: Parameters<typeof updateResearchAction>[1]) => {
    if (!canWrite) return;
    setBusy(`action-${action.id}`); setError("");
    try {
      const updated = await updateResearchAction(action.id, patch);
      setActions(current => current.map(item => item.id === updated.id ? updated : item));
    } catch (requestError) { setError(apiErrorMessage(requestError, "研究Actionを更新できませんでした")); }
    finally { setBusy(""); }
  };

  const createExperimentAction = async (experiment: ExperimentPlan) => {
    if (!canWrite) return;
    setBusy(`experiment-action-${experiment.id}`); setError("");
    try {
      const created = await createResearchAction({ title:`実験を実行する: ${experiment.intervention}`, description:"Experiment Planの測定・対照・停止規則に従って実行し、結果を記録する。", experiment_plan_id:experiment.id, generation_class:"unverified", generation_metadata:{ source:"experiment_plan" } });
      setActions(current => [created, ...current]); setNotice("Experiment Planに期限付きで使えるActionを追加しました。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "実験Actionを作成できませんでした")); }
    finally { setBusy(""); }
  };

  const submitPlan = async (event: FormEvent) => {
    event.preventDefault(); if (!canWrite || busy === "plan") return;
    setBusy("plan"); setError(""); setNotice("");
    try {
      const created = await createExperimentPlan({
        ...plan, hypothesis_card_id:plan.hypothesis_card_id || null,
        confounders:commaList(confounders), predictions:commaList(predictions), evidence:commaList(evidence),
      });
      setExperiments(current => [created, ...current]); setPlan(EMPTY_PLAN);
      setConfounders(""); setPredictions(""); setEvidence(""); setNotice("実験計画を作成しました。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "実験計画を作成できませんでした")); }
    finally { setBusy(""); }
  };

  const recordResult = async (experiment: ExperimentPlan) => {
    const draft = results[experiment.id]; if (!canWrite || !draft?.outcome.trim()) return;
    setBusy(`result-${experiment.id}`); setError(""); setNotice("");
    try {
      const updated = await addExperimentResult(experiment.id, {
        outcome:draft.outcome.trim(), interpretation:draft.interpretation.trim(), data_snapshot:{ source:"manual_ui" },
      });
      setExperiments(current => current.map(item => item.id === updated.id ? updated : item));
      setResults(current => ({ ...current, [experiment.id]:{ outcome:"", interpretation:"" } }));
      setNotice("実験結果と履歴イベントを追記しました。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "実験結果を追記できませんでした")); }
    finally { setBusy(""); }
  };

  const downloadSnapshot = async (experiment: ExperimentPlan) => {
    setBusy(`snapshot-${experiment.id}`); setError("");
    try {
      const snapshot = await getExperimentPlanSnapshot(experiment.id);
      const blob = new Blob([`${JSON.stringify(snapshot, null, 2)}\n`], { type:"application/json" });
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `experiment-${experiment.id}-v1.json`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    } catch (requestError) { setError(apiErrorMessage(requestError, "v1 snapshotを出力できませんでした")); }
    finally { setBusy(""); }
  };

  if (loading) return <div role="status" className="mb-6 rounded-2xl border border-[#deddd5] bg-white/60 p-6 text-center text-sm text-[#68736f]">Idea・仮説・実験を読み込んでいます…</div>;

  return <section className="mb-8 space-y-6" aria-label="Ideaから実験までの研究パイプライン">
    <section className="rounded-3xl border border-[#c9ddd0] bg-[#eef6f0] p-5" aria-label="アイデア探索の使い方">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-[10px] font-bold uppercase tracking-[.16em] text-[#35634f]">Idea exploration</p><h2 className="serif mt-1 text-2xl font-semibold text-[#26342e]">AIの回答を、検証できる研究案へ。</h2><p className="mt-2 max-w-3xl text-xs leading-5 text-[#526b5d]">問いを「論文に質問」で掘り下げ、気づきや反証候補をここへ保存します。保存したIdeaは未検証のまま残り、根拠・反証条件・試験方法を確認してから仮説へ昇格します。</p></div><button type="button" disabled={!canWrite} onClick={() => window.dispatchEvent(new CustomEvent("paperpilot:open-idea-capture"))} className="rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">新しいIdeaを記録</button></div>
      <ol className="mt-4 grid gap-2 text-xs text-[#40594b] md:grid-cols-3"><li className="rounded-xl bg-white/70 p-3"><strong>1. 発散</strong><span className="mt-1 block leading-5">質問画面の回答、比較・ギャップ、手元の気づきを保存。</span></li><li className="rounded-xl bg-white/70 p-3"><strong>2. 反証</strong><span className="mt-1 block leading-5">根拠・反証条件・試験方法を明示して、思いつきと区別。</span></li><li className="rounded-xl bg-white/70 p-3"><strong>3. 実験化</strong><span className="mt-1 block leading-5">チェック完了後にHypothesisへ昇格し、実験計画を作成。</span></li></ol>
    </section>
    {(error || notice) && <div role={error ? "alert" : "status"} className={`rounded-2xl border p-4 text-sm ${error ? "border-red-200 bg-red-50 text-red-800" : "border-[#b8d6c4] bg-[#edf6f0] text-[#24523e]"}`}>{error || notice}</div>}
    <div className="grid gap-6 xl:grid-cols-2">
      <section className="paper-card rounded-3xl p-6"><div className="mb-4 flex items-center gap-2"><LightBulbIcon className="h-5 w-5 text-[#164f3b]"/><div><h2 className="serif text-2xl font-semibold">Idea Inbox</h2><p className="text-xs text-[#68736f]">根拠・反証・試験を確認してから仮説へ昇格します。</p></div></div>
        <div className="max-h-[560px] space-y-3 overflow-y-auto">{ideas.length ? ideas.map(idea => <article key={idea.id} className="rounded-2xl border border-[#deddd5] bg-white/70 p-4"><div className="flex items-start justify-between gap-3"><span className="rounded-full bg-[#e7f0eb] px-2 py-1 text-[10px] font-bold text-[#35634f]">{KIND_LABEL[idea.kind] ?? idea.kind}</span><span className="text-[10px] font-semibold text-[#7a837f]">{idea.status === "promoted" ? "昇格済み" : "未検証"}</span></div><p className="mt-3 whitespace-pre-wrap text-sm leading-6">{idea.content}</p>{(idea.paper_id || idea.source_span_id || idea.claim_id || idea.research_run_id) && <p className="mt-2 truncate text-[10px] text-[#7a837f]">anchor: {idea.source_span_id ? `span:${idea.source_span_id}` : idea.paper_id ? `paper:${idea.paper_id}` : idea.claim_id ? `claim:${idea.claim_id}` : `run:${idea.research_run_id}`}</p>}<div className="mt-3 flex flex-wrap gap-2">{CHECKLIST.map(([key,label]) => <label key={key} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] ${idea.status === "unverified" && canWrite ? "cursor-pointer" : "opacity-70"}`}><input type="checkbox" checked={checklistValue(idea,key)} disabled={!canWrite || idea.status !== "unverified" || busy === `idea-${idea.id}`} onChange={() => void toggleChecklist(idea,key)}/>{label}</label>)}</div><div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={!canWrite || busy === `decompose-${idea.id}`} onClick={() => void decompose(idea)} className="rounded-full border border-[#164f3b] px-3 py-1.5 text-[11px] font-semibold text-[#164f3b] disabled:opacity-35">{busy === `decompose-${idea.id}` ? "分解中…" : "研究Actionへ分解"}</button><button type="button" disabled={!canWrite || !canPromote(idea) || busy === `idea-${idea.id}`} onClick={() => void promote(idea)} className="rounded-full bg-[#164f3b] px-3 py-1.5 text-[11px] font-semibold text-white disabled:opacity-35">Hypothesisへ昇格</button></div></article>) : <p className="rounded-2xl border border-dashed p-5 text-center text-sm text-[#68736f]">右下の「考えを残す」から最初のIdeaを保存できます。</p>}</div>
      </section>

      <section className="paper-card rounded-3xl p-6"><div className="mb-4 flex items-center gap-2"><CheckCircleIcon className="h-5 w-5 text-[#164f3b]"/><div><h2 className="serif text-2xl font-semibold">Hypothesis Cards</h2><p className="text-xs text-[#68736f]">人間レビューと実証状態を分けて確認します。</p></div></div><div className="max-h-[560px] space-y-3 overflow-y-auto">{hypotheses.length ? hypotheses.map(card => <article key={card.id} className="rounded-2xl border border-[#deddd5] bg-white/70 p-4"><div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-[#e7f0eb] px-2 py-1 text-[10px] font-bold text-[#35634f]">{card.status}</span>{card.human_reviewed && <span className="rounded-full bg-blue-50 px-2 py-1 text-[10px] font-bold text-blue-800">人間レビュー済み</span>}{card.empirically_supported && <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-bold text-emerald-800">実証支持</span>}</div><p className="mt-3 text-sm font-semibold leading-6">{card.claim}</p><p className="mt-2 text-xs leading-5 text-[#68736f]">{card.falsifiers?.length ? `反証条件: ${card.falsifiers.join(" / ")}` : "反証条件は未記入です。draftの詳細化が必要です。"}</p></article>) : <p className="rounded-2xl border border-dashed p-5 text-center text-sm text-[#68736f]">昇格済みの仮説はありません。</p>}</div></section>
    </div>

    <section className="paper-card rounded-3xl p-6"><div className="mb-4"><h2 className="serif text-2xl font-semibold">Research Actions</h2><p className="mt-1 text-xs text-[#68736f]">Actionは親Idea・ResearchRun・claim・根拠アンカーを保持します。採否は人間が判断します。</p></div><div className="grid gap-3 lg:grid-cols-2">{actions.map(action => <article key={action.id} className="rounded-2xl border border-[#deddd5] bg-white/70 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><span className="rounded-full bg-[#e7f0eb] px-2 py-1 text-[10px] font-bold text-[#35634f]">{action.generation_class}</span><span className="text-[10px] text-[#68736f]">{action.due_date ? `期限 ${action.due_date}` : "期限未設定"}</span></div><h3 className="mt-2 text-sm font-semibold">{action.title}</h3><p className="mt-1 text-xs leading-5 text-[#68736f]">{action.description}</p><p className="mt-2 truncate font-mono text-[9px] text-[#7a837f]">{action.idea_id ? `idea:${action.idea_id} ` : ""}{action.claim_id ? `claim:${action.claim_id} ` : ""}{action.source_span_id ? `span:${action.source_span_id}` : action.evidence_ref_id ? `evidence:${action.evidence_ref_id}` : "根拠未接続"}</p><div className="mt-3 flex flex-wrap gap-2"><input type="date" aria-label={`${action.title}の期限`} value={action.due_date ?? ""} disabled={!canWrite || busy === `action-${action.id}`} onChange={event => void updateAction(action, { due_date:event.target.value || null })} className="rounded-lg border bg-white px-2 py-1 text-[10px]"/><select value={action.status} disabled={!canWrite || busy === `action-${action.id}`} onChange={event => void updateAction(action, { status:event.target.value as ResearchAction["status"] })} className="rounded-lg border bg-white px-2 py-1 text-[10px]"><option value="open">未着手</option><option value="in_progress">進行中</option><option value="done">完了</option><option value="cancelled">中止</option></select><select value={action.human_decision} disabled={!canWrite || busy === `action-${action.id}`} onChange={event => void updateAction(action, { human_decision:event.target.value as ResearchAction["human_decision"] })} className="rounded-lg border bg-white px-2 py-1 text-[10px]"><option value="unreviewed">人間判断: 未確認</option><option value="accepted">採用</option><option value="held">保留</option><option value="rejected">却下</option></select></div></article>)}{!actions.length && <p className="rounded-2xl border border-dashed p-5 text-sm text-[#68736f]">Ideaを分解するか、Experiment PlanからActionを作成してください。</p>}</div></section>

    <section className="paper-card rounded-3xl p-6"><div className="mb-5 flex items-center gap-2"><BeakerIcon className="h-5 w-5 text-[#164f3b]"/><div><h2 className="serif text-2xl font-semibold">Experiment Plans</h2><p className="text-xs text-[#68736f]">意思決定基準と停止規則を先に固定し、結果をappend-only履歴へ追記します。</p></div></div>
      <details className="rounded-2xl border border-[#deddd5] bg-white/65 p-4"><summary className="cursor-pointer text-sm font-semibold text-[#164f3b]">新しい実験計画を作成</summary><form onSubmit={submitPlan} className="mt-4 grid gap-3 md:grid-cols-2"><select disabled={!canWrite} value={plan.hypothesis_card_id ?? ""} onChange={event => setPlan(current => ({ ...current, hypothesis_card_id:event.target.value || null }))} className="rounded-xl border bg-white px-3 py-2 text-sm"><option value="">仮説を選択（任意）</option>{hypotheses.map(card => <option key={card.id} value={card.id}>{card.claim.slice(0,80)}</option>)}</select>{(["intervention","measurement","controls","decision_threshold","stopping_rule","required_data","cost","competing_hypothesis_discrimination"] as const).map(field => <input key={field} required disabled={!canWrite} value={plan[field]} onChange={event => setPlan(current => ({ ...current, [field]:event.target.value }))} placeholder={({intervention:"操作・介入",measurement:"測定方法",controls:"対照条件",decision_threshold:"意思決定閾値",stopping_rule:"停止規則",required_data:"必要データ",cost:"コスト",competing_hypothesis_discrimination:"競合仮説の識別方法"} as const)[field]} className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/>)}<input value={confounders} disabled={!canWrite} onChange={event => setConfounders(event.target.value)} placeholder="交絡要因（カンマ区切り）" className="rounded-xl border bg-white px-3 py-2 text-sm"/><input value={predictions} disabled={!canWrite} onChange={event => setPredictions(event.target.value)} placeholder="予測（カンマ区切り）" className="rounded-xl border bg-white px-3 py-2 text-sm"/><input value={evidence} disabled={!canWrite} onChange={event => setEvidence(event.target.value)} placeholder="根拠ID・参照（カンマ区切り）" className="rounded-xl border bg-white px-3 py-2 text-sm md:col-span-2"/><button disabled={!canWrite || busy === "plan"} className="rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40 md:col-span-2">計画を保存</button></form></details>
      <div className="mt-5 grid gap-4 xl:grid-cols-2">{experiments.map(experiment => { const draft = results[experiment.id] ?? { outcome:"", interpretation:"" }; return <article key={experiment.id} className="rounded-2xl border border-[#deddd5] bg-white/70 p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-bold text-[#a06a28]">{experiment.hypothesis_card_id ? "仮説に接続済み" : "独立した実験計画"}</p><h3 className="mt-1 text-sm font-semibold">{experiment.intervention}</h3></div><button type="button" disabled={busy === `snapshot-${experiment.id}`} onClick={() => void downloadSnapshot(experiment)} className="inline-flex items-center gap-1 rounded-full border border-[#164f3b] px-3 py-1.5 text-[10px] font-semibold text-[#164f3b] disabled:opacity-40"><ArrowDownTrayIcon className="h-3 w-3"/>v1 snapshot</button></div><dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-xs"><dt className="font-bold">測定</dt><dd>{experiment.measurement}</dd><dt className="font-bold">閾値</dt><dd>{experiment.decision_threshold}</dd><dt className="font-bold">停止</dt><dd>{experiment.stopping_rule}</dd><dt className="font-bold">結果</dt><dd>{experiment.results?.length ?? 0}件</dd></dl><button type="button" disabled={!canWrite || busy === `experiment-action-${experiment.id}`} onClick={() => void createExperimentAction(experiment)} className="mt-3 rounded-full border border-[#164f3b] px-3 py-1.5 text-[10px] font-semibold text-[#164f3b] disabled:opacity-40">{busy === `experiment-action-${experiment.id}` ? "作成中…" : "実行Actionを作成"}</button><div className="mt-4 border-t pt-3"><input disabled={!canWrite} value={draft.outcome} onChange={event => setResults(current => ({ ...current, [experiment.id]:{ ...draft, outcome:event.target.value } }))} placeholder="観測結果（必須）" className="w-full rounded-xl border bg-white px-3 py-2 text-xs"/><textarea disabled={!canWrite} value={draft.interpretation} onChange={event => setResults(current => ({ ...current, [experiment.id]:{ ...draft, interpretation:event.target.value } }))} placeholder="解釈" rows={2} className="mt-2 w-full rounded-xl border bg-white px-3 py-2 text-xs"/><button type="button" disabled={!canWrite || !draft.outcome.trim() || busy === `result-${experiment.id}`} onClick={() => void recordResult(experiment)} className="mt-2 rounded-full bg-[#164f3b] px-3 py-1.5 text-[10px] font-semibold text-white disabled:opacity-40">結果を追記</button></div></article>; })}{!experiments.length && <p className="rounded-2xl border border-dashed p-5 text-sm text-[#68736f]">実験計画はまだありません。</p>}</div>
    </section>
  </section>;
}

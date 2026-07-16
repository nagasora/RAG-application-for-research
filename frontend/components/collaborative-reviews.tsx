"use client";

import { ArrowDownTrayIcon, ChatBubbleLeftRightIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  addReviewComment,
  addReviewDecision,
  assignReviewThread,
  createReviewThread,
  getReviewReport,
  getReviewThread,
  listReviewThreads,
  listWorkspaceMembers,
  type ReviewDecisionCreate,
  type ReviewThread,
  type ReviewThreadCreate,
  type WorkspaceMember,
} from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type CollaborativeReviewsProps = {
  workspaceId: string;
  canWrite: boolean;
};

type AnchorMode = "claim" | "evidence";
type Verdict = ReviewDecisionCreate["verdict"];

const verdictLabels: Record<Verdict, string> = {
  accepted: "承認",
  rejected: "却下",
  changes_requested: "修正依頼",
  needs_more_evidence: "根拠を追加",
};

function memberLabel(member: WorkspaceMember): string {
  return member.user.display_name || member.user.email || member.user.id;
}

type ClaimSnapshotView = {
  text: string;
  classification?: string;
  citationIds: string[];
};

function claimSnapshotView(thread: ReviewThread): ClaimSnapshotView | null {
  const snapshot = thread.claim_snapshot;
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return null;
  const text = typeof snapshot.text === "string" ? snapshot.text.trim() : "";
  if (!text) return null;
  const classification = typeof snapshot.classification === "string" ? snapshot.classification : undefined;
  const citationIds = Array.isArray(snapshot.citation_ids)
    ? snapshot.citation_ids.filter((id): id is string | number => typeof id === "string" || typeof id === "number").map(String)
    : [];
  return { text, classification, citationIds };
}

function anchorLabel(thread: ReviewThread): string {
  if (thread.evidence_link_id) return `EvidenceLink: ${thread.evidence_link_id}`;
  return claimSnapshotView(thread)?.text || `Run: ${thread.research_run_id ?? "-"} / Claim: ${thread.claim_id ?? "-"}`;
}

export function CollaborativeReviews({ workspaceId, canWrite }: CollaborativeReviewsProps) {
  const [threads, setThreads] = useState<ReviewThread[]>([]);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [selected, setSelected] = useState<ReviewThread | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [title, setTitle] = useState("");
  const [anchorMode, setAnchorMode] = useState<AnchorMode>("claim");
  const [researchRunId, setResearchRunId] = useState("");
  const [claimId, setClaimId] = useState("");
  const [evidenceLinkId, setEvidenceLinkId] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [comment, setComment] = useState("");
  const [verdict, setVerdict] = useState<Verdict>("accepted");
  const [reason, setReason] = useState("");

  const memberNames = useMemo(
    () => Object.fromEntries(members.map(member => [member.user.id, memberLabel(member)])),
    [members],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    Promise.all([listReviewThreads(controller.signal), listWorkspaceMembers(workspaceId, controller.signal)])
      .then(([nextThreads, nextMembers]) => {
        setThreads(nextThreads); setMembers(nextMembers);
        setSelected(current => current ? nextThreads.find(thread => thread.id === current.id) ?? null : nextThreads[0] ?? null);
      })
      .catch(requestError => {
        if (!controller.signal.aborted) setError(apiErrorMessage(requestError, "共同レビューを読み込めませんでした"));
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [workspaceId]);

  const replaceThread = (updated: ReviewThread) => {
    setThreads(current => current.map(thread => thread.id === updated.id ? updated : thread));
    setSelected(updated);
  };

  const openThread = async (threadId: string) => {
    setBusy(`detail-${threadId}`); setError("");
    try { setSelected(await getReviewThread(threadId)); }
    catch (requestError) { setError(apiErrorMessage(requestError, "レビュー詳細を取得できませんでした")); }
    finally { setBusy(""); }
  };

  const submitThread = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !title.trim()) return;
    const body: ReviewThreadCreate = anchorMode === "claim"
      ? { title:title.trim(), research_run_id:researchRunId.trim(), claim_id:claimId.trim(), assigned_to:assignedTo || null }
      : { title:title.trim(), evidence_link_id:evidenceLinkId.trim(), assigned_to:assignedTo || null };
    setBusy("create"); setError(""); setNotice("");
    try {
      const created = await createReviewThread(body);
      setThreads(current => [created, ...current]); setSelected(created);
      setTitle(""); setResearchRunId(""); setClaimId(""); setEvidenceLinkId("");
      setNotice("共同レビューを作成しました。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "共同レビューを作成できませんでした")); }
    finally { setBusy(""); }
  };

  const updateAssignment = async (nextAssignedTo: string) => {
    if (!canWrite || !selected) return;
    setBusy("assignment"); setError("");
    try { replaceThread(await assignReviewThread(selected.id, { assigned_to:nextAssignedTo || null })); }
    catch (requestError) { setError(apiErrorMessage(requestError, "担当者を更新できませんでした")); }
    finally { setBusy(""); }
  };

  const submitComment = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !selected || !comment.trim()) return;
    setBusy("comment"); setError("");
    try { replaceThread(await addReviewComment(selected.id, { body:comment.trim() })); setComment(""); }
    catch (requestError) { setError(apiErrorMessage(requestError, "コメントを追加できませんでした")); }
    finally { setBusy(""); }
  };

  const submitDecision = async (event: FormEvent) => {
    event.preventDefault();
    if (!canWrite || !selected || !reason.trim()) return;
    setBusy("decision"); setError(""); setNotice("");
    try {
      replaceThread(await addReviewDecision(selected.id, { verdict, reason:reason.trim() }));
      setReason(""); setNotice("レビュー判断を記録しました。");
    } catch (requestError) { setError(apiErrorMessage(requestError, "レビュー判断を記録できませんでした")); }
    finally { setBusy(""); }
  };

  const downloadReport = async () => {
    setBusy("report"); setError("");
    try {
      const { blob, filename } = await getReviewReport();
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    } catch (requestError) { setError(apiErrorMessage(requestError, "レビューレポートを出力できませんでした")); }
    finally { setBusy(""); }
  };

  const anchorReady = anchorMode === "claim"
    ? Boolean(researchRunId.trim() && claimId.trim())
    : Boolean(evidenceLinkId.trim());
  const selectedClaimSnapshot = selected ? claimSnapshotView(selected) : null;

  return <section className="paper-card mb-6 rounded-3xl p-6">
    <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
      <div><div className="flex items-center gap-2"><ChatBubbleLeftRightIcon className="h-5 w-5 text-[#164f3b]"/><h2 className="serif text-2xl font-semibold">共同レビュー</h2></div><p className="mt-2 text-xs leading-5 text-[#68736f]">主張または EvidenceLink を起点に、担当・議論・判断を監査可能な形で残します。</p></div>
      <button type="button" onClick={downloadReport} disabled={busy === "report"} className="inline-flex items-center gap-2 rounded-full border border-[#164f3b] px-4 py-2 text-xs font-semibold text-[#164f3b] disabled:opacity-40"><ArrowDownTrayIcon className="h-4 w-4"/>引用付き Markdown レポート</button>
    </div>
    {!canWrite && <p className="mb-4 rounded-xl bg-amber-50 p-3 text-xs text-amber-800">viewer はレビュー一覧・詳細・判断履歴・レポートを閲覧できます。</p>}
    {error && <div role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}
    {notice && <div role="status" className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</div>}

    {canWrite && <form onSubmit={submitThread} className="mb-6 rounded-2xl border border-[#deddd5] bg-white/65 p-4">
      <h3 className="mb-3 text-sm font-semibold">レビューを開始</h3>
      <div className="grid gap-3 md:grid-cols-2">
        <input aria-label="レビュー名" maxLength={255} value={title} onChange={event => setTitle(event.target.value)} placeholder="レビュー名" className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/>
        <select aria-label="アンカー種別" value={anchorMode} onChange={event => setAnchorMode(event.target.value as AnchorMode)} className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"><option value="claim">Research Run の主張</option><option value="evidence">EvidenceLink</option></select>
        {anchorMode === "claim" ? <><input aria-label="Research Run ID" value={researchRunId} onChange={event => setResearchRunId(event.target.value)} placeholder="Research Run ID" className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/><input aria-label="Claim ID" maxLength={128} value={claimId} onChange={event => setClaimId(event.target.value)} placeholder="Claim ID" className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/></> : <input aria-label="EvidenceLink ID" value={evidenceLinkId} onChange={event => setEvidenceLinkId(event.target.value)} placeholder="EvidenceLink ID" className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/>}
        <select aria-label="初期担当者" value={assignedTo} onChange={event => setAssignedTo(event.target.value)} className="rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"><option value="">未割り当て</option>{members.map(member => <option key={member.user.id} value={member.user.id}>{memberLabel(member)} ({member.role})</option>)}</select>
      </div>
      <button disabled={!title.trim() || !anchorReady || busy === "create"} className="mt-3 rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">レビューを作成</button>
    </form>}

    {loading ? <p role="status" className="py-10 text-center text-sm text-[#68736f]">共同レビューを読み込んでいます…</p> : <div className="grid gap-5 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
      <div className="max-h-[680px] space-y-2 overflow-y-auto pr-1">{threads.length ? threads.map(thread => <button type="button" key={thread.id} onClick={() => openThread(thread.id)} disabled={busy === `detail-${thread.id}`} className={`block w-full rounded-2xl border p-4 text-left transition ${selected?.id === thread.id ? "border-[#164f3b] bg-[#edf5f0]" : "border-[#deddd5] bg-white/65 hover:border-[#9aac9f]"}`}><div className="flex items-start justify-between gap-3"><h3 className="text-sm font-semibold">{thread.title}</h3><span className={`rounded-full px-2 py-1 text-[10px] font-bold ${thread.status === "resolved" ? "bg-[#e2eee7] text-[#35634f]" : "bg-amber-50 text-amber-800"}`}>{thread.status === "resolved" ? "解決済み" : "未解決"}</span></div><p className="mt-2 line-clamp-2 text-[11px] leading-5 text-[#68736f]">{anchorLabel(thread)}</p>{thread.claim_snapshot && <p className="mt-1 text-[10px] font-semibold text-[#35634f]">保存時点の主張スナップショット</p>}<p className="mt-1 text-[10px] text-[#89918e]">担当: {thread.assigned_to ? memberNames[thread.assigned_to] ?? thread.assigned_to : "未割り当て"} · {new Date(thread.updated_at).toLocaleString("ja-JP")}</p></button>) : <p className="rounded-2xl border border-dashed border-[#cfd4cf] p-6 text-center text-sm text-[#68736f]">共同レビューはまだありません。</p>}</div>

      {selected ? <article className="rounded-2xl border border-[#deddd5] bg-white/65 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="serif text-xl font-semibold">{selected.title}</h3>{selected.evidence_link_id && <p className="mt-1 break-all text-xs text-[#68736f]">{anchorLabel(selected)}</p>}</div><span className="rounded-full bg-[#eef1ee] px-2.5 py-1 text-[10px] font-bold">{selected.status === "resolved" ? "解決済み" : "未解決"}</span></div>
        {selectedClaimSnapshot && <section aria-label="保存時点の主張" className="mt-4 rounded-2xl border border-[#b9d4c5] bg-[#edf7f1] p-4"><div className="flex flex-wrap items-center gap-2"><h4 className="text-xs font-bold text-[#23513e]">保存時点の主張</h4><span className="rounded-full bg-white/75 px-2 py-0.5 text-[10px] font-semibold text-[#35634f]">immutable snapshot</span>{selectedClaimSnapshot.classification && <span className="rounded-full bg-white/75 px-2 py-0.5 text-[10px] text-[#52605b]">{selectedClaimSnapshot.classification}</span>}</div><p className="mt-2 whitespace-pre-wrap text-sm font-medium leading-6 text-[#26342e]">{selectedClaimSnapshot.text}</p>{selectedClaimSnapshot.citationIds.length ? <p className="mt-2 text-[11px] text-[#52605b]">引用: {selectedClaimSnapshot.citationIds.join(", ")}</p> : null}<p className="mt-2 break-all text-[10px] text-[#68736f]">Run {selected.research_run_id} · Claim {selected.claim_id}{selected.claim_artifact_id ? ` · Artifact ${selected.claim_artifact_id}` : ""}</p></section>}
        {!selected.evidence_link_id && !selectedClaimSnapshot && <p className="mt-3 break-all rounded-xl bg-amber-50 p-3 text-xs text-amber-800">主張スナップショットを表示できません。{anchorLabel(selected)}</p>}
        <label className="mt-5 block text-xs font-semibold text-[#52605b]">担当者<select aria-label="レビュー担当者" disabled={!canWrite || busy === "assignment"} value={selected.assigned_to ?? ""} onChange={event => updateAssignment(event.target.value)} className="mt-1 block w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm disabled:opacity-60"><option value="">未割り当て</option>{members.map(member => <option key={member.user.id} value={member.user.id}>{memberLabel(member)} ({member.role})</option>)}</select></label>

        <div className="mt-6"><h4 className="text-sm font-semibold">コメント</h4><div className="mt-2 space-y-2">{selected.comments?.length ? selected.comments.map(item => <div key={item.id} className="rounded-xl bg-[#f3f4f0] p-3"><p className="whitespace-pre-wrap text-sm leading-6">{item.body}</p><p className="mt-1 text-[10px] text-[#89918e]">{item.author_id ? memberNames[item.author_id] ?? item.author_id : "不明"} · {new Date(item.created_at).toLocaleString("ja-JP")}</p></div>) : <p className="text-xs text-[#89918e]">コメントはありません。</p>}</div>{canWrite && <form onSubmit={submitComment} className="mt-3 flex gap-2"><textarea aria-label="レビューコメント" maxLength={20000} rows={2} value={comment} onChange={event => setComment(event.target.value)} placeholder="確認事項や修正案" className="min-w-0 flex-1 rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/><button disabled={!comment.trim() || busy === "comment"} className="self-end rounded-full border border-[#164f3b] px-4 py-2 text-xs font-semibold text-[#164f3b] disabled:opacity-40">追加</button></form>}</div>

        <div className="mt-6"><h4 className="text-sm font-semibold">owner / editor の判断履歴</h4><div className="mt-2 space-y-2">{selected.decisions?.length ? selected.decisions.map(item => <div key={item.id} className="rounded-xl border border-[#deddd5] p-3"><p className="text-xs font-bold text-[#164f3b]">{verdictLabels[item.verdict]}</p><p className="mt-1 whitespace-pre-wrap text-sm leading-6">{item.reason}</p><p className="mt-1 text-[10px] text-[#89918e]">{item.decided_by ? memberNames[item.decided_by] ?? item.decided_by : "不明"} · {new Date(item.created_at).toLocaleString("ja-JP")}</p></div>) : <p className="text-xs text-[#89918e]">判断はまだ記録されていません。</p>}</div>{canWrite && <form onSubmit={submitDecision} className="mt-3 space-y-2"><select aria-label="レビュー判断" value={verdict} onChange={event => setVerdict(event.target.value as Verdict)} className="w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm">{Object.entries(verdictLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><textarea aria-label="判断理由" maxLength={20000} rows={2} value={reason} onChange={event => setReason(event.target.value)} placeholder="判断理由（必須）" className="w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/><button disabled={!reason.trim() || busy === "decision"} className="rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">判断を記録</button></form>}</div>
      </article> : <div className="grid min-h-56 place-items-center rounded-2xl border border-dashed border-[#cfd4cf] text-sm text-[#68736f]">一覧からレビューを選択してください。</div>}
    </div>}
  </section>;
}

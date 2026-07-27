"use client";

import { LightBulbIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useState } from "react";

import { createIdea, type IdeaCreate } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type IdeaCaptureProps = {
  canWrite: boolean;
  context: string;
};

type IdeaKind = IdeaCreate["kind"];

function parseAnchor(value: string): Pick<IdeaCreate, "research_run_id" | "claim_id" | "paper_id" | "source_span_id"> | null {
  const trimmed = value.trim();
  if (!trimmed) return {};
  const match = trimmed.match(/^(run|claim|paper|span):(.+)$/);
  if (!match || !match[2].trim()) return null;
  const id = match[2].trim();
  if (match[1] === "run") return { research_run_id:id };
  if (match[1] === "claim") return { claim_id:id };
  if (match[1] === "paper") return { paper_id:id };
  return { source_span_id:id };
}

export function IdeaCapture({ canWrite, context }: IdeaCaptureProps) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [category, setCategory] = useState<IdeaKind>("observation");
  const [anchor, setAnchor] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        if (canWrite) setOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    const openCapture = () => { if (canWrite) setOpen(true); };
    window.addEventListener("paperpilot:open-idea-capture", openCapture);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("paperpilot:open-idea-capture", openCapture);
    };
  }, [canWrite]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!content.trim() || saving || !canWrite) return;
    const parsedAnchor = parseAnchor(anchor);
    if (!parsedAnchor) { setMessage("アンカーは run:ID / claim:ID / paper:ID / span:ID の形式で入力してください。"); return; }
    setSaving(true); setMessage("");
    try {
      const created = await createIdea({
        kind:category, content:content.trim(), ...parsedAnchor,
        checklist:{ evidence:false, falsifier:false, test:false, captured_from:context },
      });
      setContent(""); setAnchor(""); setOpen(false);
      window.dispatchEvent(new CustomEvent("paperpilot:idea-created", { detail:{ ideaId:created.id } }));
      setMessage("アイデアを受信箱に保存しました。「整理・履歴」画面で確認できます。");
    } catch (error) {
      setMessage(apiErrorMessage(error, "アイデアを保存できませんでした"));
    } finally { setSaving(false); }
  };

  return <>
    {message && <div role="status" className="fixed bottom-36 right-4 z-50 max-w-sm rounded-2xl border border-[#b9d4c5] bg-[#edf7f1] px-4 py-3 text-xs leading-5 text-[#23513e] shadow-xl md:bottom-20 md:right-6">{message}</div>}
    <button type="button" onClick={() => setOpen(true)} disabled={!canWrite} aria-label="アイデアを残す（Ctrl+J）" className="fixed bottom-24 right-4 z-40 inline-flex items-center gap-2 rounded-full bg-[#164f3b] px-4 py-3 text-sm font-bold text-white shadow-lg transition hover:-translate-y-0.5 hover:bg-[#0e392a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#164f3b] disabled:cursor-not-allowed disabled:opacity-50 md:bottom-6 md:right-6">
      <LightBulbIcon className="h-5 w-5" /> <span className="hidden sm:inline">考えを残す</span>
    </button>
    {open && <div role="dialog" aria-modal="true" aria-labelledby="idea-capture-title" className="fixed inset-0 z-50 grid place-items-end bg-[#07130d]/50 p-4 sm:place-items-center" onMouseDown={() => !saving && setOpen(false)}>
      <form onSubmit={save} onMouseDown={event => event.stopPropagation()} className="w-full max-w-lg rounded-3xl border border-[#d8ded9] bg-[#fffefa] p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-bold tracking-[.16em] text-[#a06a28]">アイデア受信箱</p><h2 id="idea-capture-title" className="serif mt-1 text-2xl font-semibold">考えをメモする</h2><p className="mt-2 text-xs leading-5 text-[#68736f]">まずは受信箱に保存します。あとから根拠を確認し、研究アイデアとして整理できます。</p></div><button type="button" onClick={() => setOpen(false)} disabled={saving} aria-label="閉じる" className="rounded-full p-2 text-[#68736f] hover:bg-[#edf0eb]"><XMarkIcon className="h-5 w-5" /></button></div>
        <label className="mt-5 block text-xs font-semibold text-[#52605b]" htmlFor="idea-kind">種別</label>
        <select id="idea-kind" value={category} onChange={event => setCategory(event.target.value as IdeaKind)} disabled={saving} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"><option value="observation">観察</option><option value="interpretation">解釈</option><option value="hypothesis">仮説</option><option value="falsifier">反証候補</option><option value="todo">TODO</option></select>
        <label className="mt-4 block text-xs font-semibold text-[#52605b]" htmlFor="idea-anchor">根拠へのリンク（任意）</label><input id="idea-anchor" value={anchor} onChange={event => setAnchor(event.target.value)} maxLength={500} placeholder="通常は空欄で保存。IDを使う場合: paper:ID / span:ID / claim:ID / run:ID" className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm"/>
        <label className="mt-4 block text-xs font-semibold text-[#52605b]" htmlFor="idea-content">内容</label>
        <textarea id="idea-content" autoFocus value={content} onChange={event => setContent(event.target.value)} maxLength={100000} rows={6} placeholder="気づき、仮説、反証したい点、次の実験案など…" className="mt-1 w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-3 text-sm leading-6" />
        <p className="mt-2 text-[11px] text-[#7a837f]">未検証として保存します。根拠接続・反証検索・実験化・研究者確認を完了してから仮説へ昇格してください。保存元: {context}</p>
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setOpen(false)} disabled={saving} className="rounded-full px-4 py-2 text-sm text-[#52605b]">あとで</button><button disabled={!content.trim() || saving} className="rounded-full bg-[#164f3b] px-5 py-2 text-sm font-semibold text-white disabled:opacity-40">{saving ? "保存中…" : "受信箱に保存"}</button></div>
      </form>
    </div>}
  </>;
}

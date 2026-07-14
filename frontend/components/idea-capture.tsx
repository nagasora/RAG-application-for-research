"use client";

import { LightBulbIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useState } from "react";

import { createGraphNode } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type IdeaCaptureProps = {
  canWrite: boolean;
  context: string;
};

const TYPES = [
  ["idea", "アイデア"],
  ["hypothesis", "仮説"],
  ["constraint", "制約・注意"],
] as const;

export function IdeaCapture({ canWrite, context }: IdeaCaptureProps) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState<(typeof TYPES)[number][0]>("idea");
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
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canWrite]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!content.trim() || saving || !canWrite) return;
    setSaving(true); setMessage("");
    try {
      await createGraphNode({
        node_type: kind, content: content.trim(), layer: 1, status: "review_pending",
        phase: "inbox", evidence_excerpt: "", evidence_span_ids: [],
        metadata: { captured_from: context },
      });
      setContent(""); setOpen(false);
      setMessage("アイデア受信箱に保存しました。後でグラフから根拠とつなげられます。");
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
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-[.16em] text-[#a06a28]">Research inbox</p><h2 id="idea-capture-title" className="serif mt-1 text-2xl font-semibold">いまの考えを残す</h2><p className="mt-2 text-xs leading-5 text-[#68736f]">まず受信箱へ保存します。根拠や関係は後から確認してつなげます。</p></div><button type="button" onClick={() => setOpen(false)} disabled={saving} aria-label="閉じる" className="rounded-full p-2 text-[#68736f] hover:bg-[#edf0eb]"><XMarkIcon className="h-5 w-5" /></button></div>
        <label className="mt-5 block text-xs font-semibold text-[#52605b]" htmlFor="idea-kind">種別</label>
        <select id="idea-kind" value={kind} onChange={event => setKind(event.target.value as typeof kind)} disabled={saving} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm">{TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
        <label className="mt-4 block text-xs font-semibold text-[#52605b]" htmlFor="idea-content">内容</label>
        <textarea id="idea-content" autoFocus value={content} onChange={event => setContent(event.target.value)} maxLength={100000} rows={6} placeholder="気づき、仮説、反証したい点、次の実験案など…" className="mt-1 w-full resize-y rounded-xl border border-[#d5d8d2] bg-white px-3 py-3 text-sm leading-6" />
        <p className="mt-2 text-[11px] text-[#7a837f]">保存元: {context} · Ctrl / ⌘ + J でいつでも開けます</p>
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setOpen(false)} disabled={saving} className="rounded-full px-4 py-2 text-sm text-[#52605b]">あとで</button><button disabled={!content.trim() || saving} className="rounded-full bg-[#164f3b] px-5 py-2 text-sm font-semibold text-white disabled:opacity-40">{saving ? "保存中…" : "受信箱に保存"}</button></div>
      </form>
    </div>}
  </>;
}

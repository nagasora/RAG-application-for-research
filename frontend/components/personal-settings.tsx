"use client";

import { Cog6ToothIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useEffect, useRef, useState } from "react";

import { ThemeToggle } from "@/components/theme-toggle";

const FOCUSABLE = "button:not([disabled]), [tabindex]:not([tabindex='-1'])";

/** A small personal-only control surface. New preference tabs belong here instead of floating controls. */
export function PersonalSettings() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); }
      if (event.key !== "Tab" || !panelRef.current) return;
      const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) return;
      const first = items[0]; const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    window.requestAnimationFrame(() => panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus());
    return () => { document.removeEventListener("keydown", onKeyDown); (previouslyFocused ?? triggerRef.current)?.focus(); };
  }, [open]);

  return <>
    <button ref={triggerRef} type="button" onClick={() => setOpen(true)} aria-label="個人設定を開く" aria-expanded={open} className="fixed bottom-24 left-4 z-40 grid h-11 w-11 place-items-center rounded-full border border-[#bfc9c2] bg-[#fffefa]/95 text-[#164f3b] shadow-lg backdrop-blur transition hover:-translate-y-0.5 hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#164f3b] md:bottom-6 md:left-6">
      <Cog6ToothIcon className="h-5 w-5" aria-hidden="true" />
    </button>
    {open && <div className="fixed inset-0 z-[100] flex justify-end bg-[#07130d]/45 backdrop-blur-sm" onMouseDown={() => setOpen(false)}>
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby="personal-settings-title" onMouseDown={event => event.stopPropagation()} className="flex h-full w-full max-w-md flex-col border-l border-[#d8ded9] bg-[#fffefa] shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-[#deddd5] px-6 py-5"><div><p className="text-[10px] font-bold uppercase tracking-[.16em] text-[#a06a28]">Personal settings</p><h2 id="personal-settings-title" className="serif mt-1 text-2xl font-semibold">個人設定</h2><p className="mt-2 text-xs leading-5 text-[#68736f]">表示や操作の好みは、この端末のブラウザに保存されます。</p></div><button type="button" onClick={() => setOpen(false)} aria-label="個人設定を閉じる" className="grid h-9 w-9 place-items-center rounded-full border border-[#d5d8d2] text-[#52605b] hover:bg-[#edf0eb]"><XMarkIcon className="h-5 w-5"/></button></header>
        <div className="flex min-h-0 flex-1"><nav aria-label="個人設定の項目" className="w-28 shrink-0 border-r border-[#deddd5] bg-[#f4f6f3] p-3"><button type="button" aria-current="page" className="w-full rounded-xl bg-[#e1eee7] px-3 py-2 text-left text-xs font-bold text-[#164f3b]">表示</button></nav><section className="min-w-0 flex-1 overflow-y-auto p-6" aria-labelledby="appearance-title"><h3 id="appearance-title" className="text-sm font-bold text-[#26342e]">テーマ</h3><p className="mt-1 text-xs leading-5 text-[#68736f]">見やすい表示テーマを選びます。端末に合わせる場合はOS設定の変更にも追従します。</p><div className="mt-4"><ThemeToggle /></div></section></div>
      </div>
    </div>}
  </>;
}

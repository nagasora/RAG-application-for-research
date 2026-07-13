"use client";

import {
  CheckIcon, ChevronDownIcon, ClockIcon, FolderIcon, PencilSquareIcon, PlusIcon, XMarkIcon,
} from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Me, Workspace } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type WorkspaceSwitcherProps = {
  me: Me; workspaces: Workspace[]; activeWorkspace: Workspace;
  creating: boolean; renaming: boolean;
  onSelect: (workspaceId: string) => void;
  onCreate: (name: string) => Promise<void>;
  onRename: (workspaceId: string, name: string) => Promise<void>;
};
type Panel = "projects" | "create" | "rename" | null;
type AccessMap = Record<string, string>;

const ACCESS_KEY = "paperpilot.project-access";
const FOCUSABLE = "button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])";

function readAccessMap(): AccessMap {
  if (typeof window === "undefined") return {};
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(ACCESS_KEY) ?? "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
  } catch { return {}; }
}

function accessLabel(value: string | undefined, fallback: string) {
  const date = new Date(value ?? fallback); const elapsed = Date.now() - date.getTime();
  if (!Number.isFinite(elapsed)) return "アクセス履歴なし";
  if (elapsed < 60_000) return "たった今アクセス";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}分前にアクセス`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}時間前にアクセス`;
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)}日前にアクセス`;
  return `${date.toLocaleDateString("ja-JP")}にアクセス`;
}

export function WorkspaceSwitcher({ me, workspaces, activeWorkspace, creating, renaming, onSelect, onCreate, onRename }: WorkspaceSwitcherProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [createName, setCreateName] = useState("");
  const [renameName, setRenameName] = useState("");
  const [renameTarget, setRenameTarget] = useState<Workspace | null>(null);
  const [accesses, setAccesses] = useState<AccessMap>({});
  const [error, setError] = useState("");

  useEffect(() => {
    const next = { ...readAccessMap(), [activeWorkspace.id]:new Date().toISOString() };
    setAccesses(next);
    try { window.localStorage.setItem(ACCESS_KEY, JSON.stringify(next)); } catch { /* browser storage may be unavailable */ }
  }, [activeWorkspace.id]);

  useEffect(() => {
    if (!panel) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setPanel(null); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!focusable.length) { event.preventDefault(); return; }
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); document.body.style.overflow = previousOverflow; triggerRef.current?.focus(); };
  }, [panel]);

  const ordered = useMemo(() => [...workspaces].sort((left, right) => {
    if (left.id === activeWorkspace.id) return -1;
    if (right.id === activeWorkspace.id) return 1;
    return new Date(accesses[right.id] ?? right.created_at).getTime() - new Date(accesses[left.id] ?? left.created_at).getTime();
  }), [workspaces, activeWorkspace.id, accesses]);
  const closePanel = () => { setPanel(null); setError(""); };
  const openRename = (workspace: Workspace) => { setRenameTarget(workspace); setRenameName(workspace.name); setError(""); setPanel("rename"); };
  const selectProject = (workspaceId: string) => {
    const next = { ...accesses, [workspaceId]:new Date().toISOString() }; setAccesses(next);
    try { window.localStorage.setItem(ACCESS_KEY, JSON.stringify(next)); } catch { /* no-op */ }
    onSelect(workspaceId); closePanel();
  };
  const submitCreate = async (event: FormEvent) => {
    event.preventDefault(); if (!createName.trim() || creating) return; setError("");
    try { await onCreate(createName.trim()); setCreateName(""); setPanel(null); }
    catch (requestError) { setError(apiErrorMessage(requestError, "プロジェクトを作成できませんでした")); }
  };
  const submitRename = async (event: FormEvent) => {
    event.preventDefault(); const nextName = renameName.trim();
    if (!renameTarget || !nextName || nextName === renameTarget.name || renaming) return; setError("");
    try { await onRename(renameTarget.id, nextName); setRenameTarget(null); setPanel("projects"); }
    catch (requestError) { setError(apiErrorMessage(requestError, "プロジェクト名を変更できませんでした")); }
  };

  const displayName = me.user.display_name || me.user.email || me.user.subject;
  const modal = panel && typeof document !== "undefined" ? createPortal(
    <div className="fixed inset-0 z-[110] isolate flex items-end justify-end bg-[#07110d] sm:items-center sm:justify-center sm:p-6" onMouseDown={event => { if (event.target === event.currentTarget) closePanel(); }}>
      <div ref={dialogRef} id="project-switcher-panel" role="dialog" aria-modal="true" aria-labelledby="project-dialog-title" className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-3xl border border-[#d8ddd9] bg-[#fffefa] text-[#17201d] shadow-[0_30px_100px_rgba(0,0,0,.5)] sm:max-w-2xl sm:rounded-3xl">
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-[#d9ddd8] bg-[#f4f6f2] px-5 py-4 sm:px-6"><div><p className="text-[10px] font-bold uppercase tracking-[.2em] text-[#527264]">Project navigator</p><h2 id="project-dialog-title" className="serif mt-1 text-2xl font-semibold">{panel === "create" ? "新しい研究プロジェクト" : panel === "rename" ? "プロジェクト名を変更" : "研究プロジェクトを開く"}</h2><p className="mt-1 text-xs leading-5 text-[#52605b]">論文、研究対話、ノートはプロジェクトごとに分離されます。</p></div><button type="button" onClick={closePanel} aria-label="プロジェクト画面を閉じる" className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-[#ccd2cd] bg-white text-[#394640] hover:bg-[#e8ece8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><XMarkIcon className="h-5 w-5"/></button></header>

        {panel === "projects" && <><div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 sm:p-6"><div className="mb-3 flex items-center justify-between"><h3 className="text-xs font-bold uppercase tracking-[.16em] text-[#5f6d67]">最近アクセスしたプロジェクト</h3><span className="rounded-full bg-[#e7ede9] px-2.5 py-1 text-[10px] font-semibold text-[#46564f]">{ordered.length} projects</span></div><div className="grid gap-2">{ordered.map(workspace => <div key={workspace.id} className={`group flex items-center gap-2 rounded-2xl border p-2 ${workspace.id === activeWorkspace.id ? "border-[#78a18d] bg-[#e8f2ec]" : "border-[#d9ddd8] bg-white hover:border-[#9eb3a8]"}`}><button type="button" onClick={() => selectProject(workspace.id)} className="flex min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${workspace.id === activeWorkspace.id ? "bg-[#164f3b] text-white" : "bg-[#edf0ed] text-[#42534b]"}`}><FolderIcon className="h-5 w-5"/></span><span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className="truncate text-sm font-bold text-[#1d2924]">{workspace.name}</span>{workspace.is_personal && <span className="rounded-md bg-[#eef0ed] px-1.5 py-0.5 text-[9px] font-semibold text-[#52605b]">個人</span>}</span><span className="mt-1 flex flex-wrap items-center gap-1 text-[10px] font-medium text-[#65736d]"><ClockIcon className="h-3 w-3"/>{workspace.id === activeWorkspace.id ? "現在開いています" : accessLabel(accesses[workspace.id], workspace.created_at)}<span aria-hidden="true">·</span>{workspace.role}</span></span>{workspace.id === activeWorkspace.id && <span className="flex shrink-0 items-center gap-1 rounded-full bg-white px-2 py-1 text-[10px] font-bold text-[#164f3b]"><CheckIcon className="h-3 w-3"/>選択中</span>}</button>{workspace.role === "owner" && <button type="button" onClick={() => openRename(workspace)} aria-label={`${workspace.name}の名前を変更`} className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-transparent text-[#52605b] hover:border-[#ccd2cd] hover:bg-white hover:text-[#164f3b] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><PencilSquareIcon className="h-4 w-4"/></button>}</div>)}</div></div><footer className="shrink-0 border-t border-[#d9ddd8] bg-[#f4f6f2] p-4 sm:px-6"><button type="button" onClick={() => { setPanel("create"); setError(""); }} className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#164f3b] px-4 py-3 text-sm font-bold text-white shadow-lg shadow-[#164f3b]/15 hover:bg-[#0f3e2e]"><PlusIcon className="h-4 w-4"/>新しい研究プロジェクトを作成</button></footer></>}

        {panel === "create" && <form onSubmit={submitCreate} className="overflow-y-auto p-5 sm:p-7"><label htmlFor="project-create-name" className="text-sm font-bold text-[#26342e]">プロジェクト名</label><input id="project-create-name" autoFocus maxLength={255} value={createName} onChange={event => setCreateName(event.target.value)} placeholder="例：視覚言語モデルの再現研究" className="mt-2 w-full rounded-xl border border-[#bfc8c1] bg-white px-4 py-3 text-base text-[#17201d] outline-none placeholder:text-[#8c9791] focus:border-[#42705b] focus:ring-2 focus:ring-[#cfe3d8]"/><p className="mt-3 rounded-xl bg-[#eef3ef] p-3 text-xs leading-6 text-[#46564f]">作成すると新しいプロジェクトへ切り替わります。現在のプロジェクトの論文や対話は移動しません。</p><div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><button type="button" onClick={() => setPanel("projects")} className="rounded-full border border-[#bfc8c1] px-5 py-2.5 text-sm font-semibold text-[#394640]">一覧へ戻る</button><button disabled={creating || !createName.trim()} className="rounded-full bg-[#164f3b] px-6 py-2.5 text-sm font-bold text-white disabled:opacity-40">{creating ? "作成しています…" : "作成して開く"}</button></div>{error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}</form>}

        {panel === "rename" && renameTarget && <form onSubmit={submitRename} className="overflow-y-auto p-5 sm:p-7"><div className="mb-5 rounded-xl border border-[#d9ddd8] bg-[#f2f4f1] p-4"><p className="text-[10px] font-bold uppercase tracking-wider text-[#68736f]">変更対象</p><p className="mt-1 text-sm font-bold text-[#26342e]">{renameTarget.name}</p></div><label htmlFor="project-rename-name" className="text-sm font-bold text-[#26342e]">新しい名前</label><input id="project-rename-name" autoFocus maxLength={255} value={renameName} onChange={event => setRenameName(event.target.value)} className="mt-2 w-full rounded-xl border border-[#bfc8c1] bg-white px-4 py-3 text-base text-[#17201d] outline-none focus:border-[#42705b] focus:ring-2 focus:ring-[#cfe3d8]"/><div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><button type="button" onClick={() => setPanel("projects")} className="rounded-full border border-[#bfc8c1] px-5 py-2.5 text-sm font-semibold text-[#394640]">一覧へ戻る</button><button disabled={renaming || !renameName.trim() || renameName.trim() === renameTarget.name} className="rounded-full bg-[#164f3b] px-6 py-2.5 text-sm font-bold text-white disabled:opacity-40">{renaming ? "変更しています…" : "名前を変更"}</button></div>{error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}</form>}
      </div>
    </div>, document.body,
  ) : null;

  return <div className="flex items-center gap-2"><button ref={triggerRef} type="button" onClick={() => { setError(""); setPanel("projects"); }} aria-haspopup="dialog" aria-expanded={panel !== null} aria-controls="project-switcher-panel" className="flex min-w-0 max-w-56 items-center gap-2 rounded-full border border-[#cbd2cc] bg-white px-3 py-2 text-left text-xs font-bold text-[#26342e] shadow-sm hover:border-[#7f9b8d] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#164f3b]"><FolderIcon className="h-4 w-4 shrink-0 text-[#164f3b]"/><span className="min-w-0 flex-1 truncate"><span className="block text-[9px] font-semibold uppercase tracking-wider text-[#718078]">Project</span>{activeWorkspace.name}</span><ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-[#52605b]"/></button><div title={displayName} aria-label={`ログイン中: ${displayName}`} className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#c27d2c] text-sm font-bold text-white">{displayName.slice(0, 1).toUpperCase()}</div>{modal}</div>;
}

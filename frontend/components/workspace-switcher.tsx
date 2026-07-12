"use client";

import { PlusIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { FormEvent, useState } from "react";

import type { Me, Workspace } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type WorkspaceSwitcherProps = {
  me: Me;
  workspaces: Workspace[];
  activeWorkspace: Workspace;
  creating: boolean;
  onSelect: (workspaceId: string) => void;
  onCreate: (name: string) => Promise<void>;
};

export function WorkspaceSwitcher({ me, workspaces, activeWorkspace, creating, onSelect, onCreate }: WorkspaceSwitcherProps) {
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!name.trim()) return;
    setError("");
    try { await onCreate(name.trim()); setName(""); setShowCreate(false); }
    catch (requestError) { setError(apiErrorMessage(requestError, "ワークスペースを作成できませんでした")); }
  };

  const displayName = me.user.display_name || me.user.email || me.user.subject;

  return <div className="relative flex items-center gap-2">
    <label className="sr-only" htmlFor="workspace-select">ワークスペース</label>
    <select id="workspace-select" value={activeWorkspace.id} onChange={event => onSelect(event.target.value)} className="max-w-44 rounded-full border border-[#d8dad4] bg-white px-3 py-2 text-xs font-semibold text-[#394640] outline-none focus:border-[#6c887a]">
      {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
    </select>
    <button onClick={() => { setShowCreate(value => !value); setError(""); }} aria-expanded={showCreate} aria-controls="workspace-create-panel" aria-label="ワークスペースを作成" className="grid h-9 w-9 place-items-center rounded-full border border-[#d8dad4] bg-white text-[#164f3b]"><PlusIcon className="h-4 w-4" /></button>
    <div title={displayName} aria-label={`ログイン中: ${displayName}`} className="grid h-9 w-9 place-items-center rounded-full bg-[#d79a4a] text-sm font-bold text-white">{displayName.slice(0, 1).toUpperCase()}</div>
    {showCreate && <div id="workspace-create-panel" className="absolute right-0 top-12 z-50 w-80 rounded-2xl border border-[#d8dad4] bg-[#fffefa] p-4 shadow-xl">
      <div className="mb-3 flex items-center justify-between"><p className="text-sm font-semibold">新しいワークスペース</p><button onClick={() => setShowCreate(false)} aria-label="作成パネルを閉じる"><XMarkIcon className="h-4 w-4" /></button></div>
      <form onSubmit={submit}><label htmlFor="workspace-name" className="text-xs text-[#68736f]">名前</label><input id="workspace-name" autoFocus maxLength={255} value={name} onChange={event => setName(event.target.value)} className="mt-1 w-full rounded-xl border border-[#d5d8d2] bg-white px-3 py-2 text-sm outline-none focus:border-[#6c887a]"/><button disabled={creating || !name.trim()} className="mt-3 w-full rounded-full bg-[#164f3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">{creating ? "作成しています…" : "作成して選択"}</button></form>
      {error && <p role="alert" className="mt-3 text-xs leading-5 text-red-700">{error}</p>}
    </div>}
  </div>;
}

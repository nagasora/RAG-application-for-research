"use client";

import { TrashIcon, UserGroupIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  addWorkspaceMember, listWorkspaceMembers, removeWorkspaceMember, updateWorkspaceMemberRole,
  type Workspace, type WorkspaceMember, type WorkspaceMemberRole,
} from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

const ROLES: { value: WorkspaceMemberRole; label: string; description: string }[] = [
  { value:"editor", label:"編集者", description:"論文、ノート、対話、アイデアを編集できます" },
  { value:"viewer", label:"閲覧者", description:"プロジェクトの内容を閲覧できます" },
  { value:"owner", label:"オーナー", description:"メンバーとプロジェクト設定を管理できます" },
];

function memberLabel(member: WorkspaceMember) {
  return member.user.display_name || member.user.email || member.user.subject;
}

export function WorkspaceMemberManager({ workspace, currentUserId, dark = false }: { workspace: Workspace; currentUserId: string; dark?: boolean }) {
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [identity, setIdentity] = useState("");
  const [role, setRole] = useState<WorkspaceMemberRole>("editor");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const workspaceEpochRef = useRef(0);

  useEffect(() => {
    const epoch = ++workspaceEpochRef.current;
    setMembers([]);
    if (!open) return;
    const controller = new AbortController(); setBusy("loading"); setError("");
    listWorkspaceMembers(workspace.id, controller.signal)
      .then(items => { if (!controller.signal.aborted && workspaceEpochRef.current === epoch) setMembers(items); })
      .catch(requestError => { if (!controller.signal.aborted && workspaceEpochRef.current === epoch) setError(apiErrorMessage(requestError, "メンバーを読み込めませんでした")); })
      .finally(() => { if (!controller.signal.aborted && workspaceEpochRef.current === epoch) setBusy(""); });
    return () => controller.abort();
  }, [open, workspace.id]);
  useEffect(() => { if (!open) return; const close = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); }; document.addEventListener("keydown", close); return () => document.removeEventListener("keydown", close); }, [open]);

  if (workspace.role !== "owner" || workspace.is_personal) return null;
  const submit = async (event: FormEvent) => {
    event.preventDefault(); const value = identity.trim(); if (!value || busy) return;
    const epoch = workspaceEpochRef.current;
    setBusy("invite"); setError("");
    try {
      const created = await addWorkspaceMember(workspace.id, value.includes("@") ? { email:value, role } : { subject:value, role });
      if (workspaceEpochRef.current !== epoch) return;
      setMembers(current => [...current, created]); setIdentity(""); setRole("editor");
    } catch (requestError) { if (workspaceEpochRef.current === epoch) setError(apiErrorMessage(requestError, "メンバーを追加できませんでした")); }
    finally { if (workspaceEpochRef.current === epoch) setBusy(""); }
  };
  const changeRole = async (member: WorkspaceMember, nextRole: WorkspaceMemberRole) => {
    if (member.role === nextRole || busy) return; setBusy(`role:${member.user.id}`); setError("");
    const epoch = workspaceEpochRef.current;
    try { const updated = await updateWorkspaceMemberRole(workspace.id, member.user.id, nextRole); if (workspaceEpochRef.current === epoch) setMembers(current => current.map(item => item.user.id === updated.user.id ? updated : item)); }
    catch (requestError) { if (workspaceEpochRef.current === epoch) setError(apiErrorMessage(requestError, "権限を変更できませんでした")); }
    finally { if (workspaceEpochRef.current === epoch) setBusy(""); }
  };
  const remove = async (member: WorkspaceMember) => {
    if (busy || !window.confirm(`${memberLabel(member)} をこのプロジェクトから外しますか？`)) return;
    const epoch = workspaceEpochRef.current;
    setBusy(`remove:${member.user.id}`); setError("");
    try { await removeWorkspaceMember(workspace.id, member.user.id); if (workspaceEpochRef.current === epoch) setMembers(current => current.filter(item => item.user.id !== member.user.id)); }
    catch (requestError) { if (workspaceEpochRef.current === epoch) setError(apiErrorMessage(requestError, "メンバーを削除できませんでした")); }
    finally { if (workspaceEpochRef.current === epoch) setBusy(""); }
  };

  const dialog = open && typeof document !== "undefined" ? createPortal(
    <div className="fixed inset-0 z-[120] flex items-end justify-center bg-[#07110d]/70 p-0 sm:items-center sm:p-6" onMouseDown={event => { if (event.target === event.currentTarget) setOpen(false); }}>
      <section role="dialog" aria-modal="true" aria-labelledby="workspace-members-title" className="flex max-h-[92dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-3xl border border-[#d8ddd9] bg-[#fffefa] text-[#17201d] shadow-2xl sm:rounded-3xl">
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-[#d9ddd8] bg-[#f4f6f2] px-5 py-4 sm:px-6"><div><p className="text-[10px] font-bold uppercase tracking-[.18em] text-[#527264]">Project members</p><h2 id="workspace-members-title" className="serif mt-1 text-2xl font-semibold">メンバーを管理</h2><p className="mt-1 text-xs leading-5 text-[#52605b]">{workspace.name} を共同で利用するメンバーと権限を設定します。</p></div><button type="button" onClick={() => setOpen(false)} aria-label="メンバー管理を閉じる" className="grid h-10 w-10 place-items-center rounded-full border border-[#ccd2cd] bg-white text-[#394640]"><XMarkIcon className="h-5 w-5"/></button></header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6"><form onSubmit={submit} className="rounded-2xl border border-[#cfe0d7] bg-[#f3f8f4] p-4"><h3 className="font-semibold text-[#173d2e]">メンバーを追加</h3><p className="mt-1 text-xs leading-5 text-[#52605b]">登録済みのメールアドレス、またはユーザーIDを入力してください。</p><div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem_auto]"><input value={identity} onChange={event => setIdentity(event.target.value)} placeholder="name@example.com または user-id" maxLength={512} className="rounded-xl border border-[#bfc8c1] bg-white px-3 py-2.5 text-sm outline-none focus:border-[#42705b]"/><select value={role} onChange={event => setRole(event.target.value as WorkspaceMemberRole)} className="rounded-xl border border-[#bfc8c1] bg-white px-3 py-2.5 text-sm">{ROLES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select><button disabled={!identity.trim() || !!busy} className="rounded-xl bg-[#164f3b] px-4 py-2.5 text-sm font-bold text-white disabled:opacity-45">{busy === "invite" ? "追加中…" : "追加"}</button></div></form>
          <div className="mt-6"><div className="flex items-center justify-between"><h3 className="text-sm font-bold">参加メンバー</h3><span className="rounded-full bg-[#e7ede9] px-2.5 py-1 text-[10px] font-semibold text-[#46564f]">{members.length}名</span></div><div className="mt-3 space-y-2">{busy === "loading" ? <p className="p-4 text-sm text-[#68736f]">読み込み中…</p> : members.map(member => { const isSelf = member.user.id === currentUserId; return <article key={member.user.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-[#d9ddd8] bg-white p-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#e5eee8] text-sm font-bold text-[#164f3b]">{memberLabel(member).slice(0, 1).toUpperCase()}</div><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold">{memberLabel(member)}{isSelf ? "（自分）" : ""}</p><p className="truncate text-[11px] text-[#68736f]">{member.user.email || member.user.subject}</p></div><select aria-label={`${memberLabel(member)}の権限`} value={member.role} disabled={!!busy || isSelf} onChange={event => void changeRole(member, event.target.value as WorkspaceMemberRole)} className="rounded-lg border border-[#cbd3cc] bg-white px-2 py-2 text-xs disabled:opacity-60"><>{ROLES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</></select><button type="button" onClick={() => void remove(member)} disabled={!!busy || isSelf} aria-label={`${memberLabel(member)}を削除`} className="grid h-9 w-9 place-items-center rounded-lg text-[#a6443e] hover:bg-red-50 disabled:opacity-40"><TrashIcon className="h-4 w-4"/></button></article>; })}{!busy && !members.length && <p className="rounded-xl border border-dashed border-[#cbd3cc] p-4 text-center text-sm text-[#68736f]">メンバーはまだいません。</p>}</div></div>{error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}</div>
      </section>
    </div>, document.body,
  ) : null;
  return <><button type="button" onClick={() => setOpen(true)} className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-bold ${dark ? "border-[#658f7a]/60 bg-white/10 text-white hover:bg-white/15" : "border-[#bfc8c1] bg-white text-[#164f3b] hover:bg-[#eef4f0]"}`}><UserGroupIcon className="h-4 w-4"/>メンバー</button>{dialog}</>;
}

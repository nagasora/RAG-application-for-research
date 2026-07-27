"use client";

import { useCallback, useEffect, useState } from "react";

import {
  createWorkspace as createWorkspaceRequest, getMe, listWorkspaces,
  renameWorkspace as renameWorkspaceRequest,
  type Me, type Workspace,
} from "@/lib/api/client";
import {
  authMode, setActiveWorkspaceId, setSessionAccessToken,
} from "@/lib/api/auth";
import { toApiError, type ApiError } from "@/lib/api/error";
import { getAuth0AccessToken, loginWithAuth0, logoutFromAuth0 } from "@/lib/auth0";

const WORKSPACE_KEY = "paperpilot.active-workspace";
const workspaceKey = (userId: string) => `${WORKSPACE_KEY}.${userId}`;

function readSavedWorkspaceId(userId: string): string | null {
  if (typeof window === "undefined") return null;
  const key = workspaceKey(userId);
  try {
    const persisted = window.localStorage.getItem(key);
    if (persisted) return persisted;
  } catch { /* use session storage when durable storage is unavailable */ }
  try { return window.sessionStorage.getItem(key); }
  catch { return null; }
}

function saveWorkspaceId(userId: string, workspaceId: string) {
  if (typeof window === "undefined") return;
  const key = workspaceKey(userId);
  try {
    window.localStorage.setItem(key, workspaceId);
    window.sessionStorage.removeItem(key);
    return;
  } catch { /* fall back to this browser session */ }
  try { window.sessionStorage.setItem(key, workspaceId); }
  catch { /* browser storage may be unavailable */ }
}

function clearSavedWorkspaceId(userId: string) {
  if (typeof window === "undefined") return;
  const key = workspaceKey(userId);
  try { window.localStorage.removeItem(key); }
  catch { /* browser storage may be unavailable */ }
  try { window.sessionStorage.removeItem(key); }
  catch { /* browser storage may be unavailable */ }
}

type SessionStatus = "loading" | "ready" | "error";

export type WorkspaceSession = {
  status: SessionStatus;
  mode: "dev" | "oidc" | null;
  me: Me | null;
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  error: ApiError | null;
  creating: boolean;
  renaming: boolean;
  retry: () => void;
  selectWorkspace: (workspaceId: string) => void;
  createWorkspace: (name: string) => Promise<void>;
  renameWorkspace: (workspaceId: string, name: string) => Promise<void>;
  login: () => Promise<void>;
  logout: () => Promise<void>;
};

export function useWorkspaceSession(): WorkspaceSession {
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [mode, setMode] = useState<"dev" | "oidc" | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [revision, setRevision] = useState(0);

  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading"); setError(null);
    let currentMode: "dev" | "oidc";
    try { currentMode = authMode(); setMode(currentMode); }
    catch (configurationError) {
      setError(toApiError(configurationError)); setStatus("error");
      return () => controller.abort();
    }

    (async () => {
      try {
        setActiveWorkspaceId(null);
        if (currentMode === "oidc") setSessionAccessToken(await getAuth0AccessToken());
        const current = await getMe(controller.signal);
        setActiveWorkspaceId(current.personal_workspace.id);
        const available = await listWorkspaces(controller.signal);
        const savedId = readSavedWorkspaceId(current.user.id);
        const selected = available.find(item => item.id === savedId)
          ?? available.find(item => item.id === current.personal_workspace.id)
          ?? current.personal_workspace;
        setActiveWorkspaceId(selected.id);
        saveWorkspaceId(current.user.id, selected.id);
        setMe(current); setWorkspaces(available); setActiveWorkspace(selected); setStatus("ready");
      } catch (requestError) {
        if (controller.signal.aborted) return;
        setError(toApiError(requestError, "認証情報を取得できませんでした")); setStatus("error");
      }
    })();
    return () => controller.abort();
  }, [revision]);

  const selectWorkspace = useCallback((workspaceId: string) => {
    const selected = workspaces.find(item => item.id === workspaceId);
    if (!selected) return;
    setActiveWorkspaceId(selected.id);
    if (me) saveWorkspaceId(me.user.id, selected.id);
    setActiveWorkspace(selected);
  }, [me, workspaces]);

  const createWorkspace = useCallback(async (name: string) => {
    setCreating(true);
    try {
      const created = await createWorkspaceRequest(name);
      setWorkspaces(current => [...current, created]);
      setActiveWorkspaceId(created.id);
      if (me) saveWorkspaceId(me.user.id, created.id);
      setActiveWorkspace(created);
    } finally { setCreating(false); }
  }, [me]);

  const renameWorkspace = useCallback(async (workspaceId: string, name: string) => {
    setRenaming(true);
    try {
      const updated = await renameWorkspaceRequest(workspaceId, name);
      setWorkspaces(current => current.map(workspace => workspace.id === updated.id ? updated : workspace));
      setActiveWorkspace(current => current?.id === updated.id ? updated : current);
      setMe(current => current?.personal_workspace.id === updated.id
        ? { ...current, personal_workspace: updated }
        : current);
    } finally { setRenaming(false); }
  }, []);

  const login = useCallback(async () => {
    await loginWithAuth0();
  }, []);

  const logout = useCallback(async () => {
    setSessionAccessToken(null);
    setActiveWorkspaceId(null);
    if (me) clearSavedWorkspaceId(me.user.id);
    await logoutFromAuth0();
  }, [me]);

  return {
    status, mode, me, workspaces, activeWorkspace, error, creating, renaming,
    retry, selectWorkspace, createWorkspace, renameWorkspace, login, logout,
  };
}

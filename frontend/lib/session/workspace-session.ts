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
        const savedId = typeof window === "undefined" ? null : window.sessionStorage.getItem(WORKSPACE_KEY);
        const selected = available.find(item => item.id === savedId)
          ?? available.find(item => item.id === current.personal_workspace.id)
          ?? current.personal_workspace;
        setActiveWorkspaceId(selected.id);
        if (typeof window !== "undefined") window.sessionStorage.setItem(WORKSPACE_KEY, selected.id);
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
    if (typeof window !== "undefined") window.sessionStorage.setItem(WORKSPACE_KEY, selected.id);
    setActiveWorkspace(selected);
  }, [workspaces]);

  const createWorkspace = useCallback(async (name: string) => {
    setCreating(true);
    try {
      const created = await createWorkspaceRequest(name);
      setWorkspaces(current => [...current, created]);
      setActiveWorkspaceId(created.id);
      if (typeof window !== "undefined") window.sessionStorage.setItem(WORKSPACE_KEY, created.id);
      setActiveWorkspace(created);
    } finally { setCreating(false); }
  }, []);

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
    if (typeof window !== "undefined") window.sessionStorage.removeItem(WORKSPACE_KEY);
    await logoutFromAuth0();
  }, []);

  return {
    status, mode, me, workspaces, activeWorkspace, error, creating, renaming,
    retry, selectWorkspace, createWorkspace, renameWorkspace, login, logout,
  };
}

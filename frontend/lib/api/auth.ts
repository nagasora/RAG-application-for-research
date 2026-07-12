import { ApiError } from "./error";

export type AuthMode = "dev" | "oidc";

const TOKEN_KEY = "paperpilot.oidc.access-token";
const AUTH_MODE = process.env.NEXT_PUBLIC_AUTH_MODE?.trim().toLowerCase();
const DEV_USER = process.env.NEXT_PUBLIC_DEV_USER?.trim();

let memoryToken: string | null = null;
let activeWorkspaceId: string | null = null;

function sessionToken(): string | null {
  if (memoryToken) return memoryToken;
  if (typeof window === "undefined") return null;
  memoryToken = window.sessionStorage.getItem(TOKEN_KEY)?.trim() || null;
  return memoryToken;
}

export function authMode(): AuthMode {
  if (AUTH_MODE === "dev" || AUTH_MODE === "oidc") return AUTH_MODE;
  throw new ApiError("NEXT_PUBLIC_AUTH_MODE は dev または oidc を指定してください", {
    status: 503,
    code: "auth_configuration_error",
  });
}

export function setSessionAccessToken(token: string | null): void {
  memoryToken = token?.trim() || null;
  if (typeof window === "undefined") return;
  if (memoryToken) window.sessionStorage.setItem(TOKEN_KEY, memoryToken);
  else window.sessionStorage.removeItem(TOKEN_KEY);
}

export function setActiveWorkspaceId(workspaceId: string | null): void {
  activeWorkspaceId = workspaceId?.trim() || null;
}

export function getActiveWorkspaceId(): string | null {
  return activeWorkspaceId;
}

export function authenticatedHeaders(initial?: HeadersInit, includeWorkspace = true): Headers {
  const headers = new Headers(initial);
  const mode = authMode();
  if (mode === "dev") {
    if (!DEV_USER) {
      throw new ApiError("dev認証には NEXT_PUBLIC_DEV_USER が必要です", {
        status: 503,
        code: "auth_configuration_error",
      });
    }
    headers.set("X-Dev-User", DEV_USER);
  } else {
    const token = sessionToken();
    if (!token) {
      throw new ApiError("OIDCアクセストークンを設定してください", {
        status: 401,
        code: "missing_access_token",
      });
    }
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (includeWorkspace && activeWorkspaceId) headers.set("X-Workspace-ID", activeWorkspaceId);
  return headers;
}

export const authenticatedFetch: typeof fetch = (input, init = {}) => fetch(input, {
  ...init,
  headers: authenticatedHeaders(init.headers),
  credentials: init.credentials ?? "include",
});

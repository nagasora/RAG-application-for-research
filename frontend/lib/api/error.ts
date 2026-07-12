import type { components } from "./schema";

type ValidationError = components["schemas"]["ValidationError"];

export type ApiErrorOptions = {
  status?: number;
  code?: string;
  details?: unknown;
  requestId?: string | null;
  cause?: unknown;
};

export class ApiError extends Error {
  readonly status?: number;
  readonly code: string;
  readonly details?: unknown;
  readonly requestId?: string | null;

  constructor(message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code ?? "api_error";
    this.details = options.details;
    this.requestId = options.requestId;
  }
}

function isValidationError(value: unknown): value is ValidationError {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ValidationError>;
  return Array.isArray(candidate.loc) && typeof candidate.msg === "string";
}

function messageFromPayload(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (!payload || typeof payload !== "object") return fallback;

  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.filter(isValidationError).map(item => item.msg);
    if (messages.length) return messages.join(" / ");
  }

  const message = (payload as { message?: unknown }).message;
  return typeof message === "string" && message.trim() ? message : fallback;
}

export function apiErrorFromResponse(response: Response, payload: unknown, fallback: string): ApiError {
  return new ApiError(messageFromPayload(payload, fallback), {
    status: response.status,
    code: `http_${response.status}`,
    details: payload,
    requestId: response.headers.get("x-request-id"),
  });
}

export function toApiError(error: unknown, fallback = "APIリクエストに失敗しました"): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError("リクエストをキャンセルしました", { code: "aborted", cause: error });
  }
  if (error instanceof Error) {
    return new ApiError(error.message || fallback, { code: "network_error", cause: error });
  }
  return new ApiError(fallback, { code: "unknown_error", cause: error });
}

export function apiErrorMessage(error: unknown, fallback = "APIリクエストに失敗しました"): string {
  const normalized = toApiError(error, fallback);
  if (normalized.status === 401) return `認証が必要です。${normalized.message}`;
  if (normalized.status === 403) return `このワークスペースで操作する権限がありません。${normalized.message}`;
  if (normalized.status === 503) return `認証またはデータサービスを利用できません。${normalized.message}`;
  return normalized.message;
}

export async function errorFromFetchResponse(response: Response, fallback: string): Promise<ApiError> {
  const contentType = response.headers.get("content-type") ?? "";
  let payload: unknown;
  try {
    payload = contentType.includes("json") ? await response.json() : await response.text();
  } catch {
    payload = undefined;
  }
  return apiErrorFromResponse(response, payload, fallback);
}

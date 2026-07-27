import type { components } from "./schema";
import { API_ERROR_COPY, UI_COPY } from "../copy";

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

type StructuredErrorDetail = { code?: string; message?: string };

function structuredDetailFromPayload(payload: unknown): StructuredErrorDetail {
  if (!payload || typeof payload !== "object") return {};
  const detail = (payload as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return {};
  const candidate = detail as { code?: unknown; message?: unknown };
  const code = typeof candidate.code === "string" && /^[a-z][a-z0-9_.-]{0,127}$/.test(candidate.code)
    ? candidate.code
    : undefined;
  const message = typeof candidate.message === "string" && candidate.message.trim()
    ? candidate.message.trim()
    : undefined;
  return { code, message };
}

function messageFromPayload(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (!payload || typeof payload !== "object") return fallback;

  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  const structuredMessage = structuredDetailFromPayload(payload).message;
  if (structuredMessage) return structuredMessage;
  if (Array.isArray(detail)) {
    const messages = detail.filter(isValidationError).map(item => item.msg);
    if (messages.length) return messages.join(" / ");
  }

  const message = (payload as { message?: unknown }).message;
  return typeof message === "string" && message.trim() ? message : fallback;
}

export function apiErrorFromResponse(response: Response, payload: unknown, fallback: string): ApiError {
  const structuredDetail = structuredDetailFromPayload(payload);
  return new ApiError(messageFromPayload(payload, fallback), {
    status: response.status,
    code: structuredDetail.code ?? `http_${response.status}`,
    details: payload,
    requestId: response.headers.get("x-request-id"),
  });
}

export function toApiError(error: unknown, fallback = "APIリクエストに失敗しました"): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError(UI_COPY.cancelled, { code: "aborted", cause: error });
  }
  if (error instanceof Error) {
    return new ApiError(fallback, { code: "network_error", cause: error, details:{ internalMessage:error.message } });
  }
  return new ApiError(fallback, { code: "unknown_error", cause: error });
}

export function apiErrorMessage(error: unknown, fallback = "APIリクエストに失敗しました"): string {
  const normalized = toApiError(error, fallback);
  const codeCopy = Object.prototype.hasOwnProperty.call(API_ERROR_COPY, normalized.code)
    ? API_ERROR_COPY[normalized.code]
    : undefined;
  const statusCode = normalized.status ? `http_${normalized.status}` : "";
  const statusCopy = statusCode && Object.prototype.hasOwnProperty.call(API_ERROR_COPY, statusCode)
    ? API_ERROR_COPY[statusCode]
    : undefined;
  return codeCopy
    ?? statusCopy
    ?? (fallback.trim() && fallback !== "APIリクエストに失敗しました" ? fallback : UI_COPY.unknownError);
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

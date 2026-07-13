import { API_BASE_URL, type AnswerClaim, type Citation, type SearchRequest, type SearchResponse } from "./client";
import { authenticatedHeaders } from "./auth";
import { ApiError, errorFromFetchResponse, toApiError } from "./error";

export type SearchStreamEvent =
  | { type: "token"; value: string }
  | { type: "citations"; value: Citation[] }
  | { type: "stage"; value: SearchStage }
  | { type: "meta"; value: SearchStreamMeta }
  | { type: "done" }
  | { type: "error"; message: string };

export type SearchStreamMeta = Required<Pick<SearchResponse,
  "generation_mode" | "model" | "retrieval_queries" | "grounded" | "llm_attempted"
  | "llm_succeeded" | "grounding_status" | "fallback_reason" | "claims" | "memory_delta" | "model_calls"
>> & {
  model: string | null;
  fallback_reason: string | null;
};

export const SEARCH_STAGES = [
  "accepted", "embedding", "retrieving", "planning", "generating", "auditing", "saving",
] as const;
export type SearchStage = (typeof SEARCH_STAGES)[number];

export function isSearchStage(value: unknown): value is SearchStage {
  return typeof value === "string" && (SEARCH_STAGES as readonly string[]).includes(value);
}

function isCitation(value: unknown): value is Citation {
  if (!value || typeof value !== "object") return false;
  const citation = value as Partial<Citation>;
  return typeof citation.index === "number"
    && typeof citation.paper_id === "string"
    && typeof citation.paper_title === "string"
    && typeof citation.chunk_id === "string"
    && typeof citation.page === "number"
    && typeof citation.section === "string"
    && typeof citation.excerpt === "string"
    && typeof citation.score === "number";
}

function isAnswerClaim(value: unknown): value is AnswerClaim {
  if (!value || typeof value !== "object") return false;
  const claim = value as Partial<AnswerClaim>;
  return typeof claim.claim_id === "string"
    && typeof claim.text === "string"
    && ["paper", "general", "hypothesis"].includes(claim.kind ?? "")
    && Array.isArray(claim.citation_ids) && claim.citation_ids.every(item => typeof item === "number");
}

function isMemoryDelta(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function decodeEvent(dataLines: string[]): SearchStreamEvent | null {
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new ApiError("SSEイベントを解析できませんでした", {
      code: "invalid_sse_json",
      details: raw,
      cause: error,
    });
  }
  if (!parsed || typeof parsed !== "object" || typeof (parsed as { type?: unknown }).type !== "string") {
    throw new ApiError("SSEイベントの形式が不正です", { code: "invalid_sse_event", details: parsed });
  }
  const event = parsed as { type: string; value?: unknown; stage?: unknown; message?: unknown };
  if (event.type === "token" && typeof event.value === "string") return { type: "token", value: event.value };
  if (event.type === "citations" && Array.isArray(event.value) && event.value.every(isCitation)) {
    return { type: "citations", value: event.value };
  }
  if (event.type === "stage") {
    const stage = event.value ?? event.stage;
    if (isSearchStage(stage)) return { type: "stage", value: stage };
  }
  if (event.type === "meta" && event.value && typeof event.value === "object") {
    const meta = event.value as Partial<SearchStreamMeta>;
    if ((meta.generation_mode === "agentic_rag" || meta.generation_mode === "local_fallback")
      && (typeof meta.model === "string" || meta.model === null)
      && Array.isArray(meta.retrieval_queries) && meta.retrieval_queries.every(item => typeof item === "string")
      && typeof meta.grounded === "boolean"
      && typeof meta.llm_attempted === "boolean"
      && typeof meta.llm_succeeded === "boolean"
      && ["verified", "rejected", "not_checked", "no_evidence"].includes(meta.grounding_status ?? "")
      && (typeof meta.fallback_reason === "string" || meta.fallback_reason === null)
      && Array.isArray(meta.claims) && meta.claims.every(isAnswerClaim)
      && isMemoryDelta(meta.memory_delta)
      && typeof meta.model_calls === "number" && Number.isInteger(meta.model_calls) && meta.model_calls >= 0) {
      return { type: "meta", value: meta as SearchStreamMeta };
    }
  }
  if (event.type === "done") return { type: "done" };
  if (event.type === "error" && typeof event.message === "string") return { type: "error", message: event.message };
  throw new ApiError(`未対応のSSEイベントです: ${event.type}`, { code: "invalid_sse_event", details: parsed });
}

export async function* parseEventStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<SearchStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let dataLines: string[] = [];

  const consumeLine = (rawLine: string): SearchStreamEvent | null => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      const event = decodeEvent(dataLines);
      dataLines = [];
      return event;
    }
    if (line.startsWith(":")) return null;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") dataLines.push(value);
    return null;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const event = consumeLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        if (event) yield event;
        newline = buffer.indexOf("\n");
      }
      if (done) break;
    }
    if (buffer) {
      const event = consumeLine(buffer);
      if (event) yield event;
    }
    const finalEvent = decodeEvent(dataLines);
    if (finalEvent) yield finalEvent;
  } finally {
    reader.releaseLock();
  }
}

export async function* streamSearch(request: SearchRequest, signal?: AbortSignal): AsyncGenerator<SearchStreamEvent> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/search/stream`, {
      method: "POST",
      headers: authenticatedHeaders({ Accept: "text/event-stream", "Content-Type": "application/json" }),
      body: JSON.stringify(request),
      credentials: "include",
      signal,
    });
  } catch (error) {
    throw toApiError(error, "回答を生成できませんでした");
  }
  if (!response.ok) throw await errorFromFetchResponse(response, "回答を生成できませんでした");
  if (!response.body) throw new ApiError("ストリームを開始できませんでした", { code: "missing_response_body" });

  let completed = false;
  for await (const event of parseEventStream(response.body)) {
    if (event.type === "error") throw new ApiError(event.message, { code: "stream_error" });
    if (event.type === "done") completed = true;
    yield event;
  }
  if (!completed) {
    throw new ApiError("回答ストリームが完了前に切断されました。質問を再送できます。", {
      code: "incomplete_stream",
    });
  }
}

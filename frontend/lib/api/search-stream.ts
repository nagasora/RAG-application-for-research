import { API_BASE_URL, type Citation, type SearchRequest } from "./client";
import { authenticatedHeaders } from "./auth";
import { ApiError, errorFromFetchResponse, toApiError } from "./error";

export type SearchStreamEvent =
  | { type: "token"; value: string }
  | { type: "citations"; value: Citation[] }
  | { type: "done" }
  | { type: "error"; message: string };

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
  const event = parsed as { type: string; value?: unknown; message?: unknown };
  if (event.type === "token" && typeof event.value === "string") return { type: "token", value: event.value };
  if (event.type === "citations" && Array.isArray(event.value) && event.value.every(isCitation)) {
    return { type: "citations", value: event.value };
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

  for await (const event of parseEventStream(response.body)) {
    if (event.type === "error") throw new ApiError(event.message, { code: "stream_error" });
    yield event;
  }
}

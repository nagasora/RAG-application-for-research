import createClient from "openapi-fetch";

import { authenticatedFetch, authenticatedHeaders } from "./auth";
import { ApiError, apiErrorFromResponse, errorFromFetchResponse, toApiError } from "./error";
import type { components, paths } from "./schema";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Paper = components["schemas"]["PaperSummary"];
export type Citation = components["schemas"]["Citation"];
export type SearchRequest = components["schemas"]["SearchRequest"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type AnswerClaim = components["schemas"]["AnswerClaim"];
export type UploadResult = components["schemas"]["UploadResult"];
export type CompareRow = components["schemas"]["ComparisonRow"];
export type Gap = components["schemas"]["ResearchGap"];
export type Chunk = components["schemas"]["Chunk"];
export type PaperDetail = components["schemas"]["PaperDetail"];
export type PaperPage = components["schemas"]["PaperPage"];
export type Me = components["schemas"]["MeResponse"];
export type Workspace = components["schemas"]["Workspace"];
export type WorkspaceMember = components["schemas"]["WorkspaceMember"];
export type WorkspaceMemberRole = WorkspaceMember["role"];
export type Tag = components["schemas"]["Tag"];
export type Note = components["schemas"]["Note"];
export type SearchHistory = components["schemas"]["SearchHistory"];
export type SavedComparison = components["schemas"]["SavedComparison"];
export type ExportFormat = "bibtex" | "ris" | "csv";
export type IngestionJob = components["schemas"]["IngestionJob"];
export type DocumentElement = components["schemas"]["DocumentElement"];
export type ResearchConversation = components["schemas"]["ResearchConversation"];
export type ResearchConversationDetail = components["schemas"]["ResearchConversationDetail"];
export type ResearchMessage = components["schemas"]["ResearchMessage"];
export type ResearchMessagePage = components["schemas"]["ResearchMessagePage"];
export type ResearchMemoryEvent = components["schemas"]["ResearchMemoryEvent"];
export type ResearchMemoryPage = components["schemas"]["ResearchMemoryPage"];
export type ResearchMemoryKind = ResearchMemoryEvent["kind"];
export type LLMStatus = components["schemas"]["LLMStatus"];
export type PaperMarkdownSummary = components["schemas"]["PaperMarkdownSummary"];
export type GraphSnapshot = components["schemas"]["GraphSnapshot"];
export type SourceVersion = components["schemas"]["SourceVersion"];
export type SourceVersionCreate = components["schemas"]["SourceVersionCreate"];
export type SourceImportCreate = components["schemas"]["SourceImportCreate"];
export type SourceImportResult = components["schemas"]["SourceImportResult"];
export type SourceSpan = components["schemas"]["SourceSpan"];
export type KnowledgeNode = components["schemas"]["KnowledgeNode"];
export type KnowledgeNodeCreate = components["schemas"]["KnowledgeNodeCreate"];
export type KnowledgeNodeStatusUpdate = components["schemas"]["KnowledgeNodeStatusUpdate"];
export type KnowledgeNodeStatusResult = components["schemas"]["KnowledgeNodeStatusResult"];
export type KnowledgeEdge = components["schemas"]["KnowledgeEdge"];
export type KnowledgeEdgeCreate = components["schemas"]["KnowledgeEdgeCreate"];
export type KnowledgeEdgeStatusUpdate = components["schemas"]["KnowledgeEdgeStatusUpdate"];
export type GraphRetrieveRequest = components["schemas"]["GraphRetrieveRequest"];
export type GraphRetrievalHit = components["schemas"]["GraphRetrievalHit"];
export type ForwardPropagationCreate = components["schemas"]["ForwardPropagationCreate"];
export type ForwardPropagationResult = components["schemas"]["ForwardPropagationResult"];

export type ResearchMessagePageOptions = { limit?: number; beforeOrdinal?: number | null };
export type ResearchMemoryPageOptions = ResearchMessagePageOptions & { kind?: ResearchMemoryKind | null };

const api = createClient<paths>({ baseUrl: API_BASE_URL, credentials: "include", fetch: authenticatedFetch });

type ApiResult<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(result: ApiResult<T>, fallback: string): Promise<T> {
  if (result.error !== undefined || !result.response.ok) {
    throw apiErrorFromResponse(result.response, result.error, fallback);
  }
  if (result.data === undefined) {
    throw toApiError(new Error("APIレスポンスが空です"), fallback);
  }
  return result.data;
}

async function fetchAuthenticatedBlob(url: string, fallback: string, signal?: AbortSignal): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(url, { headers:authenticatedHeaders(), credentials:"include", signal });
  } catch (error) {
    throw toApiError(error, fallback);
  }
  if (!response.ok) throw await errorFromFetchResponse(response, fallback);
  return response.blob();
}

export async function getMe(signal?: AbortSignal): Promise<Me> {
  const result = await api.GET("/api/me", { signal });
  return unwrap(result, "ユーザー情報を取得できませんでした");
}

export async function getLLMStatus(signal?: AbortSignal): Promise<LLMStatus> {
  const result = await api.GET("/api/llm/status", { signal });
  return unwrap(result, "LLMの接続状態を取得できませんでした");
}

export async function listWorkspaces(signal?: AbortSignal): Promise<Workspace[]> {
  const result = await api.GET("/api/workspaces", { signal });
  return unwrap(result, "ワークスペース一覧を取得できませんでした");
}

export async function createWorkspace(name: string, signal?: AbortSignal): Promise<Workspace> {
  const result = await api.POST("/api/workspaces", { body: { name }, signal });
  return unwrap(result, "ワークスペースを作成できませんでした");
}

export async function renameWorkspace(workspaceId: string, name: string, signal?: AbortSignal): Promise<Workspace> {
  const result = await api.PATCH("/api/workspaces/{workspace_id}", {
    params: { path: { workspace_id: workspaceId } },
    body: { name },
    signal,
  });
  return unwrap(result, "ワークスペース名を変更できませんでした");
}

export async function listWorkspaceMembers(workspaceId: string, signal?: AbortSignal): Promise<WorkspaceMember[]> {
  const result = await api.GET("/api/workspaces/{workspace_id}/members", {
    params: { path: { workspace_id: workspaceId } }, signal,
  });
  return unwrap(result, "プロジェクトメンバーを取得できませんでした");
}

export async function addWorkspaceMember(workspaceId: string, body: { email?: string; subject?: string; role: WorkspaceMemberRole }, signal?: AbortSignal): Promise<WorkspaceMember> {
  const result = await api.POST("/api/workspaces/{workspace_id}/members", {
    params: { path: { workspace_id: workspaceId } }, body, signal,
  });
  return unwrap(result, "メンバーを追加できませんでした");
}

export async function updateWorkspaceMemberRole(workspaceId: string, memberUserId: string, role: WorkspaceMemberRole, signal?: AbortSignal): Promise<WorkspaceMember> {
  const result = await api.PATCH("/api/workspaces/{workspace_id}/members/{member_user_id}", {
    params: { path: { workspace_id: workspaceId, member_user_id: memberUserId } }, body: { role }, signal,
  });
  return unwrap(result, "メンバー権限を変更できませんでした");
}

export async function removeWorkspaceMember(workspaceId: string, memberUserId: string, signal?: AbortSignal): Promise<void> {
  const result = await api.DELETE("/api/workspaces/{workspace_id}/members/{member_user_id}", {
    params: { path: { workspace_id: workspaceId, member_user_id: memberUserId } }, signal,
  });
  if (result.error !== undefined || !result.response.ok) throw apiErrorFromResponse(result.response, result.error, "メンバーを削除できませんでした");
}

export async function listPapers(signal?: AbortSignal): Promise<Paper[]> {
  const result = await api.GET("/api/papers", { signal });
  return unwrap(result, "論文一覧を取得できませんでした");
}

export async function getGraphSnapshot(canvasId = "default", signal?: AbortSignal): Promise<GraphSnapshot> {
  return unwrap(await api.GET("/api/graph", { params: { query: { canvas_id:canvasId } }, signal }), "知識グラフを取得できませんでした");
}

export async function listGraphSources(signal?: AbortSignal): Promise<SourceVersion[]> {
  return unwrap(await api.GET("/api/graph/sources", { signal }), "Source一覧を取得できませんでした");
}

export async function createGraphSource(body: SourceVersionCreate, signal?: AbortSignal): Promise<SourceVersion> {
  return unwrap(await api.POST("/api/graph/sources", { body, signal }), "Sourceを作成できませんでした");
}

export async function importGraphSource(body: SourceImportCreate, signal?: AbortSignal): Promise<SourceImportResult> {
  return unwrap(await api.POST("/api/graph/sources/import", { body, signal }), "Sourceを取り込めませんでした");
}

export async function listGraphSourceSpans(sourceVersionId: string, signal?: AbortSignal): Promise<SourceSpan[]> {
  return unwrap(await api.GET("/api/graph/sources/{source_version_id}/spans", {
    params: { path: { source_version_id:sourceVersionId } }, signal,
  }), "Source Spanを取得できませんでした");
}

export async function createGraphNode(body: KnowledgeNodeCreate, signal?: AbortSignal): Promise<KnowledgeNode> {
  return unwrap(await api.POST("/api/graph/nodes", { body, signal }), "知識ノードを作成できませんでした");
}

export async function forwardPropagateGraph(body: ForwardPropagationCreate, signal?: AbortSignal): Promise<ForwardPropagationResult> {
  return unwrap(await api.POST("/api/graph/forward-propagations", { body, signal }), "根拠付き仮説を作成できませんでした");
}

export async function updateGraphNodeStatus(nodeId: string, body: KnowledgeNodeStatusUpdate, signal?: AbortSignal): Promise<KnowledgeNodeStatusResult> {
  return unwrap(await api.PATCH("/api/graph/nodes/{node_id}/status", {
    params: { path: { node_id:nodeId } }, body, signal,
  }), "知識ノードの状態を更新できませんでした");
}

export async function createGraphEdge(body: KnowledgeEdgeCreate, signal?: AbortSignal): Promise<KnowledgeEdge> {
  return unwrap(await api.POST("/api/graph/edges", { body, signal }), "知識エッジを作成できませんでした");
}

export async function updateGraphEdgeStatus(edgeId: string, body: KnowledgeEdgeStatusUpdate, signal?: AbortSignal): Promise<KnowledgeEdge> {
  return unwrap(await api.PATCH("/api/graph/edges/{edge_id}/status", {
    params: { path: { edge_id:edgeId } }, body, signal,
  }), "知識エッジの状態を更新できませんでした");
}

export async function retrieveGraph(body: GraphRetrieveRequest, signal?: AbortSignal): Promise<GraphRetrievalHit[]> {
  return unwrap(await api.POST("/api/graph/retrieve", { body, signal }), "グラフを展開できませんでした");
}

export async function getPaperDetail(paperId: string, signal?: AbortSignal): Promise<PaperDetail> {
  const result = await api.GET("/api/papers/{paper_id}", {
    params: { path: { paper_id: paperId } },
    signal,
  });
  return unwrap(result, "論文詳細を取得できませんでした");
}

export async function generatePaperSummary(paperId: string, signal?: AbortSignal): Promise<PaperMarkdownSummary> {
  return unwrap(await api.POST("/api/papers/{paper_id}/summary", {
    params: { path: { paper_id: paperId } }, signal,
  }), "論文要約を生成できませんでした");
}

export async function getPaperPage(paperId: string, page: number, signal?: AbortSignal): Promise<PaperPage> {
  const result = await api.GET("/api/papers/{paper_id}/pages/{page}", {
    params: { path: { paper_id: paperId, page } },
    signal,
  });
  return unwrap(result, "ページの根拠を取得できませんでした");
}

export async function getPaperChunk(paperId: string, chunkId: string, signal?: AbortSignal): Promise<Chunk> {
  const result = await api.GET("/api/papers/{paper_id}/chunks/{chunk_id}", {
    params: { path: { paper_id: paperId, chunk_id: chunkId } },
    signal,
  });
  return unwrap(result, "引用箇所を取得できませんでした");
}

export async function getPaperFile(paperId: string, signal?: AbortSignal): Promise<Blob> {
  return fetchAuthenticatedBlob(
    `${API_BASE_URL}/api/papers/${encodeURIComponent(paperId)}/file`,
    "原本ファイルを取得できませんでした", signal,
  );
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<IngestionJob> {
  return unwrap(await api.GET("/api/jobs/{job_id}", { params: { path: { job_id: jobId } }, signal }), "取り込み状況を取得できませんでした");
}

export async function pollIngestionJob(jobId: string, signal: AbortSignal, onUpdate?: (job: IngestionJob) => void, timeoutMs = 180_000): Promise<IngestionJob> {
  const startedAt = Date.now(); let delayMs = 1_000;
  while (true) {
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    const job = await getJob(jobId, signal); onUpdate?.(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    if (Date.now() - startedAt >= timeoutMs) {
      throw new ApiError("論文解析がタイムアウトしました。後で一覧を再読み込みしてください。", { code: "job_timeout" });
    }
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, delayMs);
      signal.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
    });
    delayMs = Math.min(2_000, Math.round(delayMs * 1.35));
  }
}

export async function listAssets(paperId: string, signal?: AbortSignal): Promise<DocumentElement[]> {
  return unwrap(await api.GET("/api/papers/{paper_id}/assets", { params: { path: { paper_id: paperId } }, signal }), "文書要素を取得できませんでした");
}

export async function getAssetFile(paperId: string, elementId: string, signal?: AbortSignal): Promise<Blob> {
  return fetchAuthenticatedBlob(
    `${API_BASE_URL}/api/papers/${encodeURIComponent(paperId)}/assets/${encodeURIComponent(elementId)}/file`,
    "図版を取得できませんでした", signal,
  );
}

function isUploadResult(value: unknown): value is UploadResult {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<UploadResult>;
  return typeof item.filename === "string" && typeof item.success === "boolean" && typeof item.status === "string";
}

export async function uploadPapers(files: File[], signal?: AbortSignal): Promise<UploadResult[]> {
  const form = new FormData();
  files.forEach(file => form.append("files", file));

  const result = await api.POST("/api/papers/upload", {
    body: { files: files.map(file => file.name) },
    bodySerializer: () => form,
    signal,
  });
  const payload = await unwrap(result, "論文をアップロードできませんでした");
  const unknownPayload: unknown = payload;

  // The compatibility branch can be removed after every deployed backend uses UploadResult.
  if (Array.isArray(unknownPayload) && unknownPayload.every(isUploadResult)) return unknownPayload;
  if (Array.isArray(unknownPayload)) {
    return unknownPayload.map((paper: unknown, index: number) => ({
      filename: files[index]?.name ?? `file-${index + 1}`,
      success: true,
      status: "ready",
      paper: paper as Paper,
      error: null,
      duplicate: false,
    }));
  }
  throw toApiError(new Error("アップロード結果の形式が不正です"));
}

export async function addExternalPaper(identifier: string, signal?: AbortSignal): Promise<Paper> {
  const result = await api.POST("/api/papers/external", {
    body: { identifier, title: null, authors: [], year: null, abstract: "" },
    signal,
  });
  return unwrap(result, "外部論文を取得できませんでした");
}

export async function deletePaper(paperId: string, signal?: AbortSignal): Promise<void> {
  const result = await api.DELETE("/api/papers/{paper_id}", {
    params: { path: { paper_id: paperId } },
    signal,
  });
  if (result.error !== undefined || !result.response.ok) {
    throw apiErrorFromResponse(result.response, result.error, "論文を削除できませんでした");
  }
}

function isCompareRow(value: unknown): value is CompareRow {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<CompareRow>;
  return [row.paper_id, row.title, row.purpose, row.method, row.results, row.limitations]
    .every(item => typeof item === "string");
}

function isGap(value: unknown): value is Gap {
  if (!value || typeof value !== "object") return false;
  const gap = value as Partial<Gap>;
  return [gap.paper_id, gap.paper_title, gap.page, gap.gap, gap.opportunity]
    .every(item => typeof item === "string");
}

export async function comparePapers(paperIds: string[], signal?: AbortSignal): Promise<CompareRow[]> {
  const result = await api.POST("/api/analysis/compare", {
    body: { paper_ids: paperIds },
    signal,
  });
  const payload = await unwrap(result, "論文比較に失敗しました");
  if (!Array.isArray(payload) || !payload.every(isCompareRow)) {
    throw toApiError(new Error("論文比較のレスポンス形式が不正です"));
  }
  return payload;
}

export async function findResearchGaps(paperIds: string[], signal?: AbortSignal): Promise<Gap[]> {
  const result = await api.POST("/api/analysis/gaps", {
    body: { paper_ids: paperIds },
    signal,
  });
  const payload = await unwrap(result, "リサーチギャップ分析に失敗しました");
  if (!Array.isArray(payload) || !payload.every(isGap)) {
    throw toApiError(new Error("リサーチギャップのレスポンス形式が不正です"));
  }
  return payload;
}

async function expectNoContent(result: { error?: unknown; response: Response }, fallback: string): Promise<void> {
  if (result.error !== undefined || !result.response.ok) {
    throw apiErrorFromResponse(result.response, result.error, fallback);
  }
}

export async function listTags(signal?: AbortSignal): Promise<Tag[]> {
  return unwrap(await api.GET("/api/tags", { signal }), "タグ一覧を取得できませんでした");
}

export async function createTag(name: string, color: string, signal?: AbortSignal): Promise<Tag> {
  return unwrap(await api.POST("/api/tags", { body: { name, color }, signal }), "タグを作成できませんでした");
}

export async function updateTag(tagId: string, name: string, color: string, signal?: AbortSignal): Promise<Tag> {
  return unwrap(await api.PUT("/api/tags/{tag_id}", { params: { path: { tag_id: tagId } }, body: { name, color }, signal }), "タグを更新できませんでした");
}

export async function deleteTag(tagId: string, signal?: AbortSignal): Promise<void> {
  return expectNoContent(await api.DELETE("/api/tags/{tag_id}", { params: { path: { tag_id: tagId } }, signal }), "タグを削除できませんでした");
}

export async function getPaperTags(paperId: string, signal?: AbortSignal): Promise<Tag[]> {
  return unwrap(await api.GET("/api/papers/{paper_id}/tags", { params: { path: { paper_id: paperId } }, signal }), "論文タグを取得できませんでした");
}

export async function setPaperTags(paperId: string, tagIds: string[], signal?: AbortSignal): Promise<Tag[]> {
  return unwrap(await api.PUT("/api/papers/{paper_id}/tags", { params: { path: { paper_id: paperId } }, body: { tag_ids: tagIds }, signal }), "論文タグを更新できませんでした");
}

export async function listNotes(paperId?: string, signal?: AbortSignal): Promise<Note[]> {
  return unwrap(await api.GET("/api/notes", { params: { query: { paper_id: paperId } }, signal }), "ノートを取得できませんでした");
}

export async function createNote(paperId: string | null, title: string, content: string, signal?: AbortSignal): Promise<Note> {
  return unwrap(await api.POST("/api/notes", { body: { paper_id: paperId, title, content }, signal }), "ノートを作成できませんでした");
}

export async function updateNote(noteId: string, title: string, content: string, signal?: AbortSignal): Promise<Note> {
  return unwrap(await api.PATCH("/api/notes/{note_id}", { params: { path: { note_id: noteId } }, body: { title, content }, signal }), "ノートを更新できませんでした");
}

export async function deleteNote(noteId: string, signal?: AbortSignal): Promise<void> {
  return expectNoContent(await api.DELETE("/api/notes/{note_id}", { params: { path: { note_id: noteId } }, signal }), "ノートを削除できませんでした");
}

export async function listSearchHistory(signal?: AbortSignal): Promise<SearchHistory[]> {
  return unwrap(await api.GET("/api/search/history", { signal }), "検索履歴を取得できませんでした");
}

export async function deleteSearchHistory(historyId: string, signal?: AbortSignal): Promise<void> {
  return expectNoContent(await api.DELETE("/api/search/history/{history_id}", { params: { path: { history_id: historyId } }, signal }), "検索履歴を削除できませんでした");
}

export async function listResearchConversations(signal?: AbortSignal): Promise<ResearchConversation[]> {
  return unwrap(await api.GET("/api/research/conversations", { signal }), "研究対話の一覧を取得できませんでした");
}

export async function createResearchConversation(title: string, signal?: AbortSignal): Promise<ResearchConversation> {
  return unwrap(await api.POST("/api/research/conversations", { body: { title }, signal }), "研究対話を作成できませんでした");
}

export async function getResearchConversation(conversationId: string, signal?: AbortSignal): Promise<ResearchConversationDetail> {
  return unwrap(await api.GET("/api/research/conversations/{conversation_id}", {
    params: { path: { conversation_id: conversationId } }, signal,
  }), "研究対話を取得できませんでした");
}

export async function getResearchMessagesPage(
  conversationId: string,
  options: ResearchMessagePageOptions = {},
  signal?: AbortSignal,
): Promise<ResearchMessagePage> {
  return unwrap(await api.GET("/api/research/conversations/{conversation_id}/messages", {
    params: {
      path: { conversation_id: conversationId },
      query: { limit:options.limit, before_ordinal:options.beforeOrdinal },
    },
    signal,
  }), "研究対話のメッセージを取得できませんでした");
}

export async function getResearchMemoryPage(
  conversationId: string,
  options: ResearchMemoryPageOptions = {},
  signal?: AbortSignal,
): Promise<ResearchMemoryPage> {
  return unwrap(await api.GET("/api/research/conversations/{conversation_id}/memory", {
    params: {
      path: { conversation_id:conversationId },
      query: { kind:options.kind, limit:options.limit, before_ordinal:options.beforeOrdinal },
    },
    signal,
  }), "研究メモリを取得できませんでした");
}

export async function listSavedComparisons(signal?: AbortSignal): Promise<SavedComparison[]> {
  return unwrap(await api.GET("/api/comparisons", { signal }), "保存済み比較を取得できませんでした");
}

export async function saveComparison(name: string, paperIds: string[], signal?: AbortSignal): Promise<SavedComparison> {
  return unwrap(await api.POST("/api/comparisons", { body: { name, paper_ids: paperIds }, signal }), "比較を保存できませんでした");
}

export async function deleteSavedComparison(comparisonId: string, signal?: AbortSignal): Promise<void> {
  return expectNoContent(await api.DELETE("/api/comparisons/{comparison_id}", { params: { path: { comparison_id: comparisonId } }, signal }), "保存済み比較を削除できませんでした");
}

export async function exportPapers(format: ExportFormat, paperIds: string[] = [], signal?: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  const url = new URL(`${API_BASE_URL}/api/exports/papers`);
  url.searchParams.set("format", format);
  paperIds.forEach(paperId => url.searchParams.append("paper_ids", paperId));
  let response: Response;
  try { response = await fetch(url, { headers: authenticatedHeaders(), credentials: "include", signal }); }
  catch (error) { throw toApiError(error, "論文をエクスポートできませんでした"); }
  if (!response.ok) throw await errorFromFetchResponse(response, "論文をエクスポートできませんでした");
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? `paperpilot-export.${format === "bibtex" ? "bib" : format}`;
  return { blob: await response.blob(), filename };
}

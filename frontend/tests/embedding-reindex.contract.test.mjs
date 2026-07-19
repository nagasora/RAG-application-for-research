import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const client = await readFile(new URL("../lib/api/client.ts", import.meta.url), "utf8");
const panel = await readFile(new URL("../components/embedding-reindex-panel.tsx", import.meta.url), "utf8");
const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

test("reindex API stays generated-type based and scoped to the active workspace", () => {
  assert.match(client, /type EmbeddingReindexRequest = components\["schemas"\]\["EmbeddingReindexRequest"\]/);
  assert.match(client, /api\.POST\("\/api\/embeddings\/reindex", \{ body, signal \}\)/);
  assert.match(panel, /reindexEmbeddings\(\{ paper_ids:readyPaperIds \}\)/);
});

test("multilingual reindex UI limits the action to ready papers and respects viewer access", () => {
  assert.match(panel, /paper\.status === "ready"/);
  assert.match(panel, /日本語の質問で英語・日本語論文を検索する/);
  assert.match(panel, /viewer 権限では検索と状態の確認のみできます/);
  assert.match(panel, /role="status"/);
  assert.match(panel, /role="alert"/);
  assert.match(page, /<EmbeddingReindexPanel papers=\{papers\} canWrite=\{session\.activeWorkspace\.role !== "viewer"\}/);
});

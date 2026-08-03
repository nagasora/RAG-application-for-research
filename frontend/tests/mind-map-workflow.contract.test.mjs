import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = path => readFileSync(new URL(path, import.meta.url), "utf8");
const workspace = read("../components/mind-map-workspace.tsx");
const hub = read("../components/graph-hub.tsx");
const client = read("../lib/api/client.ts");
const ask = read("../components/ask-workspace.tsx");
const schema = read("../lib/api/schema.d.ts");
const auth = read("../lib/api/auth.ts");

test("mind map API client keeps candidate generation separate from confirmation", () => {
  for (const route of [
    "/api/mind-maps",
    "/api/mind-maps/generate",
    "/api/mind-map-nodes/{node_id}/expand",
    "/api/mind-map-nodes/{node_id}/children",
    "/api/mind-map-nodes/{node_id}/research-actions/generate",
    "/api/mind-map-nodes/{node_id}/research-actions",
    "/api/mind-map-nodes/{node_id}/notes",
    "/api/mind-map-nodes/{node_id}/graph-node",
  ]) assert.match(client, new RegExp(route.replace(/[{}]/g, "\\$&")));
  assert.match(workspace, /まだ保存されていません/);
  assert.match(workspace, /選択を確定/);
  assert.match(workspace, /setCandidate\(null\)/);
  assert.match(workspace, /reloadMap\(selectedNode\.mind_map_id\)/);
});

test("graph screen exposes distinct knowledge graph and mind map tabs", () => {
  assert.match(hub, /role="tablist"/);
  assert.match(hub, /知識グラフ/);
  assert.match(hub, /マインドマップ/);
  assert.match(hub, /<GraphWorkspace/);
  assert.match(hub, /<MindMapWorkspace/);
});

test("workspace authorization, viewer mode, and governed Ask seed stay explicit", () => {
  assert.match(auth, /X-Workspace-ID/);
  assert.match(workspace, /viewer・読取専用/);
  assert.match(workspace, /disabled=\{!canWrite/);
  assert.match(ask, /mind_map_seed:\{ mind_map_id:runMindMapSeed\.mindMapId, node_id:runMindMapSeed\.nodeId \}/);
  assert.match(schema, /ResearchRunMindMapSeed/);
  assert.match(schema, /mind_map_seed\?: components\["schemas"\]\["ResearchRunMindMapSeed"\]/);
});

test("mind map UI exposes evidence return, keyboard canvas, and explicit graph promotion", () => {
  assert.match(workspace, /openNodeEvidence/);
  assert.match(workspace, /Knowledge Graphへ昇格/);
  assert.match(workspace, /自動同期はしません/);
  assert.match(workspace, /部分木を削除/);
  assert.match(workspace, /Action候補/);
});

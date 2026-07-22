import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");

test("mind map preserves layout boundaries and structured task/note workflows", async () => {
  const [canvas, workspace] = await Promise.all([
    read("components/graph-canvas.tsx"), read("components/graph-workspace.tsx"),
  ]);

  assert.match(canvas, /"mindmap"/);
  assert.match(canvas, /mindMapLayout/);
  assert.match(canvas, /offsetX/);
  assert.match(canvas, /minX\s*=\s*Math\.min/);
  assert.match(canvas, /viewMode\s*===\s*"mindmap"\s*\?\s*Math\.max/);
  assert.match(canvas, /selectedNodeIds\.at\(-1\)/);
  assert.match(workspace, /setCanvasView\("mindmap"\)/);
  assert.match(workspace, /extractTasksFromSelectedNode/);
  assert.match(workspace, /createResearchAction\(\{/);
  assert.match(workspace, /origin_node_id:\s*selected\.id/);
  assert.match(workspace, /source_span_id:\s*evidenceSpanIds\[0\]\s*\?\?\s*null/);
  assert.match(workspace, /generation_class:\s*"unverified"/);
  assert.match(workspace, /source:\s*"mind_map_task_extraction_v1"/);
  assert.match(workspace, /ordinal:\s*index\s*\+\s*1/);
  assert.match(workspace, /node_snapshot:\s*\{/);
  assert.match(workspace, /evidence_span_ids:\s*evidenceSpanIds/);
  assert.match(workspace, /disabled=\{!canWrite\s*\|\|\s*actionCreating\}/);
  assert.match(workspace, /disabled=\{!canWrite\s*\|\|\s*noteCreating\}/);
  assert.match(workspace, /listNotes\(undefined,\s*\{\s*originKind:\s*"mind_map"\s*\}\)/);
  assert.match(workspace, /createNote\(null,[\s\S]*?originKind:\s*"mind_map"/);
  assert.doesNotMatch(workspace, /title\.startsWith\("マインドマップ:"\)/);
  assert.match(workspace, /mindMapNotes/);
  assert.match(workspace, /タスク候補を抽出/);
  assert.match(workspace, /ノートに追加/);
});

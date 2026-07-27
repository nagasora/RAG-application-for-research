import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");

test("Idea decomposition and graph/experiment action entry points remain available", async () => {
  const [pipeline, graph, client] = await Promise.all([
    read("components/research-pipeline.tsx"), read("components/graph-workspace.tsx"), read("lib/api/client.ts"),
  ]);
  assert.match(pipeline, /decomposeIdeaActions/);
  assert.match(pipeline, /Research Actions/);
  assert.match(pipeline, /human_decision/);
  assert.match(pipeline, /experiment_plan_id:experiment\.id/);
  assert.match(graph, /origin_node_id:selected\.id/);
  assert.match(graph, /研究計画の子要素/);
  assert.match(client, /\/api\/ideas\/\{idea_id\}\/actions\/decompose/);
  assert.match(client, /\/api\/research-actions/);
});

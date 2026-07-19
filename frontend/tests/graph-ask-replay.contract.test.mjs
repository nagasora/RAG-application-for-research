import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const graph = await readFile(new URL("../components/graph-workspace.tsx", import.meta.url), "utf8");
const ask = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");
const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

test("a selected graph node offers the three editor-only derived Ask intents", () => {
  assert.match(graph, /type GraphAskIntent = "explore" \| "challenge" \| "design"/);
  for (const action of ["広げる", "対立仮説", "検証案"]) assert.match(graph, new RegExp(action));
  assert.match(graph, /if \(!canWrite \|\| !selected \|\| !snapshot\.nodes\.some/);
  assert.match(graph, /viewer権限では派生質問を開始できません/);
});

test("page replay switches to Ask safely and carries graph context into the Run plan", () => {
  assert.match(page, /if \(session\.activeWorkspace\?\.role === "viewer" \|\| !seed\.nodeId\.trim\(\) \|\| !seed\.content\.trim\(\)\) return/);
  assert.match(page, /slice\(0, 1_200\)/);
  assert.match(page, /const graphSeed = \{ \.\.\.seed, content:excerpt \}/);
  assert.match(page, /setView\("ask"\)/);
  assert.match(page, /onReplayConsumed=\{\(\) => setSearchReplay\(null\)\}/);
  assert.match(ask, /graph_seed:\{ node_id:runGraphSeed\.nodeId, content:runGraphSeed\.content, intent:runGraphSeed\.intent \}/);
  assert.match(ask, /setInteractionMode\(replay\.graphSeed\.intent\)/);
  assert.match(ask, /setGraphSeed\(null\)/);
});

test("manual question replacement and conversation changes clear graph provenance after replay", () => {
  assert.match(ask, /const replaceQuery = \(nextQuery: string\) => \{\s*setQuery\(nextQuery\);\s*setGraphSeed\(null\);/);
  assert.match(ask, /onChange=\{event => replaceQuery\(event\.target\.value\)\}/);
  assert.match(ask, /onClick=\{\(\) => replaceQuery\(text\)\}/);
  const selectConversation = ask.slice(ask.indexOf("const selectConversation"), ask.indexOf("const refreshList"));
  assert.match(selectConversation, /setGraphSeed\(null\)/);
  assert.match(ask, /if \(replay\) \{ setQuery\(replay\.query\); setGraphSeed\(replay\.graphSeed \?\? null\);/);
});

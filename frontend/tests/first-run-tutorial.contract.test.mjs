import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const tutorial = await readFile(new URL("../components/first-run-tutorial.tsx", import.meta.url), "utf8");
const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

test("first-run tutorial derives progress from persisted research records rather than navigation", () => {
  for (const api of ["listSearchHistory", "listIdeas", "listHypothesisCards", "listSavedComparisons", "getGraphSnapshot", "listGraphSources"]) {
    assert.match(tutorial, new RegExp(api));
  }
  assert.match(tutorial, /画面を開いただけでは完了になりません/);
  assert.match(tutorial, /未実施は未実施のまま表示します/);
  assert.match(tutorial, /paper\.status === "ready"/);
});

test("first-run tutorial covers the complete PaperPilot research loop and can persistently dismiss or reopen", () => {
  for (const label of ["再埋め込み", "日本語で論文に質問する", "Ideaとして残す", "反証可能な仮説へ昇格する", "比較してギャップを検討する", "グラフ候補を人間がレビューする"]) {
    assert.match(tutorial, new RegExp(label));
  }
  assert.match(tutorial, /localStorage\.setItem/);
  assert.match(tutorial, /localStorage\.removeItem/);
  assert.match(tutorial, /使い方・進捗を表示/);
  assert.match(page, /FirstRunTutorial workspaceId=\{session\.activeWorkspace\.id\}/);
});

test("graph Sources do not complete a review and stale workspace activity is discarded", () => {
  assert.match(tutorial, /graphPending === 0 && \(activity\.graphReviewed \?\? 0\) > 0/);
  assert.match(tutorial, /判断済み/);
  assert.match(tutorial, /Source \$\{activity\.graphSources/);
  assert.match(tutorial, /activityAbortRef\.current\?\.abort\(\)/);
  assert.match(tutorial, /activityRevisionRef\.current/);
  assert.match(tutorial, /setActivity\(EMPTY_ACTIVITY\)/);
});

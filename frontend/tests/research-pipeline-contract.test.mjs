import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const capture = await readFile(new URL("../components/idea-capture.tsx", import.meta.url), "utf8");
const pipeline = await readFile(new URL("../components/research-pipeline.tsx", import.meta.url), "utf8");
const reviews = await readFile(new URL("../components/collaborative-reviews.tsx", import.meta.url), "utf8");
const workspace = await readFile(new URL("../components/research-workspace.tsx", import.meta.url), "utf8");
const ask = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");

test("IdeaCapture writes to the dedicated inbox instead of the knowledge graph", () => {
  assert.match(capture, /await createIdea\(/);
  assert.doesNotMatch(capture, /createGraphNode/);
  assert.match(capture, /paperpilot:idea-created/);
  assert.match(capture, /run\|claim\|paper\|span/);
});

test("research pipeline keeps promotion gates and experiment audit actions visible", () => {
  assert.match(pipeline, /\["evidence", "根拠を接続"\]/);
  assert.match(pipeline, /\["falsifier", "反証条件を確認"\]/);
  assert.match(pipeline, /\["test", "試験方法を設計"\]/);
  assert.match(pipeline, /await promoteIdea\(/);
  assert.match(pipeline, /await addExperimentResult\(/);
  assert.match(pipeline, /await getExperimentPlanSnapshot\(/);
  assert.match(pipeline, /experiment-\$\{experiment\.id\}-v1\.json/);
});

test("Ask can produce graph candidates even when durable memory is empty", () => {
  assert.match(ask, /await listGraphIdeaCandidates\(/);
  assert.match(ask, /await exportConversationGraphDrafts\(/);
  assert.match(ask, /自分でレビュー候補を追加/);
  assert.match(ask, /kind:"manual"/);
  assert.match(ask, /論文根拠・検証済み知識・引用の支持を意味しません/);
});

test("Research workspace exposes the complete collaborative review workflow", () => {
  assert.match(workspace, /<CollaborativeReviews workspaceId=\{workspaceId\} canWrite=\{canWrite\}/);
  for (const apiCall of [
    "listReviewThreads", "getReviewThread", "createReviewThread", "assignReviewThread",
    "addReviewComment", "addReviewDecision", "getReviewReport",
  ]) {
    assert.match(reviews, new RegExp(`${apiCall}\\(`), `${apiCall} must remain wired to the review UI`);
  }
  assert.match(reviews, /viewer はレビュー一覧・詳細・判断履歴・レポートを閲覧できます/);
  assert.match(reviews, /Research Run の主張/);
  assert.match(reviews, /EvidenceLink/);
  assert.match(reviews, /selectedClaimSnapshot/);
  assert.match(reviews, /保存時点の主張/);
  assert.match(reviews, /immutable snapshot/);
  assert.match(reviews, /claim_artifact_id/);
  assert.match(reviews, /owner \/ editor の判断履歴/);
});

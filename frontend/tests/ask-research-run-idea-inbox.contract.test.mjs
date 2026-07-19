import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const client = await readFile(new URL("../lib/api/client.ts", import.meta.url), "utf8");
const ask = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");

test("the API client exposes generated ResearchRun creation and cancellation", () => {
  assert.match(client, /export type ResearchRun = components\["schemas"\]\["ResearchRun"\]/);
  assert.match(client, /export type ResearchRunCreate = components\["schemas"\]\["ResearchRunCreate"\]/);
  assert.match(client, /api\.POST\("\/api\/research\/runs", \{ body, signal \}\)/);
  assert.match(client, /api\.POST\("\/api\/research\/runs\/\{run_id\}\/cancel"/);
});

test("editor Ask creates an auditable run before a new conversation or stream", () => {
  assert.match(ask, /const researchRun = await createResearchRun\(\{/);
  assert.match(ask, /source_paper_ids:selected\.length \? selected : readyPapers\.map\(paper => paper\.id\)/);
  assert.match(ask, /purpose:prompt/);
  assert.match(ask, /plan:\{ origin:"ask_workspace", interaction_mode:interactionMode, \.\.\.\(runGraphSeed \?/);
  assert.match(ask, /research_run_id:researchRun\.id/);
  assert.ok(ask.indexOf("const researchRun = await createResearchRun") < ask.indexOf("const created = await createResearchConversation"));
  assert.ok(ask.indexOf("const researchRun = await createResearchRun") < ask.indexOf("for await (const streamEvent of streamSearch"));
  assert.match(ask, /void cancelResearchRun\(researchRun\.id\)\.catch\(\(\) => \{ \/\* best-effort cleanup preserves the conversation error \*\/ \}\)/);
  assert.match(ask, /let researchRunId: string \| null = null/);
  assert.match(ask, /if \(!streamCompleted && researchRunId\)/);
  assert.match(ask, /void cancelResearchRun\(researchRunId\)\.catch/);
});

test("viewer preview remains evidence-only and does not create a ResearchRun", () => {
  const start = ask.indexOf("if (!canWrite) {");
  const viewer = ask.slice(start, ask.indexOf("streamAbortRef.current?.abort();", start));
  assert.match(viewer, /previewSearch\(\{ query:prompt, paper_ids:selected, limit:10, interaction_mode:"evidence" \}\)/);
  assert.doesNotMatch(viewer, /createResearchRun/);
});

test("only eligible persisted claims can be sent to the Idea Inbox with immutable anchors", () => {
  assert.match(ask, /claim\.classification === "hypothesis" \|\| claim\.classification === "unverified" \|\| claim\.classification === "inference"/);
  assert.match(ask, /if \(!canWrite \|\| !message\.research_run_id\) return null/);
  assert.match(ask, /kind:ideaKindForClaim\(claim\), content:claim\.text, research_run_id:researchRunId, claim_id:claim\.claim_id/);
  assert.match(ask, /checklist:\{ evidence:false, falsifier:false, test:false, captured_from:"ask_workspace" \}/);
  assert.match(ask, /paperpilot:idea-created/);
  assert.match(ask, /state === "saving" \|\| state === "saved"/);
  assert.match(ask, /IdeaInboxActions message=\{message\}/);
  assert.doesNotMatch(ask.slice(ask.indexOf("(liveAnswer || busy)"), ask.indexOf("<div ref={messageEndRef}")), /IdeaInboxActions/);
});

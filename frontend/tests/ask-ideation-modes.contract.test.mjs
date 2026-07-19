import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");

test("editors can choose every supported ideation mode with Japanese guidance", () => {
  for (const mode of ["synthesis", "explore", "challenge", "design", "update"]) {
    assert.match(source, new RegExp(`id:\\"${mode}\\"`));
  }
  for (const label of ["統合する", "発想を広げる", "反証を探す", "検証を設計する", "判断を更新する"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /draft \/ unverified/);
  assert.match(source, /人間が採否を判断してください/);
});

test("the editor stream request uses the selected interaction mode", () => {
  assert.match(source, /const \[interactionMode, setInteractionMode\] = useState<EditorInteractionMode>\("synthesis"\)/);
  assert.match(source, /streamSearch\(\{ query:prompt, paper_ids:selected, limit:10, conversation_id:conversationId, research_run_id:researchRun\.id, interaction_mode:interactionMode \}/);
  assert.doesNotMatch(source, /streamSearch\([^\n]+interaction_mode:\"synthesis\"/);
});

test("viewers remain on the evidence-only preview route", () => {
  assert.match(source, /if \(!canWrite\) \{/);
  assert.match(source, /previewSearch\(\{ query:prompt, paper_ids:selected, limit:10, interaction_mode:\"evidence" \}\)/);
  assert.match(source, /\{canWrite && <fieldset/);
});

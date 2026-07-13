import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");

test("interruption sync compares the authoritative message count", () => {
  assert.match(source, /const baselineMessageCount = detail\?\.message_count \?\? 0/);
  assert.match(source, /refreshed\.message_count > baselineMessageCount/);
  assert.doesNotMatch(source, /refreshed\.messages\?\.length[^\n]*> baseline/);
});

test("an incomplete stream cannot clear the retry query after loop exit", () => {
  const loopEnd = source.indexOf('setQuery(""); setPhase("syncing")');
  assert.ok(loopEnd >= 0);
  assert.match(source, /streamEvent\.type === "done"\) streamCompleted = true/);
  assert.equal(source.match(/streamCompleted\s*=\s*true/g)?.length, 1);
});

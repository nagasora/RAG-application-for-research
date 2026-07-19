import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");

test("assistant responses share classification badges between history and live streaming", () => {
  assert.match(source, /function AnswerClassificationBadges/);
  assert.match(source, /interactionMode=\{message\.interaction_mode\} draft=\{message\.draft\} claims=\{message\.claims\}/);
  assert.match(source, /interactionMode=\{lastMeta\?\.interaction_mode \?\? interactionMode\} draft=\{lastMeta\?\.draft\} claims=\{lastMeta\?\.claims\}/);
  assert.match(source, /aria-label=\{`回答の主張区分/);
});

test("the response UI communicates mode, draft status, and every claim classification without calling drafts verified", () => {
  for (const label of ["統合", "発想", "反証", "実験設計", "判断更新", "根拠あり", "推論", "一般知識", "仮説", "未検証"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /下書き・要確認/);
  assert.match(source, /提案は未検証です。原文・引用を確認し、人間が採否を判断してください。/);
  assert.doesNotMatch(source, /下書き・要確認[\s\S]{0,100}検証済み/);
});

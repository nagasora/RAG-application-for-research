import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

test("analysis view explains the workflow and preserves direct next actions", () => {
  assert.match(page, /この画面でできること/);
  assert.match(page, /論文を2件以上選ぶ/);
  assert.match(page, /比較・ギャップを分析/);
  assert.match(page, /原文を確認して保存/);
  assert.match(page, /ライブラリで論文を追加・確認/);
  assert.match(page, /保存した比較を開く/);
});

test("analysis only offers ready papers and describes uncertainty as unresolved evidence", () => {
  assert.match(page, /paper\.status === "ready" && selected\.includes\(paper\.id\)/);
  assert.match(page, /disabled=\{selectedPapers\.length < 2 \|\| busy\}/);
  assert.match(page, /const paperIds = selectedPapers\.map\(paper => paper\.id\)/);
  assert.match(page, /空欄や「未判定」は、本文から根拠を確定できなかったことを示します/);
  assert.match(page, /研究ギャップは結論ではなく候補です/);
  assert.match(page, /比較表の論文名は本文の先頭を開きます/);
});

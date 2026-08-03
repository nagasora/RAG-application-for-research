import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
const comparisonTabs = await readFile(new URL("../components/analysis-comparison-tabs.tsx", import.meta.url), "utf8");

test("analysis view explains the workflow and preserves direct next actions", () => {
  assert.match(page, /この画面でできること/);
  assert.match(page, /論文を2件以上選ぶ/);
  assert.match(page, /実験結果・手法を比較/);
  assert.match(page, /原文を確認して保存/);
  assert.match(page, /ライブラリで論文を追加・確認/);
  assert.match(page, /保存した比較を開く/);
});

test("analysis only offers ready papers and describes uncertainty as unresolved evidence", () => {
  assert.match(page, /paper\.status === "ready" && !isAbstractOnlyPaper\(paper\) && selected\.includes\(paper\.id\)/);
  assert.match(page, /disabled=\{!canWrite \|\| selectedPapers\.length < 2 \|\| selectedPapers\.length > 5 \|\| busy\}/);
  assert.match(page, /const paperIds = selectedPapers\.map\(paper => paper\.id\)/);
  assert.match(page, /空欄や「未判定」は、本文から根拠を確定できなかったことを示します/);
  assert.match(page, /研究ギャップは結論ではなく候補です/);
  assert.match(page, /比較表の論文名は本文の先頭を開きます/);
});

test("experiment comparison preserves successful profiles and records exclusions on save", () => {
  assert.match(page, /successfulProfilePaperIds/);
  assert.match(page, /successfulExperimentProfileIds/);
  assert.match(page, /profile\.status === "ready" \|\| profile\.status === "cached"/);
  assert.match(page, /successfulProfilePaperIds\.length >= 2/);
  assert.match(page, /experimentProfileIds:successfulExperimentProfileIds/);
  assert.match(page, /analysisErrors:comparisonErrors/);
  assert.match(page, /成功した比較プロファイルが2件以上必要です/);
  assert.match(page, /一部の論文を比較できませんでした/);
  assert.match(page, /研究ギャップ候補を取得できませんでした/);
});

test("experiment comparison tabs expose keyboard and tabpanel relationships", () => {
  assert.match(comparisonTabs, /aria-controls=\{`analysis-panel-\$\{id\}`\}/);
  assert.match(comparisonTabs, /tabIndex=\{tab === id \? 0 : -1\}/);
  assert.match(comparisonTabs, /event\.key === "ArrowRight"/);
  assert.match(comparisonTabs, /event\.key === "ArrowLeft"/);
  assert.match(comparisonTabs, /event\.key === "Home"/);
  assert.match(comparisonTabs, /event\.key === "End"/);
  assert.match(comparisonTabs, /role="tabpanel"/);
  assert.match(comparisonTabs, /aria-labelledby="analysis-tab-/);
});

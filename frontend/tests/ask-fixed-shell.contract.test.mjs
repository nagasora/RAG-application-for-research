import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const ask = await readFile(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");
const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
const copy = await readFile(new URL("../lib/copy.ts", import.meta.url), "utf8");

test("Ask uses a fixed viewport shell with the requested responsive pane boundaries", () => {
  assert.match(page, /view === "ask" \? "h-\[100dvh\] overflow-hidden"/);
  assert.match(ask, /xl:grid-cols-\[280px_minmax\(0,1fr\)\]/);
  assert.match(ask, /2xl:grid-cols-\[minmax\(0,1fr\)_300px\]/);
  assert.match(ask, /className="hidden 2xl:block"/);
  assert.match(ask, /role="log" aria-label="研究対話"/);
  assert.match(ask, /event\.key === "Escape"/);
  assert.match(ask, /DRAWER_FOCUSABLE/);
});

test("responsive drawers deactivate their modal state and body lock at static-pane breakpoints", () => {
  assert.match(ask, /HISTORY_STATIC_MEDIA_QUERY = "\(min-width: 1280px\)"/);
  assert.match(ask, /EVIDENCE_STATIC_MEDIA_QUERY = "\(min-width: 1536px\)"/);
  assert.match(ask, /historyStatic\.addEventListener\("change", deactivateHistoryDrawer\)/);
  assert.match(ask, /evidenceStatic\.addEventListener\("change", deactivateEvidenceDrawer\)/);
  assert.match(ask, /historyStatic\.removeEventListener\("change", deactivateHistoryDrawer\)/);
  assert.match(ask, /evidenceStatic\.removeEventListener\("change", deactivateEvidenceDrawer\)/);
  assert.match(ask, /if \(event\.matches\) setHistoryOpen\(false\)/);
  assert.match(ask, /if \(event\.matches\) setEvidenceOpen\(false\)/);
  assert.match(ask, /document\.body\.style\.overflow = previousOverflow/);
  assert.match(ask, /!window\.matchMedia\(HISTORY_STATIC_MEDIA_QUERY\)\.matches/);
  assert.match(ask, /!window\.matchMedia\(EVIDENCE_STATIC_MEDIA_QUERY\)\.matches/);
});

test("Ask enforces one to five locked sources and shares the exact request contract", () => {
  assert.match(ask, /const MAX_SOURCE_PAPERS = 5/);
  assert.match(ask, /const SEARCH_RESULT_LIMIT = 8/);
  assert.match(ask, /if \(paperIds\.length < 1\)/);
  assert.match(ask, /if \(paperIds\.length > MAX_SOURCE_PAPERS\)/);
  assert.match(ask, /source_paper_ids:paperIds/);
  assert.match(ask, /paper_ids:paperIds, limit:SEARCH_RESULT_LIMIT/g);
  assert.match(ask, /getResearchRun\(latestRunId/);
  assert.match(ask, /setSelected\(\[\]\); setError/);
});

test("conversation source restoration is revision guarded and independent of paper readiness rerenders", () => {
  assert.match(ask, /const sourceChoiceRevisionRef = useRef\(0\)/);
  assert.match(ask, /const sourceRestoreRevisionRef = useRef\(0\)/);
  assert.match(ask, /const restoreRevision = \+\+sourceRestoreRevisionRef\.current/);
  assert.match(ask, /sourceChoiceRevisionRef\.current === choiceRevision/);
  assert.match(ask, /sourceRestoreRevisionRef\.current === restoreRevision/);
  assert.match(ask, /const readyIdsAtSelection = readyIdsRef\.current/);
  assert.match(ask, /\}, \[activeId\]\);/);
  assert.doesNotMatch(ask, /\}, \[activeId, readyIds/);
  assert.match(ask, /appliedReplayRevisionRef\.current === replay\.revision/);
  assert.match(ask, /sourceChoiceRevisionRef\.current \+= 1;\s+setSourceNotice\(""\);\s+setSelected\(normalized\)/);
});

test("timeout recovery and public Japanese copy stay stable", () => {
  assert.match(copy, /AIの回答が時間内に終わりませんでした。論文の抜粋が見つかった場合は、代わりに表示しています/);
  assert.match(copy, /同じ条件でもう一度/);
  assert.match(copy, /処理を完了できませんでした。もう一度お試しください/);
  assert.match(copy, /cockpit: "研究対話"/);
  assert.match(copy, /evidence: "最新回答の根拠"/);
  assert.match(copy, /sources: "検索対象"/);
  assert.match(ask, /ask\(undefined, timeoutRetry\)/);
  assert.match(ask, /この回答で参照した論文の根拠/);
  assert.match(ask, /回答の主張と引用元の対応を確認済み/);
  assert.match(ask, /代替回答について:/);
});

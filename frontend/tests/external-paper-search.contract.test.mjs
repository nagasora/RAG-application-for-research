import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const search = readFileSync(new URL("../components/external-paper-search.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const client = readFileSync(new URL("../lib/api/client.ts", import.meta.url), "utf8");
const ask = readFileSync(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");

test("external search keeps the cursor-based API boundary and 20-result UX", () => {
  assert.match(search, /ExternalPaperSearchRequest = DiscoverySearchRequest/);
  assert.match(search, /ExternalPaperImportRequest = DiscoveryImportRequest/);
  assert.match(search, /const PAGE_SIZE = 20/);
  assert.match(search, /provider_paper_ids/);
  assert.match(search, /search_context:/);
  assert.match(search, /next_cursor/);
  assert.match(search, /cursorStack/);
  assert.match(search, /external_provider_rate_limited/);
  assert.match(client, /export type DiscoverySearchRequest/);
  assert.match(client, /api\.POST\("\/api\/discovery\/search"/);
  assert.match(client, /api\.POST\("\/api\/discovery\/imports"/);
});

test("external search exposes accessible selection, partial, rate-limit, and abstract-only states", () => {
  assert.match(search, /このページを全選択/);
  assert.match(search, /選択した\$\{selectedPapers\.length\}件を要旨として追加/);
  assert.match(search, /利用上限に達しました/);
  assert.match(search, /要旨のみ/);
  assert.match(search, /要旨を開く/);
  assert.match(search, /閲覧者は検索・追加できません/);
  assert.match(search, /クリア/);
  assert.match(search, /Semantic Scholarで開く/);
  assert.match(search, /const selectablePapers/);
  assert.match(search, /const registeredPaperIds = new Map/);
  assert.match(search, /existing_paper_id: registeredPaperIds\.get/);
  assert.match(search, /disabled=\{!canWrite \|\| adding \|\| existing\}/);
  assert.match(search, /role="alert"/);
});

test("library keeps legacy arXiv or DOI entry while hiding PDF actions for abstract-only papers", () => {
  assert.match(page, /arXiv ID または DOI/);
  assert.match(page, /<ExternalPaperSearch/);
  assert.match(page, /function isAbstractOnlyPaper/);
  assert.match(page, /const ready = paper\.status === "ready";/);
  assert.match(page, /const canOpenEvidence = ready && !abstractOnly/);
  assert.match(page, /abstractOnly \? <details/);
  assert.match(page, /\{canOpenEvidence && <button/);
  assert.match(page, /disabled=\{abstractOnly\}/);
  assert.match(page, /onSearch=\{searchExternalPapers\}/);
});

test("abstract citations are labeled and never open a full-text page", () => {
  assert.match(ask, /function isAbstractCitation/);
  assert.match(ask, /citation\.evidence_scope === "abstract"/);
  assert.match(ask, /外部要旨/);
  assert.match(ask, /外部要旨からの抜粋/);
  assert.match(ask, /!isAbstractCitation\(citation\)/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const search = readFileSync(new URL("../components/external-paper-search.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const client = readFileSync(new URL("../lib/api/client.ts", import.meta.url), "utf8");
const nextConfig = readFileSync(new URL("../next.config.ts", import.meta.url), "utf8");
const localEnvExample = readFileSync(new URL("../.env.local.example", import.meta.url), "utf8");
const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
const copy = readFileSync(new URL("../lib/copy.ts", import.meta.url), "utf8");
const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");
const compose = readFileSync(new URL("../../docker-compose.local.yml", import.meta.url), "utf8");
const ask = readFileSync(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");
const safeExternalUrl = readFileSync(new URL("../lib/api/safe-external-url.ts", import.meta.url), "utf8");

test("external search keeps the cursor-based API boundary and 20-result UX", () => {
  assert.match(search, /ExternalPaperSearchRequest = DiscoverySearchRequest/);
  assert.match(search, /ExternalPaperImportRequest = DiscoveryImportRequest/);
  assert.match(search, /const PAGE_SIZE = 20/);
  assert.match(search, /provider_paper_ids/);
  assert.match(search, /search_context:/);
  assert.match(search, /next_cursor/);
  assert.match(search, /cursorStack/);
  assert.match(search, /pageHistory/);
  assert.match(search, /const showPreviousPage/);
  assert.match(search, /setPage\(previous\)/);
  assert.match(search, /external_provider_rate_limited/);
  assert.match(search, /discovery_session_not_found/);
  assert.match(search, /UI_COPY\.discovery\.retrySearch/);
  assert.match(search, /page\.expires_at/);
  assert.match(client, /export type DiscoverySearchRequest/);
  assert.match(client, /api\.POST\("\/api\/discovery\/search"/);
  assert.match(client, /api\.POST\("\/api\/discovery\/imports"/);
});

test("external search exposes accessible selection, partial, rate-limit, and abstract-only states", () => {
  assert.match(search, /このページを全選択/);
  assert.match(search, /選択した\$\{selected\.length\}件を要旨として追加/);
  assert.match(search, /利用上限に達しました/);
  assert.match(search, /要旨のみ/);
  assert.match(search, /要旨を開く/);
  assert.match(search, /閲覧者は検索・追加できません/);
  assert.match(search, /提供元で開く/);
  assert.match(search, /safeExternalHttpsUrl\(item\.source_url\)/);
  assert.match(search, /href=\{sourceUrl\}/);
  assert.match(search, /rel="noreferrer noopener"/);
  assert.match(safeExternalUrl, /\^https:\\\/\\\//);
  assert.match(search, /const selectable/);
  assert.match(search, /const registered = new Map/);
  assert.match(search, /existing_paper_id:registered\.get/);
  assert.match(search, /disabled=\{!canWrite \|\| adding \|\| existing\}/);
  assert.match(search, /role="alert"/);
  assert.match(search, /UI_COPY\.discovery\.connectionHelp/);
  assert.match(search, /UI_COPY\.discovery\.retrySearch/);
  assert.match(search, /<button type="button" onClick=\{\(\) => void runSearch\(\)\}/);
});

test("local API calls use the same-origin proxy unless an explicit public API URL is configured", () => {
  assert.match(nextConfig, /PAPERPILOT_API_PROXY_TARGET/);
  assert.match(nextConfig, /"http:\/\/localhost:8000"/);
  assert.match(nextConfig, /destination: `\$\{apiProxyTarget\}\/api\/:path\*`/);
  assert.match(nextConfig, /if \(process\.env\.NEXT_PUBLIC_API_URL\) return \[\]/);
  assert.match(client, /process\.env\.NEXT_PUBLIC_API_URL \|\| ""/);
  assert.match(client, /export const apiUrl/);
  assert.match(client, /fetch\(apiUrl\("\/api\/search\/preview"\)/);
  assert.match(copy, /connectionHelp/);
  assert.doesNotMatch(localEnvExample, /^NEXT_PUBLIC_API_URL=/m);
  assert.match(localEnvExample, /NEXT_PUBLIC_API_URL=https:\/\/api\.example\.com/);
  assert.match(readme, /Next\.js proxies same-origin `\/api` requests/);
  assert.doesNotMatch(readme, /NEXT_PUBLIC_API_URL=http:\/\/localhost:8000/);
  assert.match(dockerfile, /ARG PAPERPILOT_API_PROXY_TARGET/);
  assert.match(compose, /PAPERPILOT_API_PROXY_TARGET: http:\/\/api:8000/);
});

test("library keeps legacy arXiv or DOI entry while hiding PDF actions for abstract-only papers", () => {
  assert.match(page, /arXiv ID または DOI/);
  assert.match(page, /<ExternalPaperSearch/);
  assert.match(page, /function isAbstractOnlyPaper/);
  assert.match(page, /const ready = paper\.status === "ready";/);
  assert.match(page, /const canAnalyze = ready && !abstractOnly/);
  assert.match(page, /const canOpenEvidence = canAnalyze/);
  assert.match(page, /abstractOnly \? <details/);
  assert.match(page, /\{canOpenEvidence && <button/);
  assert.match(page, /disabled=\{!canAnalyze\}/);
  assert.match(page, /要旨のみのため分析対象にできません/);
  assert.match(page, /onSearch=\{searchExternalPapers\}/);
  assert.match(page, /externalLoading/);
  assert.match(page, /UI_COPY\.externalPaper\.loading/);
  assert.match(page, /externalNotice/);
  assert.match(page, /externalError/);
  assert.match(page, /controller\.signal\.aborted \|\| externalAbortRef\.current !== controller/);
  assert.match(page, /!controller\.signal\.aborted && externalAbortRef\.current === controller/);
  assert.match(page, /role="status"/);
  assert.match(page, /role="alert"/);
});

test("connection-only help is cleared before validation and abstract imports", () => {
  assert.match(search, /const runSearch[\s\S]*?setConnectionError\(false\); setSessionExpired\(false\);[\s\S]*?const context = searchContext/);
  assert.match(search, /const addSelected[\s\S]*?setConnectionError\(false\); setSessionExpired\(false\);/);
});

test("abstract citations are labeled and never open a full-text page", () => {
  assert.match(ask, /function isAbstractCitation/);
  assert.match(ask, /citation\.evidence_scope === "abstract"/);
  assert.match(ask, /外部要旨/);
  assert.match(ask, /外部要旨からの抜粋/);
  assert.match(ask, /!isAbstractCitation\(citation\)/);
});

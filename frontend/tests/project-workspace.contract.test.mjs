import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const switcher = readFileSync(new URL("../components/workspace-switcher.tsx", import.meta.url), "utf8");
const session = readFileSync(new URL("../lib/session/workspace-session.ts", import.meta.url), "utf8");
const auth = readFileSync(new URL("../lib/api/auth.ts", import.meta.url), "utf8");

test("project switcher exposes create, search, rename, and natural Japanese roles", () => {
  assert.match(switcher, /新しい研究プロジェクトを作成/);
  assert.match(switcher, /プロジェクト名で検索/);
  assert.match(switcher, /名前を変更/);
  assert.match(switcher, /owner: "所有者"/);
  assert.match(switcher, /editor: "編集者"/);
  assert.match(switcher, /viewer: "閲覧者"/);
  assert.match(switcher, /研究プロジェクトを切り替える（現在:/);
  assert.doesNotMatch(switcher, /Project navigator|\{ordered\.length\} projects|>Project</);
});

test("active project is restored safely and a newly created project becomes active", () => {
  assert.match(session, /workspaceKey = \(userId: string\)/);
  assert.match(session, /localStorage\.getItem\(key\)/);
  assert.match(session, /sessionStorage\.getItem\(key\)/);
  assert.match(session, /available\.find\(item => item\.id === savedId\)/);
  assert.match(session, /available\.find\(item => item\.id === current\.personal_workspace\.id\)/);
  assert.match(session, /saveWorkspaceId\(me\.user\.id, created\.id\)/);
  assert.match(session, /setActiveWorkspace\(created\)/);
});

test("switching projects cancels requests and remounts every project-local surface", () => {
  assert.match(page, /papersRequestRef\.current!\.cancel\(\)/);
  assert.match(page, /uploadAbortRef\.current\?\.abort\(\)/);
  assert.match(page, /setExternalId\(""\)/);
  assert.match(page, /setDragging\(false\)/);
  assert.match(page, /<LibraryBrowse key=\{session\.activeWorkspace\.id\}/);
  assert.match(page, /<EmbeddingReindexPanel key=\{session\.activeWorkspace\.id\}/);
  assert.match(page, /<IdeaCapture key=\{session\.activeWorkspace\.id\}/);
});

test("API client keeps the selected project at the authorization boundary", () => {
  assert.match(auth, /headers\.set\("X-Workspace-ID", activeWorkspaceId\)/);
});

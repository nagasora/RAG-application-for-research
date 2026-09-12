import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import ts from "typescript";

async function loadSafeUrl() {
  const source = await readFile(new URL("../lib/api/safe-external-url.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports:{} };
  new vm.Script(compiled, { filename:"safe-external-url.js" }).runInNewContext({ URL, exports:module.exports, module });
  return module.exports;
}

test("only absolute HTTPS provider URLs are linkable", async () => {
  const { safeExternalHttpsUrl } = await loadSafeUrl();
  assert.equal(safeExternalHttpsUrl("https://example.org/paper?id=1"), "https://example.org/paper?id=1");
  for (const value of ["http://example.org", "javascript:alert(1)", "//example.org/paper", "/paper", "https://user:pass@example.org", "https:example.org"]) {
    assert.equal(safeExternalHttpsUrl(value), null, value);
  }
});

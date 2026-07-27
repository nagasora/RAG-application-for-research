import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import ts from "typescript";

async function compileCommonJs(url, requireImpl = () => { throw new Error("unexpected import"); }) {
  const source = await readFile(url, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports:{} };
  const context = vm.createContext({
    DOMException, Error, Object, Response,
    exports:module.exports, module, require:requireImpl,
  });
  new vm.Script(compiled, { filename:url.pathname }).runInContext(context);
  return module.exports;
}

async function loadErrorModule() {
  const copy = await compileCommonJs(new URL("../lib/copy.ts", import.meta.url));
  return compileCommonJs(new URL("../lib/api/error.ts", import.meta.url), specifier => {
    if (specifier === "../copy") return copy;
    throw new Error(`unexpected import: ${specifier}`);
  });
}

test("structured FastAPI detail code and message survive JSON response normalization", async () => {
  const api = await loadErrorModule();
  const cases = [
    [422, "source_scope_required", "Select at least one source"],
    [422, "source_scope_too_large", "Select no more than five sources"],
    [409, "research_run_scope_mismatch", "Run scope does not match"],
  ];
  for (const [status, code, message] of cases) {
    const payload = { detail:{ code, message } };
    const response = new Response(JSON.stringify(payload), {
      status,
      headers:{ "content-type":"application/json", "x-request-id":"request-1" },
    });
    const error = await api.errorFromFetchResponse(response, "fallback");
    assert.equal(error.status, status);
    assert.equal(error.code, code);
    assert.equal(error.message, message);
    assert.equal(error.requestId, "request-1");
    assert.deepEqual(error.details, payload);
  }
});

test("structured codes reach public copy while unsafe codes fall back to HTTP copy", async () => {
  const api = await loadErrorModule();
  const expected = {
    source_scope_required:"検索する論文を1〜5件選んでください。",
    source_scope_too_large:"検索する論文は5件まで選べます。",
    research_run_scope_mismatch:"検索する論文が変わりました。もう一度質問してください。",
  };
  for (const [code, message] of Object.entries(expected)) {
    const error = api.apiErrorFromResponse(new Response(null, { status:422 }), { detail:{ code, message:"backend detail" } }, "fallback");
    assert.equal(api.apiErrorMessage(error), message);
  }

  const unsafe = api.apiErrorFromResponse(
    new Response(null, { status:422 }),
    { detail:{ code:"__proto__", message:"Safe backend message" } },
    "fallback",
  );
  assert.equal(unsafe.code, "http_422");
  assert.equal(unsafe.message, "Safe backend message");
  assert.equal(api.apiErrorMessage(unsafe), "入力内容を確認してください。");
});

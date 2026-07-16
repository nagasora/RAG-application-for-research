import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import ts from "typescript";

async function loadSearchStreamModule(fetchImpl = globalThis.fetch) {
  const source = await readFile(new URL("../lib/api/search-stream.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022 },
  }).outputText;
  class ApiError extends Error {
    constructor(message, options = {}) { super(message); this.code = options.code ?? "api_error"; }
  }
  const module = { exports:{} };
  const context = vm.createContext({
    AbortController, DOMException, ReadableStream, TextDecoder, Uint8Array, fetch:fetchImpl,
    exports:module.exports, module,
    require(specifier) {
      if (specifier === "./client") return { API_BASE_URL:"http://localhost" };
      if (specifier === "./auth") return { authenticatedHeaders:() => ({}) };
      if (specifier === "./error") return { ApiError, errorFromFetchResponse:async () => new ApiError("http"), toApiError:error => error };
      throw new Error(`unexpected import: ${specifier}`);
    },
  });
  new vm.Script(compiled, { filename:"search-stream.js" }).runInContext(context);
  return module.exports;
}

function eventStream(frames) {
  const encoder = new TextEncoder();
  return new ReadableStream({ start(controller) { controller.enqueue(encoder.encode(frames)); controller.close(); } });
}

test("all supported Agentic RAG stages are decoded in order", async () => {
  const api = await loadSearchStreamModule();
  const frames = api.SEARCH_STAGES.map(stage => `data: ${JSON.stringify({ type:"stage", value:stage })}\n\n`).join("");
  const received = [];
  for await (const event of api.parseEventStream(eventStream(frames))) received.push(event);
  assert.equal(received.map(event => `${event.type}:${event.value}`).join("|"), Array.from(api.SEARCH_STAGES, stage => `stage:${stage}`).join("|"));
});

test("legacy token/done events remain compatible and a top-level stage is accepted", async () => {
  const api = await loadSearchStreamModule();
  const frames = [
    { type:"stage", stage:"retrieving" }, { type:"token", value:"回答" }, { type:"done" },
  ].map(event => `data: ${JSON.stringify(event)}\n\n`).join("");
  const received = [];
  for await (const event of api.parseEventStream(eventStream(frames))) received.push(event);
  assert.equal(received[0].type, "stage"); assert.equal(received[0].value, "retrieving");
  assert.equal(received[1].type, "token"); assert.equal(received[1].value, "回答");
  assert.equal(received[2].type, "done");
});

const paperCitation = {
  index:1, paper_id:"paper-1", paper_title:"Paper", chunk_id:"chunk-1",
  page:2, section:"Results", excerpt:"Paper evidence", score:0.8,
};

const graphContradiction = {
  ...paperCitation, index:2, source_kind:"graph_edge", evidence_role:"contradicts",
  retrieval_stance:"negative",
  source_version_id:"version-1", source_span_id:"span-1", knowledge_edge_id:"edge-1",
  source_quote:"Contradicting source quote", graph_path:[{ relation:"contradicts" }],
  retrieval_channels:["paper", "graph_contradicts"], retrieval_reason:"graph_contradiction",
  extraction_quality:"high", fusion_score:0.72,
};

test("citation events accept legacy paper and graph contradiction payloads together", async () => {
  const api = await loadSearchStreamModule();
  const frame = `data: ${JSON.stringify({ type:"citations", value:[paperCitation, graphContradiction] })}\n\n`;
  const received = [];
  for await (const event of api.parseEventStream(eventStream(frame))) received.push(event);
  assert.equal(received[0].type, "citations");
  assert.equal(received[0].value.length, 2);
  assert.equal(received[0].value[0].source_kind, undefined);
  assert.equal(received[0].value[1].evidence_role, "contradicts");
  assert.equal(received[0].value[1].retrieval_stance, "negative");
  assert.equal(received[0].value[1].retrieval_channels.join("|"), "paper|graph_contradicts");
});

test("negative retrieval stance remains distinct from a supporting source role", async () => {
  const api = await loadSearchStreamModule();
  const negativeSupport = { ...graphContradiction, evidence_role:"supports", retrieval_stance:"negative" };
  const frame = `data: ${JSON.stringify({ type:"citations", value:[paperCitation, negativeSupport] })}\n\n`;
  const received = [];
  for await (const event of api.parseEventStream(eventStream(frame))) received.push(event);
  assert.equal(received[0].value[1].evidence_role, "supports");
  assert.equal(received[0].value[1].retrieval_stance, "negative");
});

test("citation events reject graph provenance without its immutable locator", async () => {
  const api = await loadSearchStreamModule();
  const malformed = { ...graphContradiction, source_span_id:null };
  const frame = `data: ${JSON.stringify({ type:"citations", value:[malformed] })}\n\n`;
  await assert.rejects(async () => {
    for await (const _event of api.parseEventStream(eventStream(frame))) { /* consume */ }
  }, error => error?.code === "invalid_sse_event");
});

test("citation events reject graph provenance without a retrieval stance", async () => {
  const api = await loadSearchStreamModule();
  const { retrieval_stance: _omitted, ...malformed } = graphContradiction;
  const frame = `data: ${JSON.stringify({ type:"citations", value:[malformed] })}\n\n`;
  await assert.rejects(async () => {
    for await (const _event of api.parseEventStream(eventStream(frame))) { /* consume */ }
  }, error => error?.code === "invalid_sse_event");
});

test("unknown stages are rejected instead of silently corrupting progress", async () => {
  const api = await loadSearchStreamModule();
  await assert.rejects(async () => {
    for await (const _event of api.parseEventStream(eventStream('data: {"type":"stage","value":"unknown"}\n\n'))) { /* consume */ }
  }, error => error?.code === "invalid_sse_event");
});

test("structured RAG metadata is preserved", async () => {
  const api = await loadSearchStreamModule();
  const value = {
    generation_mode:"agentic_rag", model:"gpt-5.4-nano", retrieval_queries:["query"],
    grounded:true, llm_attempted:true, llm_succeeded:true, grounding_status:"verified",
    fallback_reason:null, claims:[{ claim_id:"c1", text:"claim", kind:"paper", citation_ids:[1] }],
    memory_delta:{ hypotheses:["H1"] }, model_calls:2,
  };
  const received = [];
  for await (const event of api.parseEventStream(eventStream(`data: ${JSON.stringify({type:"meta",value})}\n\n`))) received.push(event);
  assert.equal(received[0].value.model_calls, 2);
  assert.equal(received[0].value.claims[0].claim_id, "c1");
});

test("incomplete RAG metadata is rejected", async () => {
  const api = await loadSearchStreamModule();
  const value = {
    generation_mode:"agentic_rag", model:"gpt-5.4-nano", retrieval_queries:[], grounded:true,
    llm_attempted:true, llm_succeeded:true, grounding_status:"verified", fallback_reason:null,
  };
  await assert.rejects(async () => {
    for await (const _event of api.parseEventStream(eventStream(`data: ${JSON.stringify({type:"meta",value})}\n\n`))) { /* consume */ }
  }, error => error?.code === "invalid_sse_event");
});

test("streamSearch rejects a clean EOF when the done event is missing", async () => {
  const frames = 'data: {"type":"token","value":"途中までの回答"}\n\n';
  const api = await loadSearchStreamModule(async () => new Response(eventStream(frames), {
    status:200,
    headers:{ "content-type":"text/event-stream" },
  }));
  await assert.rejects(async () => {
    for await (const _event of api.streamSearch({ query:"質問", paper_ids:[], limit:10 })) { /* consume */ }
  }, error => error?.code === "incomplete_stream");
});

test("streamSearch accepts a stream terminated by done", async () => {
  const frames = [
    { type:"token", value:"回答" }, { type:"done" },
  ].map(event => `data: ${JSON.stringify(event)}\n\n`).join("");
  const api = await loadSearchStreamModule(async () => new Response(eventStream(frames), {
    status:200,
    headers:{ "content-type":"text/event-stream" },
  }));
  const received = [];
  for await (const event of api.streamSearch({ query:"質問", paper_ids:[], limit:10 })) received.push(event.type);
  assert.deepEqual(received, ["token", "done"]);
});

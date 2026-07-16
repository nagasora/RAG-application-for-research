import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const spec = JSON.parse(await readFile(new URL("../openapi/paperpilot.json", import.meta.url), "utf8"));

test("knowledge graph snapshot remains typed and grounded endpoints stay public in OpenAPI", () => {
  const snapshot = spec.paths["/api/graph"]?.get;
  const source = spec.paths["/api/graph/sources"]?.post;
  const sourceImport = spec.paths["/api/graph/sources/import"]?.post;
  const sourceSpans = spec.paths["/api/graph/sources/{source_version_id}/spans"];
  const edge = spec.paths["/api/graph/edges"]?.post;
  const edgeStatus = spec.paths["/api/graph/edges/{edge_id}/status"]?.patch;
  const retrieve = spec.paths["/api/graph/retrieve"]?.post;
  assert.equal(snapshot.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/GraphSnapshot");
  assert.equal(source.requestBody.content["application/json"].schema.$ref, "#/components/schemas/SourceVersionCreate");
  assert.equal(sourceImport.requestBody.content["application/json"].schema.$ref, "#/components/schemas/SourceImportCreate");
  assert.ok(sourceSpans.get);
  assert.equal(sourceSpans.post, undefined);
  assert.equal(spec.components.schemas.SourceSpanCreate, undefined);
  assert.equal(spec.components.schemas.SourceImportCreate.properties.content_hash.type, "string");
  assert.equal(edge.requestBody.content["application/json"].schema.$ref, "#/components/schemas/KnowledgeEdgeCreate");
  assert.equal(spec.components.schemas.KnowledgeEdgeCreate.properties.evidence_span_ids.maxItems, 32);
  assert.equal(spec.components.schemas.KnowledgeEdgeCreate.properties.evidence_links.items.$ref, "#/components/schemas/EvidenceLinkCreate");
  assert.equal(edgeStatus.requestBody.content["application/json"].schema.$ref, "#/components/schemas/KnowledgeEdgeStatusUpdate");
  assert.equal(spec.components.schemas.KnowledgeEdgeCreate.properties.relation.enum.length, 8);
  assert.equal(retrieve.requestBody.content["application/json"].schema.$ref, "#/components/schemas/GraphRetrieveRequest");
});

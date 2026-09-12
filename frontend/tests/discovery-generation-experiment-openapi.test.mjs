import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const spec = JSON.parse(await readFile(new URL("../openapi/paperpilot.json", import.meta.url), "utf8"));

test("federated discovery remains generated, bounded, and session-importable", () => {
  const search = spec.paths["/api/discovery/search"].post;
  const imports = spec.paths["/api/discovery/imports"].post;
  assert.equal(search.requestBody.content["application/json"].schema.$ref, "#/components/schemas/DiscoverySearchRequest");
  assert.equal(search.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/DiscoverySearchResponse");
  assert.equal(imports.requestBody.content["application/json"].schema.$ref, "#/components/schemas/DiscoveryImportRequest");
  const request = spec.components.schemas.DiscoverySearchRequest.properties;
  assert.deepEqual(request.search_mode.enum, ["keyword", "question"]);
  assert.deepEqual(request.providers.items.enum, ["semantic_scholar", "openalex", "cinii", "jstage"]);
  assert.equal(request.providers.maxItems, 4);
  assert.equal(spec.components.schemas.DiscoveryImportRequest.properties.candidate_ids.maxItems, 20);
  assert.ok(spec.components.schemas.DiscoverySearchResponse.properties.search_session_id);
  assert.ok(spec.components.schemas.DiscoverySearchResponse.properties.expires_at);
});

test("generation settings and experiment comparison use concrete generated DTOs", () => {
  const settings = spec.paths["/api/workspace/generation-settings"];
  const comparison = spec.paths["/api/analysis/experiments/compare"].post;
  assert.equal(settings.get.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/WorkspaceGenerationSettings");
  assert.equal(settings.put.requestBody.content["application/json"].schema.$ref, "#/components/schemas/WorkspaceGenerationSettingsUpdate");
  assert.equal(comparison.requestBody.content["application/json"].schema.$ref, "#/components/schemas/ExperimentComparisonRequest");
  assert.equal(comparison.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/ExperimentComparisonResponse");
  assert.equal(spec.components.schemas.ExperimentMatrixRowResponse.properties.cells.items.$ref, "#/components/schemas/ExperimentComparisonCellResponse");
  assert.equal(spec.components.schemas.ExperimentComparisonCellResponse.properties.evidence.items.$ref, "#/components/schemas/ExperimentEvidenceResponse");
  assert.equal(spec.components.schemas.ExperimentProfileResponse.properties.extraction_mode.enum.length, 2);
});

test("paper and citation contracts distinguish abstract-only evidence", () => {
  assert.deepEqual(spec.components.schemas.PaperDetail.properties.content_scope.enum, ["full_text", "abstract_only"]);
  assert.deepEqual(spec.components.schemas.PaperSummary.properties.content_scope.enum, ["full_text", "abstract_only"]);
  assert.deepEqual(spec.components.schemas.Citation.properties.evidence_scope.enum, ["full_text", "abstract"]);
});

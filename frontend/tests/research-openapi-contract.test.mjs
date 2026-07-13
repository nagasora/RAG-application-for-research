import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const spec = JSON.parse(await readFile(new URL("../openapi/paperpilot.json", import.meta.url), "utf8"));

test("research message pagination endpoint remains in OpenAPI", () => {
  const operation = spec.paths["/api/research/conversations/{conversation_id}/messages"]?.get;
  assert.ok(operation);
  assert.equal(operation.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/ResearchMessagePage");
  assert.deepEqual(operation.parameters.map(parameter => parameter.name), ["conversation_id", "limit", "before_ordinal"]);
});

test("research memory pagination endpoint keeps kind and cursor filters", () => {
  const operation = spec.paths["/api/research/conversations/{conversation_id}/memory"]?.get;
  assert.ok(operation);
  assert.equal(operation.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/ResearchMemoryPage");
  assert.deepEqual(operation.parameters.map(parameter => parameter.name), ["conversation_id", "kind", "limit", "before_ordinal"]);
  const kind = operation.parameters.find(parameter => parameter.name === "kind");
  assert.deepEqual(kind.schema.anyOf[0].enum, ["hypothesis", "assumption", "unresolved_question", "planned_test"]);
});
